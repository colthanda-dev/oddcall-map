"""Moderator queue. Login by magic link. Every action lands in audit_log."""
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, g, abort, current_app
from ..db import get_db
from ..auth import login_required, send_magic_link, consume_magic_link, is_moderator_email
from ..reports import (get_report, set_status, update_fields, merge_candidates, refuzz, audit,
                       CATEGORIES, TIERS, TIER_LABEL, REJECT_REASONS, PRECISIONS, parse_date, create_report)
from ..media import decrypt_contact, media_list, encrypt_contact
from ..geo import STATES
from .. import alerts
from flask import session

bp = Blueprint("mod", __name__, url_prefix="/mod")


@bp.get("/login")
def login():
    return render_template("mod_login.html", sent=request.args.get("sent"))


@bp.post("/login")
def login_post():
    email = (request.form.get("email") or "").strip().lower()
    if is_moderator_email(email):
        send_magic_link(email)
    # same response either way; no account enumeration
    return redirect(url_for("mod.login", sent=1))


@bp.get("/magic/<token>")
def magic(token):
    if consume_magic_link(token):
        return redirect(url_for("mod.queue"))
    flash("That link is expired or not valid. Request a new one.")
    return redirect(url_for("mod.login"))


@bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("public.index"))


@bp.get("/")
@login_required
def queue():
    db = get_db()
    status = request.args.get("status", "pending")
    if status not in ("pending", "approved", "rejected", "merged", "redacted", "all"):
        status = "pending"
    where = "" if status == "all" else "WHERE status=?"
    vals = [] if status == "all" else [status]
    rows = db.query(f"SELECT * FROM reports {where} ORDER BY submitted_at ASC LIMIT 200", vals)
    for r in rows:
        r["flags"] = json.loads(r.get("hoax_flags") or "[]")
    counts = {c["status"]: c["n"] for c in db.query("SELECT status, COUNT(*) AS n FROM reports GROUP BY status")}
    takedowns = db.query("SELECT * FROM audit_log WHERE action='takedown_request' ORDER BY at DESC LIMIT 20")
    return render_template("mod_queue.html", rows=rows, status=status, counts=counts, takedowns=takedowns)


@bp.get("/report/<rid>")
@login_required
def report(rid):
    r = get_report(rid)
    if not r:
        abort(404)
    r["flags"] = json.loads(r.get("hoax_flags") or "[]")
    r["media_items"] = media_list(r)
    r["contact_plain"] = decrypt_contact(r.get("witness_contact"))
    cands = merge_candidates(r) if r["status"] == "pending" else []
    log = get_db().query("SELECT * FROM audit_log WHERE report_id=? ORDER BY at DESC", (rid,))
    return render_template("mod_report.html", r=r, cands=cands, log=log, categories=CATEGORIES, tiers=TIERS,
                           tier_label=TIER_LABEL, reasons=REJECT_REASONS, precisions=PRECISIONS, states=STATES)


@bp.post("/report/<rid>/approve")
@login_required
def approve(rid):
    r = get_report(rid)
    if not r:
        abort(404)
    if r.get("public_lat") is None:
        flash("No location on this report. Set lat/lng in Edit, then approve.")
        return redirect(url_for("mod.report", rid=rid))
    tier = request.form.get("tier")
    fields = {}
    if tier in TIERS:
        fields["credibility_tier"] = tier
    set_status(rid, "approved", g.moderator, **fields)
    n = alerts.fire_for_report(get_report(rid))
    flash(f"Approved. {n} alert email(s) sent.")
    return redirect(url_for("mod.queue"))


@bp.post("/report/<rid>/reject")
@login_required
def reject(rid):
    r = get_report(rid)
    if not r:
        abort(404)
    code = request.form.get("reason", "other")
    text = REJECT_REASONS.get(code, REJECT_REASONS["other"])
    set_status(rid, "rejected", g.moderator, detail=code, rejection_reason=code)
    contact = decrypt_contact(r.get("witness_contact"))
    notified = False
    if contact and "@" in contact:
        notified = alerts.notify_witness_rejected(contact, text)
    elif contact and contact.startswith("+"):
        from .hotline import send_sms
        notified = send_sms(contact, f"Oddcall: we did not publish your report. {text} You can text us again with more detail.")
    flash("Rejected." + (" Witness notified." if notified else ""))
    return redirect(url_for("mod.queue"))


@bp.post("/report/<rid>/merge")
@login_required
def merge(rid):
    into = request.form.get("into")
    target = get_report(into) if into else None
    if not target or target["status"] != "approved":
        flash("Pick an approved report to merge into.")
        return redirect(url_for("mod.report", rid=rid))
    set_status(rid, "merged", g.moderator, detail=f"into {into}", merged_into=into)
    flash("Merged. This record stays on file and points at the canonical report.")
    return redirect(url_for("mod.queue"))


@bp.post("/report/<rid>/redact")
@login_required
def redact(rid):
    text = (request.form.get("redacted_narrative") or "").strip()
    update_fields(rid, g.moderator, "redact", redacted_narrative=text or None)
    flash("Redaction saved. The original narrative is untouched.")
    return redirect(url_for("mod.report", rid=rid))


@bp.post("/report/<rid>/fuzz")
@login_required
def fuzz(rid):
    lo = int(request.form.get("min_m", current_app.config["FUZZ_MIN_M"]))
    hi = int(request.form.get("max_m", current_app.config["FUZZ_MAX_M"]))
    hi = max(hi, lo + 50)
    refuzz(rid, g.moderator, lo, hi)
    flash(f"Public pin re-placed {lo} to {hi} m from the true point.")
    return redirect(url_for("mod.report", rid=rid))


@bp.post("/report/<rid>/edit")
@login_required
def edit(rid):
    f = request.form
    fields = {}
    if f.get("category") in CATEGORIES:
        fields["category"] = f["category"]
    fields["subcategory"] = (f.get("subcategory") or "").strip() or None
    for key in ("event_date", "first_told_date", "public_record_date"):
        iso, prec = parse_date(f.get(key))
        fields[key] = iso
        if key == "event_date":
            fields["event_date_precision"] = prec if iso else "unknown"
    fields["county"] = (f.get("county") or "").strip() or None
    fields["state"] = (f.get("state") or "").strip().upper() or None
    fields["place"] = (f.get("place") or "").strip() or None
    if f.get("tier") in TIERS:
        fields["credibility_tier"] = f["tier"]
    fields["credibility_note"] = (f.get("credibility_note") or "").strip() or None
    fields["moderator_notes"] = (f.get("moderator_notes") or "").strip() or None
    fields["broadcast_consent"] = 1 if f.get("broadcast") == "1" else 0
    if f.get("lat") and f.get("lng"):
        from ..geo import fuzz as _fz
        lat, lng = float(f["lat"]), float(f["lng"])
        plat, plng = _fz(lat, lng, seed=rid)
        fields.update(lat=lat, lng=lng, public_lat=plat, public_lng=plng)
    update_fields(rid, g.moderator, "edit", **fields)
    flash("Saved.")
    return redirect(url_for("mod.report", rid=rid))


@bp.post("/report/<rid>/takedown")
@login_required
def takedown(rid):
    set_status(rid, "rejected", g.moderator, detail="takedown honored", rejection_reason="takedown")
    flash("Taken down.")
    return redirect(url_for("mod.queue"))


@bp.get("/field")
@login_required
def field_form():
    return render_template("mod_field.html", categories=CATEGORIES, tiers=TIERS, tier_label=TIER_LABEL, states=STATES)


@bp.post("/field")
@login_required
def field_post():
    """Field interview entry: the moderator's own fieldwork, three dates, full provenance."""
    f = request.form
    data = {
        "category": f.get("category"),
        "subcategory": (f.get("subcategory") or "").strip() or None,
        "narrative": (f.get("narrative") or "").strip(),
        "lat": f.get("lat") or None, "lng": f.get("lng") or None,
        "place": f.get("place"), "county": f.get("county"), "state": f.get("state"),
        "event_date": f.get("event_date"),
        "first_told_date": f.get("first_told_date"),
        "public_record_date": f.get("public_record_date"),
        "source_ref": (f.get("source_ref") or "").strip() or None,
        "source_name": "field",
        "witness_contact": encrypt_contact(f.get("contact")) if f.get("contact") else None,
        "witness_anonymity": f.get("anonymity") or "anonymous",
        "witness_display": (f.get("witness_display") or "").strip() or "Anonymous",
        "broadcast_consent": f.get("broadcast") == "1",
        "credibility_tier": f.get("tier") if f.get("tier") in TIERS else "field_verified",
    }
    if len(data["narrative"]) < 10:
        flash("Narrative required.")
        return redirect(url_for("mod.field_form"))
    rid = create_report(data, "field_interview", status="pending")
    update_fields(rid, g.moderator, "intake", credibility_note=(f.get("credibility_note") or "").strip() or None,
                  moderator_notes=(f.get("moderator_notes") or "").strip() or None)
    flash("Field entry saved to the queue. Approve it from there.")
    return redirect(url_for("mod.report", rid=rid))

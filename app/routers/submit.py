"""Public web report form. No account. Honeypot + per-IP rate limit, no CAPTCHA."""
import hashlib
import datetime as dt
from flask import Blueprint, render_template, request, redirect, url_for, current_app, flash
from ..db import get_db, now
from ..reports import create_report, CATEGORIES, ANONYMITY, audit
from ..media import save_photo, encrypt_contact
from ..geo import STATES

bp = Blueprint("submit", __name__)


def _ip_hash():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    return hashlib.sha256((ip + current_app.config["SECRET_KEY"]).encode()).hexdigest()[:32]


def _rate_ok(key):
    db = get_db()
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
    n = db.one("SELECT COUNT(*) AS n FROM rate_limits WHERE key=? AND at>=?", (key, since))["n"]
    if n >= current_app.config["SUBMIT_PER_HOUR"]:
        return False
    db.execute("INSERT INTO rate_limits (key, at) VALUES (?,?)", (key, now()))
    db.commit()
    return True


def _witness_display(name, mode):
    name = (name or "").strip()
    if mode == "full_name" and name:
        return name
    if mode == "first_name" and name:
        return name.split()[0]
    if mode == "initials" and name:
        return "".join(p[0].upper() + "." for p in name.split() if p)
    return "Anonymous"


@bp.get("/submit")
def form():
    return render_template("submit.html", categories=CATEGORIES, states=STATES, anonymity=ANONYMITY,
                           hotline=current_app.config["HOTLINE_NUMBER"])


@bp.post("/submit")
def post():
    f = request.form
    if f.get("website"):  # honeypot
        return redirect(url_for("submit.thanks"))
    if f.get("age_ok") != "yes":
        flash("You must be 18 or older to submit a report.")
        return redirect(url_for("submit.form"))
    ip_hash = _ip_hash()
    if not _rate_ok("submit:" + ip_hash):
        flash("Too many submissions from this connection. Try again in an hour, or call the hotline.")
        return redirect(url_for("submit.form"))
    narrative = (f.get("narrative") or "").strip()
    if len(narrative) < 20:
        flash("Tell us what happened. A sentence or two at least.")
        return redirect(url_for("submit.form"))
    lat = f.get("lat") or None
    lng = f.get("lng") or None
    if not (lat and lng) and not (f.get("state") and (f.get("county") or f.get("place"))):
        flash("Drop a pin, or give us at least a state and a county or town.")
        return redirect(url_for("submit.form"))

    # stage photos first so EXIF dates feed the hoax check
    import uuid
    rid_tmp = str(uuid.uuid4())
    media, exif_dates = [], []
    for i, fs in enumerate(request.files.getlist("photos")[: current_app.config["MAX_PHOTOS"]]):
        if not fs or not fs.filename:
            continue
        entry, ed = save_photo(fs, rid_tmp, i)
        if entry:
            media.append(entry)
            exif_dates.append(ed)

    contact = (f.get("contact") or "").strip() or None
    data = {
        "category": f.get("category"),
        "subcategory": (f.get("subcategory") or "").strip() or None,
        "narrative": narrative,
        "lat": lat, "lng": lng,
        "place": f.get("place"), "county": f.get("county"), "state": f.get("state"),
        "event_date": f.get("event_date"),
        "first_told_date": f.get("first_told_date"),
        "public_record_date": None,
        "witness_contact": encrypt_contact(contact),
        "witness_anonymity": f.get("anonymity") or "anonymous",
        "witness_display": _witness_display(f.get("name"), f.get("anonymity") or "anonymous"),
        "broadcast_consent": f.get("broadcast") == "yes",
        "media": media,
        "ip_hash": ip_hash,
    }
    rid = create_report(data, "web_form", exif_dates=exif_dates)
    audit(rid, "system", "intake", "web_form")
    return redirect(url_for("submit.thanks"))


@bp.get("/submit/thanks")
def thanks():
    return render_template("thanks.html", hotline=current_app.config["HOTLINE_NUMBER"])

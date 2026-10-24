"""Public pages and JSON API. Nothing here ever reads lat/lng, witness_contact, or moderator_notes."""
import json
from flask import Blueprint, render_template, request, jsonify, abort, current_app, redirect, url_for, flash
from ..db import get_db
from ..reports import public_shape, public_filters, nearby, CATEGORIES, TIERS, TIER_LABEL, SOURCE_TYPES
from ..geo import STATES, CENTROIDS, norm_state, slug
from .. import alerts

bp = Blueprint("public", __name__)


@bp.app_context_processor
def inject():
    cfg = current_app.config
    return {"site_name": cfg["SITE_NAME"], "hotline": cfg["HOTLINE_NUMBER"], "org": cfg["ORG_NAME"],
            "tier_label": TIER_LABEL, "regions": cfg["REGION_STATES"], "state_names": STATES}


@bp.get("/")
def index():
    db = get_db()
    recent = db.query("SELECT * FROM reports WHERE status='approved' AND public_lat IS NOT NULL ORDER BY approved_at DESC LIMIT 10")
    stats = db.one("SELECT COUNT(*) AS n FROM reports WHERE status='approved'")
    return render_template("index.html", recent=[public_shape(r) for r in recent], total=stats["n"],
                           categories=CATEGORIES, tiers=TIERS, sources=SOURCE_TYPES,
                           home_bounds=current_app.config["HOME_BOUNDS"], centroids=CENTROIDS)


@bp.get("/api/reports.geojson")
def reports_geojson():
    where, vals = public_filters(request.args)
    limit = min(int(request.args.get("limit", 5000)), 20000)
    rows = get_db().query(f"SELECT * FROM reports WHERE {where} ORDER BY approved_at DESC LIMIT ?", vals + [limit])
    feats = []
    for r in rows:
        p = public_shape(r)
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lng"], p["lat"]]},
                      "properties": {k: v for k, v in p.items() if k not in ("lat", "lng", "narrative", "media")}
                      | {"snippet": (p["narrative"] or "")[:160]}})
    resp = jsonify({"type": "FeatureCollection", "features": feats})
    resp.headers["Cache-Control"] = "public, max-age=120"
    return resp


@bp.get("/api/report/<rid>")
def api_report(rid):
    r = get_db().one("SELECT * FROM reports WHERE id=? AND status IN ('approved','merged')", (rid,))
    if not r:
        abort(404)
    return jsonify(public_shape(r, full=True))


@bp.get("/api/stats")
def api_stats():
    db = get_db()
    out = {
        "approved": db.one("SELECT COUNT(*) AS n FROM reports WHERE status='approved'")["n"],
        "by_category": db.query("SELECT category, COUNT(*) AS n FROM reports WHERE status='approved' GROUP BY category"),
        "by_state": db.query("SELECT state, COUNT(*) AS n FROM reports WHERE status='approved' AND state IS NOT NULL GROUP BY state ORDER BY n DESC"),
        "by_source": db.query("SELECT source_type, COUNT(*) AS n FROM reports WHERE status='approved' GROUP BY source_type"),
    }
    return jsonify(out)


@bp.get("/report/<rid>")
def report(rid):
    r = get_db().one("SELECT * FROM reports WHERE id=?", (rid,))
    if not r or r["status"] not in ("approved", "merged"):
        abort(404)
    if r["status"] == "merged" and r.get("merged_into"):
        return redirect(url_for("public.report", rid=r["merged_into"]))
    return render_template("report.html", r=public_shape(r, full=True), near=nearby(r))


@bp.get("/state/<st>")
def state(st):
    code = norm_state(st)
    if not code:
        abort(404)
    db = get_db()
    counties = db.query("SELECT county, COUNT(*) AS n FROM reports WHERE status='approved' AND state=? AND county IS NOT NULL GROUP BY county ORDER BY n DESC", (code,))
    cats = db.query("SELECT category, COUNT(*) AS n FROM reports WHERE status='approved' AND state=? GROUP BY category ORDER BY n DESC", (code,))
    decades = db.query("SELECT SUBSTR(event_date,1,3) AS dec, COUNT(*) AS n FROM reports WHERE status='approved' AND state=? AND event_date IS NOT NULL GROUP BY dec ORDER BY dec", (code,))
    recent = db.query("SELECT * FROM reports WHERE status='approved' AND state=? ORDER BY approved_at DESC LIMIT 25", (code,))
    for c in counties:
        c["slug"] = slug(c["county"])
    return render_template("state.html", code=code, name=STATES[code], counties=counties, cats=cats,
                           decades=[{"decade": d["dec"] + "0s", "n": d["n"]} for d in decades],
                           recent=[public_shape(r) for r in recent], total=sum(c["n"] for c in cats),
                           center=CENTROIDS.get(code, current_app.config["HOME_CENTER"]))


@bp.get("/county/<st>/<cslug>")
def county(st, cslug):
    code = norm_state(st)
    if not code:
        abort(404)
    db = get_db()
    rows = db.query("SELECT * FROM reports WHERE status='approved' AND state=? AND county IS NOT NULL ORDER BY event_date DESC", (code,))
    rows = [r for r in rows if slug(r["county"]) == cslug]
    if not rows:
        abort(404)
    name = rows[0]["county"]
    cats = {}
    decades = {}
    for r in rows:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
        if r.get("event_date"):
            d = r["event_date"][:3] + "0s"
            decades[d] = decades.get(d, 0) + 1
    shaped = [public_shape(r) for r in rows]
    lat = sum(p["lat"] for p in shaped if p["lat"]) / max(1, len([p for p in shaped if p["lat"]]))
    lng = sum(p["lng"] for p in shaped if p["lng"]) / max(1, len([p for p in shaped if p["lng"]]))
    return render_template("county.html", code=code, state_name=STATES[code], county=name, reports=shaped,
                           cats=sorted(cats.items(), key=lambda x: -x[1]), decades=sorted(decades.items()),
                           center=[lat or CENTROIDS.get(code, (39, -82))[0], lng or CENTROIDS.get(code, (39, -82))[1]])


@bp.get("/about")
def about():
    return render_template("about.html", tiers=TIERS)


@bp.post("/subscribe")
def subscribe():
    email = (request.form.get("email") or "").strip()
    if "@" not in email:
        flash("Enter an email address.")
        return redirect(request.referrer or url_for("public.index"))
    st = request.form.get("state")
    county = request.form.get("county")
    lat, lng, km = request.form.get("lat"), request.form.get("lng"), request.form.get("radius_km")
    alerts.subscribe(email, state=st, county=county,
                     lat=float(lat) if lat else None, lng=float(lng) if lng else None,
                     radius_km=float(km) if km else None)
    flash("Check your email to confirm the alert.")
    return redirect(request.referrer or url_for("public.index"))


@bp.get("/confirm/<token>")
def confirm_subscription(token):
    ok = alerts.confirm(token)
    flash("Alerts confirmed." if ok else "That confirmation link is not valid.")
    return redirect(url_for("public.index"))


@bp.get("/unsubscribe/<token>")
def unsubscribe(token):
    alerts.unsubscribe(token)
    flash("Alerts stopped.")
    return redirect(url_for("public.index"))


@bp.get("/takedown/<rid>")
def takedown(rid):
    return render_template("takedown.html", rid=rid)


@bp.post("/takedown/<rid>")
def takedown_post(rid):
    from ..reports import audit
    db = get_db()
    r = db.one("SELECT id FROM reports WHERE id=?", (rid,))
    if not r:
        abort(404)
    reason = (request.form.get("reason") or "").strip()[:2000]
    contact = (request.form.get("contact") or "").strip()[:200]
    audit(rid, "public", "takedown_request", json.dumps({"reason": reason, "contact": contact}))
    flash("Received. We act on takedown requests within 48 hours.")
    return redirect(url_for("public.report", rid=rid))


@bp.get("/robots.txt")
def robots():
    return "User-agent: *\nDisallow: /mod\nDisallow: /hooks\nSitemap: " + current_app.config["SITE_URL"].rstrip("/") + "/sitemap.xml\n", 200, {"Content-Type": "text/plain"}


@bp.get("/sitemap.xml")
def sitemap():
    db = get_db()
    base = current_app.config["SITE_URL"].rstrip("/")
    urls = [base + "/", base + "/about", base + "/submit"]
    for s in db.query("SELECT DISTINCT state FROM reports WHERE status='approved' AND state IS NOT NULL"):
        urls.append(f"{base}/state/{s['state']}")
    for c in db.query("SELECT DISTINCT state, county FROM reports WHERE status='approved' AND state IS NOT NULL AND county IS NOT NULL"):
        urls.append(f"{base}/county/{c['state']}/{slug(c['county'])}")
    for r in db.query("SELECT id FROM reports WHERE status='approved'"):
        urls.append(f"{base}/report/{r['id']}")
    body = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + \
        "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>"
    return body, 200, {"Content-Type": "application/xml"}

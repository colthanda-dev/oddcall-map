"""Email alerts: county subscriptions and radius subscriptions, fired on approval.
Witness notifications on reject."""
import secrets
from flask import current_app, url_for
from .db import get_db, new_id, now
from .geo import haversine_km, norm_state, norm_county
from . import mailer


def subscribe(email, state=None, county=None, lat=None, lng=None, radius_km=None):
    db = get_db()
    token = secrets.token_urlsafe(24)
    sid = new_id()
    db.execute(
        "INSERT INTO subscriptions (id, email, state, county, lat, lng, radius_km, confirmed, token, created_at) VALUES (?,?,?,?,?,?,?,0,?,?)",
        (sid, email.strip().lower(), norm_state(state), norm_county(county), lat, lng, radius_km, token, now()))
    db.commit()
    base = current_app.config["SITE_URL"].rstrip("/")
    link = base + url_for("public.confirm_subscription", token=token)
    where = f"{county} County, {state}" if county else (f"{radius_km} km of your point" if radius_km else state or "the map")
    mailer.send(email, "Confirm your Oddcall Map alerts",
                f"Confirm alerts for new reports in {where}:\n\n{link}\n\nTo stop them later: {base}{url_for('public.unsubscribe', token=token)}")
    return sid


def confirm(token):
    db = get_db()
    s = db.one("SELECT * FROM subscriptions WHERE token=?", (token,))
    if not s:
        return False
    db.execute("UPDATE subscriptions SET confirmed=1 WHERE id=?", (s["id"],))
    db.commit()
    return True


def unsubscribe(token):
    db = get_db()
    db.execute("DELETE FROM subscriptions WHERE token=?", (token,))
    db.commit()
    return True


def fire_for_report(report):
    """Called once when a report is approved."""
    db = get_db()
    subs = db.query("SELECT * FROM subscriptions WHERE confirmed=1")
    base = current_app.config["SITE_URL"].rstrip("/")
    link = f"{base}/report/{report['id']}"
    sent = 0
    for s in subs:
        hit = False
        if s["county"] and report.get("county") and s["state"] == report.get("state") and s["county"].lower() == report["county"].lower():
            hit = True
        elif s["state"] and not s["county"] and s["state"] == report.get("state"):
            hit = True
        elif s["radius_km"] and report.get("public_lat") is not None:
            if haversine_km(s["lat"], s["lng"], report["public_lat"], report["public_lng"]) <= s["radius_km"]:
                hit = True
        if hit:
            where = ", ".join(x for x in [report.get("place"), (report.get("county") or "") + (" County" if report.get("county") else ""), report.get("state")] if x)
            mailer.send(s["email"], f"New report on Oddcall Map: {report['category']} near {where or 'the map'}",
                        f"A new {report['category']} report was published near {where}.\n\n{link}\n\nStop alerts: {base}/unsubscribe/{s['token']}")
            sent += 1
    return sent


def notify_witness_rejected(contact, reason_text):
    """contact is decrypted plain text: an email or a phone. Phones are handled by the hotline module."""
    if contact and "@" in contact:
        mailer.send(contact, "About your Oddcall report",
                    f"We did not publish your report. {reason_text}\n\nYou can resubmit with more detail, or reply to this email with questions.")
        return True
    return False

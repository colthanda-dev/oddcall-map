"""Two-way hotline: moderators text witnesses back and call them back, always from the hotline number.

The witness never sees a moderator's own number. Every text and call lands in `messages` and the audit log.
"""
import datetime as dt
from xml.sax.saxutils import escape
import requests
from flask import current_app
from .db import get_db, new_id, now
from .reports import audit

TWILIO = "https://api.twilio.com/2010-04-01/Accounts/{sid}/{what}.json"

# Twilio error codes worth translating for a moderator at midnight.
TWILIO_ERRORS = {
    21610: "they texted STOP, so the carriers block anything we send until they text START",
    21211: "that phone number is not valid",
    21614: "that number can't get texts (probably a landline)",
    30034: "the hotline number isn't registered for business texting (A2P 10DLC), so carriers blocked it",
    21408: "Twilio's geo permissions don't allow that destination",
}


def _creds():
    cfg = current_app.config
    return cfg.get("TWILIO_ACCOUNT_SID"), cfg.get("TWILIO_AUTH_TOKEN")


def _post(what, data):
    """POST to the Twilio REST API. Returns (ok, sid, error_text)."""
    sid, token = _creds()
    if not (sid and token):
        return False, None, "Twilio keys are not set on the server"
    try:
        r = requests.post(TWILIO.format(sid=sid, what=what), auth=(sid, token), data=data, timeout=15)
    except Exception as e:
        return False, None, f"could not reach Twilio ({e.__class__.__name__})"
    try:
        j = r.json()
    except ValueError:
        j = {}
    if r.status_code < 300:
        return True, j.get("sid"), None
    code = j.get("code")
    return False, None, TWILIO_ERRORS.get(code) or j.get("message") or f"Twilio said {r.status_code}"


def log_message(phone_ref, direction, body, actor, report_id=None, twilio_sid=None, status=None):
    db = get_db()
    db.execute("INSERT INTO messages (id, report_id, phone_ref, direction, body, actor, twilio_sid, status, at) VALUES (?,?,?,?,?,?,?,?,?)",
               (new_id(), report_id, phone_ref, direction, body, actor, twilio_sid, status, now()))
    db.commit()


def thread(report_id, phone_ref=None):
    """Everything said with this witness: by report, plus anything tied to their phone hash."""
    db = get_db()
    if phone_ref:
        return db.query("SELECT * FROM messages WHERE report_id=? OR phone_ref=? ORDER BY at ASC", (report_id, phone_ref))
    return db.query("SELECT * FROM messages WHERE report_id=? ORDER BY at ASC", (report_id,))


def open_thread(phone_ref):
    """The report a moderator recently texted this phone about, or None. Inbound texts go there."""
    days = current_app.config["THREAD_DAYS"]
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).replace(microsecond=0).isoformat()
    return get_db().one("SELECT report_id FROM messages WHERE phone_ref=? AND direction IN ('out','call') AND at >= ? ORDER BY at DESC LIMIT 1",
                        (phone_ref, cutoff))


def send_text(report_id, phone_ref, to, body, moderator):
    cfg = current_app.config
    ok, sid, err = _post("Messages", {"From": cfg["HOTLINE_E164"], "To": to, "Body": body})
    log_message(phone_ref, "out", body, moderator, report_id, sid, "sent" if ok else f"failed: {err}")
    audit(report_id, moderator, "text_witness", "sent" if ok else f"failed: {err}")
    return ok, err


def start_callback(report_id, phone_ref, to, moderator):
    """Ring the moderator first. When they pick up, Twilio dials the witness with the hotline as caller ID."""
    cfg = current_app.config
    mod_cell = cfg["MODERATOR_PHONES"].get(moderator.lower())
    if not mod_cell:
        return False, "your cell isn't on file. Add it to MODERATOR_PHONES in the server's .env"
    hotline = cfg["HOTLINE_E164"]
    twiml = ('<?xml version="1.0" encoding="UTF-8"?><Response>'
             '<Say voice="Polly.Matthew">Oddcall callback. Connecting you to the witness now.</Say>'
             f'<Dial callerId="{escape(hotline)}" timeout="30" answerOnBridge="true"><Number>{escape(to)}</Number></Dial>'
             '<Say voice="Polly.Matthew">No answer. Send them a text instead.</Say></Response>')
    ok, sid, err = _post("Calls", {"From": hotline, "To": mod_cell, "Twiml": twiml})
    log_message(phone_ref, "call", None, moderator, report_id, sid, "ringing your phone" if ok else f"failed: {err}")
    audit(report_id, moderator, "call_witness", "started" if ok else f"failed: {err}")
    return ok, err

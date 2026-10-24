"""Strangeness Hotline: Twilio SMS/MMS and voicemail webhooks.

Point the Twilio number at:
  Messaging  ->  POST {SITE_URL}/hooks/twilio/sms
  Voice      ->  POST {SITE_URL}/hooks/twilio/voice
Twilio also calls /hooks/twilio/recording and /hooks/twilio/transcription on its own.

Text flow (four follow-ups, anything skipped stays blank, nothing is guessed):
  first text  = the account itself  ->  Q1 where
  reply       = where               ->  Q2 when
  reply       = when                ->  Q3 what kind
  reply       = kind                ->  Q4 consent
  reply       = consent             ->  report created, pending
  MORE        = append to the last pending report from this phone
"""
import base64
import hashlib
import hmac
import io
import json
import re
import requests
from flask import Blueprint, request, Response, current_app, abort
from ..db import get_db, now
from ..reports import create_report, get_report, audit, update_fields
from ..media import save_photo, save_audio_bytes, encrypt_contact
from ..geo import norm_state, NAME_TO_CODE

bp = Blueprint("hotline", __name__, url_prefix="/hooks/twilio")

Q = {
    1: "Oddcall here. Got it. Where did this happen? Town or crossroads, and the state.",
    2: "When? A date, a month, or a year. Reply SKIP if you're not sure.",
    3: "What kind of thing? Reply a number: 1 light or craft in the sky, 2 creature, 3 ghost or apparition, 4 sound, 5 animal, 6 other.",
    4: "Can we put this on the public map anonymously and read it on Oddcall Radio? Reply YES, MAP ONLY, or NO.",
}
DONE = "Logged. A person reads every report before it goes public, usually within a day. Text MORE to add detail or send photos. Reply STOP any time to opt out."
CAT_MAP = {"1": "uap", "2": "cryptid", "3": "apparition", "4": "sound", "5": "animal", "6": "other"}


def twiml(body):
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>', mimetype="application/xml")


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def validate_twilio():
    cfg = current_app.config
    if not cfg["TWILIO_VALIDATE"]:
        return True
    token = cfg.get("TWILIO_AUTH_TOKEN")
    sig = request.headers.get("X-Twilio-Signature", "")
    if not token or not sig:
        return False
    url = request.url
    if request.headers.get("X-Forwarded-Proto") == "https" and url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    s = url + "".join(k + request.form[k] for k in sorted(request.form.keys()))
    digest = base64.b64encode(hmac.new(token.encode(), s.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(digest, sig)


def _twilio_get(url):
    cfg = current_app.config
    auth = (cfg.get("TWILIO_ACCOUNT_SID") or "", cfg.get("TWILIO_AUTH_TOKEN") or "")
    r = requests.get(url, auth=auth if all(auth) else None, timeout=30)
    r.raise_for_status()
    return r.content


def _session(phone):
    db = get_db()
    s = db.one("SELECT * FROM hotline_sessions WHERE phone=?", (phone,))
    if s:
        s["draft"] = json.loads(s["draft"] or "{}")
    return s


def _save_session(phone, step, draft):
    db = get_db()
    if db.pg:
        db.execute("INSERT INTO hotline_sessions (phone, step, draft, updated_at) VALUES (?,?,?,?) ON CONFLICT (phone) DO UPDATE SET step=EXCLUDED.step, draft=EXCLUDED.draft, updated_at=EXCLUDED.updated_at",
                   (phone, step, json.dumps(draft), now()))
    else:
        db.execute("INSERT OR REPLACE INTO hotline_sessions (phone, step, draft, updated_at) VALUES (?,?,?,?)",
                   (phone, step, json.dumps(draft), now()))
    db.commit()


def _clear_session(phone):
    db = get_db()
    db.execute("DELETE FROM hotline_sessions WHERE phone=?", (phone,))
    db.commit()


def _parse_where(text):
    """'Millfield, Ohio' -> place=Millfield, state=OH. County if the word 'county' appears."""
    text = (text or "").strip()
    state = None
    for name, code in NAME_TO_CODE.items():
        if re.search(r"\b" + re.escape(name) + r"\b", text, re.I):
            state = code
            break
    if not state:
        m = re.search(r"\b([A-Z]{2})\b", text)
        if m:
            state = norm_state(m.group(1))
    county = None
    m = re.search(r"([A-Za-z .']+?)\s+county", text, re.I)
    if m:
        county = m.group(1).strip().strip(",")
    place = re.split(r",", text)[0].strip() if "," in text else (None if county else text)
    return {"place": place, "county": county, "state": state, "where_raw": text}


def _media_from_request(rid):
    media, exif = [], []
    n = int(request.form.get("NumMedia", "0") or 0)
    for i in range(min(n, current_app.config["MAX_PHOTOS"])):
        url = request.form.get(f"MediaUrl{i}")
        ctype = request.form.get(f"MediaContentType{i}", "")
        if not url or not ctype.startswith("image/"):
            continue
        try:
            data = _twilio_get(url)
            entry, ed = save_photo(io.BytesIO(data), rid, i)
            if entry:
                media.append(entry)
                exif.append(ed)
        except Exception:
            continue
    return media, exif


@bp.post("/sms")
def sms():
    if not validate_twilio():
        abort(403)
    phone = request.form.get("From", "")
    body = (request.form.get("Body") or "").strip()
    upper = body.upper()
    s = _session(phone)

    if upper == "MORE":
        db = get_db()
        last = db.one("SELECT id FROM reports WHERE source_ref=? AND status IN ('pending','approved') ORDER BY submitted_at DESC LIMIT 1", (phone_ref(phone),))
        if not last:
            return twiml(f"<Message>{_esc('No open report from this number. Text what you saw to start a new one.')}</Message>")
        _save_session(phone, 9, {"append_to": last["id"]})
        return twiml("<Message>Send the extra detail or photos now, in one message.</Message>")

    if s and s["step"] == 9:
        r = get_report(s["draft"]["append_to"])
        media, _ = _media_from_request(r["id"] + "-" + now()[:10])
        narrative = r["narrative"] + ("\n\n[added by text] " + body if body else "")
        existing = json.loads(r["media"] or "[]")
        update_fields(r["id"], "system", "append", narrative=narrative, media=json.dumps(existing + media))
        _clear_session(phone)
        return twiml("<Message>Added. Thanks.</Message>")

    if not s:
        if not body and int(request.form.get("NumMedia", "0") or 0) == 0:
            return twiml(f"<Message>{_esc('Oddcall Strangeness Hotline. Text what you saw, where, and when, and we will take it from there.')}</Message>")
        draft = {"narrative": body, "sid": request.form.get("MessageSid"), "num_media": int(request.form.get("NumMedia", "0") or 0),
                 "media_urls": [request.form.get(f"MediaUrl{i}") for i in range(int(request.form.get("NumMedia", "0") or 0))],
                 "media_types": [request.form.get(f"MediaContentType{i}") for i in range(int(request.form.get("NumMedia", "0") or 0))]}
        _save_session(phone, 1, draft)
        return twiml(f"<Message>{_esc(Q[1])}</Message>")

    step, draft = s["step"], s["draft"]
    if step == 1:
        draft.update(_parse_where(body))
        _save_session(phone, 2, draft)
        return twiml(f"<Message>{_esc(Q[2])}</Message>")
    if step == 2:
        draft["event_date"] = None if upper in ("SKIP", "") else body
        _save_session(phone, 3, draft)
        return twiml(f"<Message>{_esc(Q[3])}</Message>")
    if step == 3:
        draft["category"] = CAT_MAP.get(body.strip()[:1], "other")
        _save_session(phone, 4, draft)
        return twiml(f"<Message>{_esc(Q[4])}</Message>")
    if step == 4:
        if upper.startswith("NO"):
            _clear_session(phone)
            return twiml("<Message>Understood. Nothing will be published. If you change your mind, text again.</Message>")
        broadcast = upper.startswith("Y")
        rid = _finish_sms(phone, draft, broadcast)
        _clear_session(phone)
        return twiml(f"<Message>{_esc(DONE)}</Message>")
    _clear_session(phone)
    return twiml(f"<Message>{_esc(Q[1])}</Message>")


def phone_ref(phone):
    """Stable, non-reversible reference for grouping a phone's reports without storing the number in the clear."""
    return "phone:" + hashlib.sha256((phone + current_app.config["SECRET_KEY"]).encode()).hexdigest()[:20]


def _finish_sms(phone, draft, broadcast):
    import uuid
    rid_tmp = str(uuid.uuid4())
    media, exif = [], []
    for i, (url, ctype) in enumerate(zip(draft.get("media_urls", []), draft.get("media_types", []))):
        if url and (ctype or "").startswith("image/"):
            try:
                entry, ed = save_photo(io.BytesIO(_twilio_get(url)), rid_tmp, i)
                if entry:
                    media.append(entry)
                    exif.append(ed)
            except Exception:
                continue
    when_raw = draft.get("event_date")
    data = {
        "category": draft.get("category", "other"),
        "narrative": draft.get("narrative") or "[photo only]",
        "place": draft.get("place"), "county": draft.get("county"), "state": draft.get("state"),
        "event_date": _loose_date(when_raw),
        "first_told_date": now()[:10],
        "source_ref": phone_ref(phone),
        "source_name": "hotline",
        "witness_contact": encrypt_contact(phone),
        "witness_anonymity": "anonymous",
        "witness_display": "Anonymous",
        "broadcast_consent": broadcast,
        "media": media,
        "credibility_tier": "witness_direct",
    }
    rid = create_report(data, "hotline_sms", exif_dates=exif)
    note = f"where as texted: {draft.get('where_raw')!r}; when as texted: {when_raw!r}; sid {draft.get('sid')}"
    update_fields(rid, "system", "intake", moderator_notes=note)
    return rid


def _loose_date(text):
    """Turn '10/19/26', 'October 2019', '2019' into something parse_date accepts; else None (moderator sets it)."""
    if not text:
        return None
    t = text.strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", t)
    if m:
        mo, d, y = m.groups()
        y = int(y) + (2000 if len(y) == 2 else 0)
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    m = re.match(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$", t)
    if m:
        return t
    months = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
    m = re.match(r"^([A-Za-z]+)\.?\s+(\d{4})$", t)
    if m and m.group(1).lower()[:3] in [x[:3] for x in months]:
        mo = [x[:3] for x in months].index(m.group(1).lower()[:3]) + 1
        return f"{m.group(2)}-{mo:02d}"
    m = re.match(r"^(\d{4})$", t)
    if m:
        return t
    return None


@bp.post("/voice")
def voice():
    if not validate_twilio():
        abort(403)
    base = current_app.config["SITE_URL"].rstrip("/")
    greeting = (
        "You've reached the Oddcall Strangeness Hotline. Leave what you saw, where, and when, after the tone. "
        "By leaving a message you agree we may publish it anonymously on the map and air it on Oddcall Radio. "
        "Say do not air if you do not want it broadcast. You must be 18 or older. Take your time."
    )
    return twiml(
        f"<Say voice=\"Polly.Matthew\">{_esc(greeting)}</Say>"
        f"<Record maxLength=\"240\" playBeep=\"true\" transcribe=\"true\" "
        f"transcribeCallback=\"{base}/hooks/twilio/transcription\" "
        f"recordingStatusCallback=\"{base}/hooks/twilio/recording\" recordingStatusCallbackEvent=\"completed\"/>"
        f"<Say voice=\"Polly.Matthew\">Got it. Thanks.</Say>"
    )


@bp.post("/recording")
def recording():
    if not validate_twilio():
        abort(403)
    sid = request.form.get("RecordingSid")
    url = request.form.get("RecordingUrl")
    phone = request.form.get("From") or request.form.get("Caller") or ""
    if not sid or not url:
        return ("", 204)
    db = get_db()
    if db.one("SELECT id FROM reports WHERE source_ref=?", ("recording:" + sid,)):
        return ("", 204)
    import uuid
    rid_tmp = str(uuid.uuid4())
    media = []
    try:
        media.append(save_audio_bytes(_twilio_get(url + ".mp3"), rid_tmp))
    except Exception:
        pass
    data = {
        "category": "other",
        "narrative": "[voicemail: transcript pending; listen before approving]",
        "first_told_date": now()[:10],
        "source_ref": "recording:" + sid,
        "source_name": "hotline",
        "witness_contact": encrypt_contact(phone) if phone else None,
        "witness_display": "Anonymous (caller)",
        "broadcast_consent": True,
        "media": media,
        "credibility_tier": "witness_direct",
    }
    rid = create_report(data, "hotline_voice")
    update_fields(rid, "system", "intake", moderator_notes=f"voicemail {sid}; caller group {phone_ref(phone) if phone else 'unknown'}")
    return ("", 204)


@bp.post("/transcription")
def transcription():
    if not validate_twilio():
        abort(403)
    sid = request.form.get("RecordingSid")
    text = (request.form.get("TranscriptionText") or "").strip()
    db = get_db()
    r = db.one("SELECT * FROM reports WHERE source_ref=?", ("recording:" + sid,))
    if not r:
        return ("", 204)
    narrative = text or "[voicemail: no transcript returned; listen to the audio]"
    if re.search(r"do not air|don'?t air", text, re.I):
        update_fields(r["id"], "system", "transcription", narrative=narrative, broadcast_consent=0)
    else:
        update_fields(r["id"], "system", "transcription", narrative=narrative)
    return ("", 204)


def send_sms(to, body):
    """Outbound SMS through Twilio REST, used for witness notifications. Returns True on 2xx."""
    cfg = current_app.config
    sid, token = cfg.get("TWILIO_ACCOUNT_SID"), cfg.get("TWILIO_AUTH_TOKEN")
    if not (sid and token):
        return False
    try:
        r = requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                          auth=(sid, token), data={"From": cfg["HOTLINE_E164"], "To": to, "Body": body}, timeout=15)
        return r.status_code < 300
    except Exception:
        return False

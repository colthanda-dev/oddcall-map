"""Outbound email through Resend. No API key = the email is logged, not sent."""
import logging
import requests
from flask import current_app

log = logging.getLogger("oddcall.mail")


def send(to, subject, text):
    cfg = current_app.config
    key = cfg.get("RESEND_API_KEY")
    if not key:
        log.info("EMAIL (not sent, no RESEND_API_KEY)\nTo: %s\nSubject: %s\n\n%s", to, subject, text)
        current_app.extensions.setdefault("outbox", []).append({"to": to, "subject": subject, "text": text})
        return True
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}"},
            json={"from": cfg["EMAIL_FROM"], "to": [to], "subject": subject, "text": text},
            timeout=15,
        )
        return r.status_code < 300
    except Exception as e:
        log.warning("email failed: %s", e)
        return False

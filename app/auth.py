"""Moderator login by magic link. Two users, no password table to leak."""
import functools
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask import current_app, session, redirect, url_for, request, g
from .db import get_db, now
from . import mailer


def _ser():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="magic-link")


def is_moderator_email(email):
    email = (email or "").strip().lower()
    if not email:
        return False
    if email in current_app.config["MODERATOR_EMAILS"]:
        return True
    return get_db().one("SELECT email FROM moderators WHERE email=?", (email,)) is not None


def send_magic_link(email):
    email = email.strip().lower()
    token = _ser().dumps(email)
    link = current_app.config["SITE_URL"].rstrip("/") + url_for("mod.magic", token=token)
    mailer.send(email, f"{current_app.config['SITE_NAME']} moderator login",
                f"Your login link, good for 15 minutes:\n\n{link}\n\nIf you did not ask for this, ignore it.")
    return link


def consume_magic_link(token):
    try:
        email = _ser().loads(token, max_age=current_app.config["MAGIC_LINK_TTL"])
    except (BadSignature, SignatureExpired):
        return None
    if not is_moderator_email(email):
        return None
    db = get_db()
    if db.pg:
        db.execute("INSERT INTO moderators (email, created_at, last_login) VALUES (?,?,?) ON CONFLICT (email) DO UPDATE SET last_login=EXCLUDED.last_login",
                   (email, now(), now()))
    else:
        db.execute("INSERT OR IGNORE INTO moderators (email, created_at) VALUES (?,?)", (email, now()))
        db.execute("UPDATE moderators SET last_login=? WHERE email=?", (now(), email))
    db.commit()
    session.permanent = True
    session["moderator"] = email
    return email


def current_moderator():
    return session.get("moderator")


def login_required(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if not current_moderator():
            return redirect(url_for("mod.login", next=request.path))
        g.moderator = current_moderator()
        return view(*a, **kw)
    return wrapped

"""Photo handling and witness-contact encryption.

Photos: EXIF is read for the hoax check (capture date vs stated event date), then stripped,
then the image is re-encoded as JPEG and saved under MEDIA_DIR. Caddy serves MEDIA_DIR at /media.
To move to DigitalOcean Spaces later, mount the bucket with s3fs or rclone at MEDIA_DIR; nothing
in the app changes.

Contact info: encrypted with Fernet using a key derived from SECRET_KEY. It lives in the DB only
as ciphertext; the moderator screen decrypts on view.
"""
import base64
import hashlib
import io
import json
from pathlib import Path
from PIL import Image, ImageOps
from flask import current_app
from cryptography.fernet import Fernet, InvalidToken

ALLOWED = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


def _fernet():
    key = hashlib.sha256(current_app.config["SECRET_KEY"].encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_contact(text):
    if not text:
        return None
    return _fernet().encrypt(text.strip().encode()).decode()


def decrypt_contact(token):
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return "[undecryptable: SECRET_KEY changed]"


def exif_capture_date(img):
    try:
        ex = img.getexif()
        v = ex.get(36867) or ex.get(306)  # DateTimeOriginal, DateTime
        if v:
            return str(v)[:10].replace(":", "-")
    except Exception:
        pass
    return None


def save_photo(fileobj, report_id, idx):
    """Returns (media_entry, exif_date) or (None, None) if the file is not an image."""
    raw = fileobj.read()
    if not raw:
        return None, None
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        return None, None
    exif_date = exif_capture_date(img)
    img = ImageOps.exif_transpose(img)  # apply rotation before we drop EXIF
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((2000, 2000))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85, optimize=True)  # no exif kwarg = EXIF gone
    mdir = Path(current_app.config["MEDIA_DIR"])
    mdir.mkdir(parents=True, exist_ok=True)
    name = f"{report_id}-{idx}.jpg"
    (mdir / name).write_bytes(out.getvalue())
    return {"url": f"/media/{name}", "kind": "photo"}, exif_date


def save_audio_bytes(data, report_id, ext="mp3"):
    mdir = Path(current_app.config["MEDIA_DIR"])
    mdir.mkdir(parents=True, exist_ok=True)
    name = f"{report_id}-voicemail.{ext}"
    (mdir / name).write_bytes(data)
    return {"url": f"/media/{name}", "kind": "audio"}


def media_list(report):
    try:
        return json.loads(report.get("media") or "[]")
    except Exception:
        return []

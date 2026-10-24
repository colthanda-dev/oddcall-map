"""Settings. Everything comes from the environment; .env.example lists them."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None, cast=str):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    if cast is bool:
        return v.lower() in ("1", "true", "yes", "on")
    return cast(v)


class Config:
    SECRET_KEY = env("SECRET_KEY", "change-me-before-you-go-live")
    # sqlite:///data/oddcall.db  or  postgresql://user:pass@host:5432/oddcall
    DATABASE_URL = env("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'oddcall.db'}")
    SITE_NAME = env("SITE_NAME", "Oddcall Map")
    SITE_URL = env("SITE_URL", "http://localhost:8000")
    HOTLINE_NUMBER = env("HOTLINE_NUMBER", "(740) 508-5757")
    HOTLINE_E164 = env("HOTLINE_E164", "+17405085757")
    ORG_NAME = env("ORG_NAME", "Appalachian Paranormal LLC")
    MEDIA_DIR = Path(env("MEDIA_DIR", str(BASE_DIR / "media")))
    MAX_PHOTOS = 4
    MAX_UPLOAD_MB = 12
    MAX_CONTENT_LENGTH = 4 * 12 * 1024 * 1024
    # Public pin offset range, metres. Exact coordinates never leave the moderator screen.
    FUZZ_MIN_M = env("FUZZ_MIN_M", 300, int)
    FUZZ_MAX_M = env("FUZZ_MAX_M", 800, int)
    # Duplicate candidates: within this many km and days of an approved report
    DEDUP_KM = env("DEDUP_KM", 2.0, float)
    DEDUP_DAYS = env("DEDUP_DAYS", 7, int)
    # Twilio
    TWILIO_ACCOUNT_SID = env("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN = env("TWILIO_AUTH_TOKEN")
    TWILIO_VALIDATE = env("TWILIO_VALIDATE", True, bool)
    # Email: Resend API. Blank key = print emails to the log instead.
    RESEND_API_KEY = env("RESEND_API_KEY")
    EMAIL_FROM = env("EMAIL_FROM", "Oddcall Map <map@appalachianparanormal.org>")
    # Moderators who may request a magic link. Comma separated.
    MODERATOR_EMAILS = [e.strip().lower() for e in env("MODERATOR_EMAILS", "").split(",") if e.strip()]
    MAGIC_LINK_TTL = 15 * 60
    SESSION_TTL_DAYS = 30
    # Nominatim reverse geocoding (county/state from lat/lng). Be polite: 1 req/sec, cached.
    NOMINATIM_URL = env("NOMINATIM_URL", "https://nominatim.openstreetmap.org/reverse")
    NOMINATIM_USER_AGENT = env("NOMINATIM_USER_AGENT", "oddcall-map/0.1 (map@appalachianparanormal.org)")
    GEOCODE_ENABLED = env("GEOCODE_ENABLED", True, bool)
    # Rate limit for the public form: submissions per IP per hour
    SUBMIT_PER_HOUR = env("SUBMIT_PER_HOUR", 5, int)
    # Launch footprint. Reports outside it still import; the map just centers here.
    HOME_BOUNDS = [[33.0, -97.5], [47.6, -66.9]]  # Appalachia + Midwest + Great Lakes + New England
    HOME_CENTER = [39.0, -82.0]  # Ohio River Valley
    REGION_STATES = {
        "Appalachia": ["AL", "GA", "KY", "MD", "MS", "NY", "NC", "OH", "PA", "SC", "TN", "VA", "WV"],
        "Midwest": ["IL", "IN", "IA", "KS", "MI", "MN", "MO", "NE", "ND", "OH", "SD", "WI"],
        "Great Lakes": ["IL", "IN", "MI", "MN", "NY", "OH", "PA", "WI"],
        "New England": ["CT", "ME", "MA", "NH", "RI", "VT"],
    }


class TestConfig(Config):
    TESTING = True
    DATABASE_URL = "sqlite:///:memory:"
    TWILIO_VALIDATE = False
    GEOCODE_ENABLED = False
    MODERATOR_EMAILS = ["colt@example.test"]
    SECRET_KEY = "test"
    MEDIA_DIR = BASE_DIR / "data" / "test-media"
    SUBMIT_PER_HOUR = 1000

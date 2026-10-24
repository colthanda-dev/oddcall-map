# Oddcall Map

Public, searchable map of anomaly reports across Appalachia, the Midwest, the Great Lakes and New England,
fed by the Strangeness Hotline ((740) 508-5757), a web form, and the strangeness-crawler. A database with a
thin social skin. Nothing goes public without a moderator click. Three dates on every report, never collapsed.

Spec: the "Oddcall Sighting Map: MVP Spec" doc. This repo is Phases 1 to 3 of it.

## What is here

| Piece | Where |
| --- | --- |
| Public map, report pages, state and county pages, GeoJSON API, alerts, takedowns, sitemap | `app/routers/public.py`, `app/templates/` |
| Web report form (no account, honeypot, rate limit, EXIF strip) | `app/routers/submit.py` |
| Hotline: Twilio SMS four-question script, MMS photos, voicemail + transcription, "do not air" | `app/routers/hotline.py` |
| Crawler import (CSV / GeoJSON / the crawler's SQLite) | `app/importers/crawler.py` |
| Moderator queue: magic-link login, approve, reject + notify, merge, redact, re-fuzz, edit, field entry, audit | `app/routers/mod.py`, `app/auth.py` |
| Report lifecycle, hoax flags, dedup candidates, public shaping | `app/reports.py` |
| Fuzzing, haversine, Nominatim reverse geocode (cached, 1 req/s) | `app/geo.py` |
| Schema (SQLite + Postgres, same file) | `app/schema.sql` |
| Tests, end to end on an in-memory DB | `tests/` |

Stack: Python 3.13, Flask, Postgres (SQLite for local), Leaflet + CARTO dark basemap + Census state lines,
Twilio, Resend, Caddy, Docker Compose. No PostGIS yet: radius and dedup queries use a bounding box in SQL
plus haversine in Python, which is fine to about 100k reports. Add PostGIS when that stops being true.

## Run it locally (2 minutes)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY=dev MODERATOR_EMAILS=you@example.com GEOCODE_ENABLED=false TWILIO_VALIDATE=false
flask --app app import-crawler data/sample/seed.csv   # five sample public-record pins
flask --app app run -p 8000
```

Open http://localhost:8000. Log in at /mod/login; with no RESEND_API_KEY the magic link prints to the
terminal. Tests: `python -m pytest -q` or `python tests/run.py` where pytest is missing.

## Deploy on the droplet (30 minutes)

1. DNS: in Squarespace, add a CNAME `map` pointing at the droplet (or an A record to its IP).
2. On the droplet (Ubuntu, Docker installed):
   ```bash
   git clone <this repo> /opt/oddcall-map && cd /opt/oddcall-map
   cp .env.example .env && nano .env     # SECRET_KEY, MODERATOR_EMAILS, Twilio, Resend, POSTGRES_PASSWORD
   docker compose up -d --build
   docker compose exec app flask --app app stats
   ```
   Caddy gets the TLS certificate on its own.
3. Twilio console, the hotline number:
   * Messaging, "a message comes in": webhook, `https://map.appalachianparanormal.org/hooks/twilio/sms`, POST
   * Voice, "a call comes in": webhook, `https://map.appalachianparanormal.org/hooks/twilio/voice`, POST
   The recording and transcription callbacks are set by the app in its TwiML.
4. Resend: verify `appalachianparanormal.org` as a sending domain, paste the key into `.env`.
5. Crawler: edit the two command lines in `scripts/nightly_crawl.sh` to match the crawler's CLI, then
   `crontab -e` and add `17 3 * * * /opt/oddcall-map/scripts/nightly_crawl.sh >> /var/log/oddcall-crawl.log 2>&1`.
   For the first backfill, export the crawler's whole database and run
   `docker compose exec app flask --app app import-crawler /srv/exports/backfill.csv` once.
6. Squarespace hotline page: embed `<iframe src="https://map.appalachianparanormal.org/" style="width:100%;height:80vh;border:0"></iframe>`.
   The app already allows framing from appalachianparanormal.org.

Media: photos and voicemail audio sit in the `media` Docker volume. To move them to Spaces later, mount the
bucket at `/srv/media` with s3fs or rclone; the app does not change.

## Moderation, in one paragraph

Queue at `/mod`. Open a report: the exact pin (red) and the public pin (orange), the verbatim narrative, the
decrypted contact, the flags intake raised, and any approved report within 2 km and 7 days. Approve sets the
tier and fires county and radius alert emails. Reject picks a canned reason; the witness gets one sentence by
email or text if they left contact. Merge keeps the record and points it at the canonical one. Redact writes a
public copy; the original is never edited. Re-fuzz re-places the public pin. The field form is for your own
interviews and goes through the same queue so the audit trail shows two steps. Takedown requests from the
public land at the top of the queue.

## Crawler import, column names

The importer matches loosely: narrative/text/summary/description, date/event_date/occurred,
posted/published/public_record_date, lat/latitude, lon/lng/longitude, state, county, city/place/location,
url/link, source/adapter, category/type/shape. Rows with a URL already on file are skipped; rows with no URL
dedupe on identical text. Crawler rows publish immediately as `unverified_public`, excerpt only, with a link
to the source.

## What is deliberately not here

Accounts, follows, comments, upvotes, video, native apps. Those are Phase 4, gated on 500 approved reports and
1,000 monthly visitors. PostGIS, object storage SDKs and a queue worker are not needed at this size.

## Retention and privacy switches

`flask --app app purge` (run nightly by the cron script) deletes rejected reports after 90 days, stale hotline
sessions after 2 days and rate-limit rows after 1 day. Witness contact is Fernet-encrypted with a key derived
from `SECRET_KEY`: change the key and the contacts are gone, so back it up. Exact coordinates and contact
never appear in any public route; `tests/test_app.py` checks that.

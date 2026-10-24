"""Import strangeness-crawler output: its CSV or GeoJSON exports, or its SQLite database directly.

Column names are matched loosely so the crawler's normalized Report schema lands without edits.
Crawler reports import as status=approved, tier=unverified_public, with public_record_date set
to the source's posted date, since they are already public. A short excerpt is stored for display;
the full text stays at the source link.

CLI:
  flask --app app import-crawler reports.csv
  flask --app app import-crawler reports.geojson
  flask --app app import-crawler crawler.sqlite --table reports
"""
import csv
import json
import sqlite3
from pathlib import Path
from ..db import get_db, now
from ..reports import create_report, audit

ALIASES = {
    "narrative": ["narrative", "text", "body", "description", "summary", "report", "content", "details"],
    "title": ["title", "headline", "subject"],
    "event_date": ["event_date", "date", "occurred", "occurred_at", "sighting_date", "date_occurred", "event"],
    "public_record_date": ["public_record_date", "posted", "posted_at", "published", "published_at", "reported", "date_reported", "fetched_at"],
    "lat": ["lat", "latitude", "y"],
    "lng": ["lng", "lon", "long", "longitude", "x"],
    "state": ["state", "state_code", "region"],
    "county": ["county"],
    "place": ["place", "city", "town", "location", "locality"],
    "source_ref": ["url", "link", "source_url", "permalink", "id_url"],
    "source_name": ["source", "source_name", "adapter", "feed"],
    "category": ["category", "type", "kind", "class"],
    "subcategory": ["subcategory", "shape", "creature", "subtype"],
}

CATEGORY_MAP = {
    "ufo": "uap", "uap": "uap", "uas": "uap", "light": "light", "lights": "light", "orb": "light",
    "bigfoot": "cryptid", "sasquatch": "cryptid", "cryptid": "cryptid", "dogman": "cryptid", "mothman": "cryptid",
    "ghost": "apparition", "haunting": "apparition", "apparition": "apparition", "shadow": "apparition",
    "sound": "sound", "boom": "sound", "hum": "sound",
    "panther": "animal", "cougar": "animal", "animal": "animal",
}
SOURCE_CATEGORY = {"nuforc": "uap", "bfro": "cryptid", "mufon": "uap"}


def _pick(row, key):
    lower = {k.lower().strip(): v for k, v in row.items() if k}
    for a in ALIASES[key]:
        if a in lower and lower[a] not in (None, ""):
            return lower[a]
    return None


def _category(row, source_name):
    raw = (_pick(row, "category") or "").strip().lower()
    for k, v in CATEGORY_MAP.items():
        if k in raw:
            return v
    s = (source_name or "").lower()
    for k, v in SOURCE_CATEGORY.items():
        if k in s:
            return v
    return "other"


def _excerpt(text, n=280):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "..."


def rows_from_file(path, table="reports"):
    path = Path(path)
    suf = path.suffix.lower()
    if suf == ".csv":
        with path.open(newline="", encoding="utf-8", errors="replace") as f:
            yield from csv.DictReader(f)
    elif suf in (".geojson", ".json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        feats = data.get("features") if isinstance(data, dict) else data
        for ft in feats or []:
            if isinstance(ft, dict) and ft.get("type") == "Feature":
                props = dict(ft.get("properties") or {})
                geom = ft.get("geometry") or {}
                if geom.get("type") == "Point" and geom.get("coordinates"):
                    props.setdefault("lng", geom["coordinates"][0])
                    props.setdefault("lat", geom["coordinates"][1])
                yield props
            elif isinstance(ft, dict):
                yield ft
    elif suf in (".db", ".sqlite", ".sqlite3"):
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        for r in con.execute(f"SELECT * FROM {table}"):
            yield dict(r)
        con.close()
    else:
        raise ValueError(f"unsupported file type: {suf}")


def import_rows(rows, default_source=None, actor="system"):
    db = get_db()
    seen = {"inserted": 0, "skipped": 0}
    latest = {}
    for row in rows:
        source_name = (_pick(row, "source_name") or default_source or "crawler").strip().lower()
        ref = _pick(row, "source_ref")
        narrative = _pick(row, "narrative") or _pick(row, "title") or ""
        if not narrative.strip():
            seen["skipped"] += 1
            continue
        if ref and db.one("SELECT id FROM reports WHERE source_ref=?", (ref,)):
            seen["skipped"] += 1
            continue
        if not ref and db.one("SELECT id FROM reports WHERE source_type='crawler' AND narrative=?", (narrative,)):
            seen["skipped"] += 1
            continue
        lat, lng = _pick(row, "lat"), _pick(row, "lng")
        try:
            lat = float(lat) if lat not in (None, "") else None
            lng = float(lng) if lng not in (None, "") else None
        except ValueError:
            lat = lng = None
        posted = _pick(row, "public_record_date")
        data = {
            "category": _category(row, source_name),
            "subcategory": _pick(row, "subcategory"),
            "narrative": narrative,
            "source_excerpt": _excerpt(narrative),
            "lat": lat, "lng": lng,
            "state": _pick(row, "state"), "county": _pick(row, "county"), "place": _pick(row, "place"),
            "event_date": _pick(row, "event_date"),
            "public_record_date": posted,
            "source_ref": ref,
            "source_name": source_name,
            "witness_anonymity": "anonymous",
            "witness_display": f"Public record ({source_name})",
            "credibility_tier": "unverified_public",
        }
        rid = create_report(data, "crawler", status="approved")
        seen["inserted"] += 1
        if posted and (source_name not in latest or str(posted) > latest[source_name]):
            latest[source_name] = str(posted)
    for name, wm in latest.items():
        if db.pg:
            db.execute("INSERT INTO sources (name, kind, watermark, last_run, last_count) VALUES (?,?,?,?,?) ON CONFLICT (name) DO UPDATE SET watermark=EXCLUDED.watermark, last_run=EXCLUDED.last_run, last_count=EXCLUDED.last_count",
                       (name, "crawler", wm, now(), seen["inserted"]))
        else:
            db.execute("INSERT OR REPLACE INTO sources (name, kind, watermark, last_run, last_count) VALUES (?,?,?,?,?)",
                       (name, "crawler", wm, now(), seen["inserted"]))
    db.commit()
    audit(None, actor, "import", json.dumps(seen))
    return seen

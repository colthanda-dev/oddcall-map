"""Report lifecycle: create, flag, dedup, approve, reject, merge, redact, publish-shape."""
import json
import re
import datetime as dt
from flask import current_app
from .db import get_db, new_id, now
from .geo import fuzz, bbox, haversine_km, reverse_geocode, norm_state, norm_county, in_water_or_interstate_guess, slug
from .media import media_list

CATEGORIES = ["uap", "cryptid", "apparition", "sound", "light", "animal", "other"]
TIERS = ["field_verified", "witness_direct", "witness_anonymous", "unverified_public", "secondhand", "disputed"]
TIER_LABEL = {
    "field_verified": "Field verified",
    "witness_direct": "Witness, direct",
    "witness_anonymous": "Witness, anonymous",
    "unverified_public": "Public record, unverified",
    "secondhand": "Secondhand",
    "disputed": "Disputed",
}
PRECISIONS = ["exact", "month", "year", "unknown"]
ANONYMITY = ["anonymous", "initials", "first_name", "full_name"]
SOURCE_TYPES = ["hotline_sms", "hotline_voice", "web_form", "crawler", "field_interview"]
REJECT_REASONS = {
    "duplicate": "This matched a report already on the map.",
    "promo": "The report read as promotional content.",
    "hoax": "The report matched a known hoax pattern.",
    "insufficient": "There was not enough detail to place or date it.",
    "names_private": "It named a private individual or address we do not publish.",
    "minor": "It appears to involve or come from a minor.",
    "other": "It did not fit the map.",
}

COPYPASTA = [
    r"it was a late night", r"ben drowned", r"i was sitting in my room",
]


def parse_date(s):
    """Accepts YYYY, YYYY-MM, YYYY-MM-DD. Returns (iso, precision) or (None, 'unknown')."""
    if not s:
        return None, "unknown"
    s = str(s).strip()
    m = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", s)
    if not m:
        return None, "unknown"
    y, mo, d = m.group(1), m.group(2), m.group(3)
    if d:
        try:
            return dt.date(int(y), int(mo), int(d)).isoformat(), "exact"
        except ValueError:
            return None, "unknown"
    if mo:
        return f"{y}-{int(mo):02d}", "month"
    return y, "year"


def hoax_flags(db, narrative, lat, lng, event_date, exif_dates):
    flags = []
    text = (narrative or "").strip()
    if len(text) >= 80:
        dup = db.one("SELECT id FROM reports WHERE narrative = ? LIMIT 1", (text,))
        if dup:
            flags.append(f"identical_text:{dup['id']}")
    if lat is not None and lng is not None and in_water_or_interstate_guess(lat, lng):
        flags.append("coords_suspicious")
    for pat in COPYPASTA:
        if re.search(pat, text, re.I):
            flags.append("copypasta")
            break
    if event_date and exif_dates:
        for ed in exif_dates:
            if ed and ed[:4].isdigit() and ed[:len(event_date)] != event_date[:len(ed)] and ed[:4] != event_date[:4]:
                flags.append(f"exif_date_mismatch:{ed}")
    return flags


def create_report(data, source_type, exif_dates=None, status="pending"):
    """data: category, subcategory, narrative, lat, lng, place, county, state, event_date,
    first_told_date, public_record_date, source_ref, source_name, source_excerpt,
    witness_contact (already encrypted), witness_anonymity, witness_display, broadcast_consent,
    media (list), credibility_tier, ip_hash."""
    db = get_db()
    rid = new_id()
    lat = data.get("lat")
    lng = data.get("lng")
    lat = float(lat) if lat not in (None, "") else None
    lng = float(lng) if lng not in (None, "") else None
    event_date, precision = parse_date(data.get("event_date"))
    told, _ = parse_date(data.get("first_told_date"))
    pub, _ = parse_date(data.get("public_record_date"))
    state = norm_state(data.get("state"))
    county = norm_county(data.get("county"))
    place = (data.get("place") or "").strip() or None
    if lat is not None and lng is not None:
        if not (state and county):
            geo = reverse_geocode(lat, lng)
            if geo:
                state = state or geo.get("state")
                county = county or geo.get("county")
                place = place or geo.get("place")
        plat, plng = fuzz(lat, lng, seed=rid)
    else:
        plat = plng = None
    narrative = (data.get("narrative") or "").strip()
    flags = hoax_flags(db, narrative, lat, lng, event_date, exif_dates or [])
    cat = data.get("category") if data.get("category") in CATEGORIES else "other"
    tier = data.get("credibility_tier") if data.get("credibility_tier") in TIERS else (
        "witness_direct" if data.get("witness_contact") else "witness_anonymous")
    db.execute(
        """INSERT INTO reports (id, category, subcategory, event_date, event_date_precision, first_told_date,
           public_record_date, submitted_at, lat, lng, public_lat, public_lng, county, state, place, narrative,
           source_type, source_ref, source_name, source_excerpt, witness_contact, witness_anonymity, witness_display,
           broadcast_consent, media, credibility_tier, status, hoax_flags, ip_hash, approved_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (rid, cat, (data.get("subcategory") or None), event_date, precision, told, pub, now(), lat, lng, plat, plng,
         county, state, place, narrative, source_type, data.get("source_ref"), data.get("source_name"),
         data.get("source_excerpt"), data.get("witness_contact"),
         data.get("witness_anonymity") if data.get("witness_anonymity") in ANONYMITY else "anonymous",
         data.get("witness_display"), 1 if data.get("broadcast_consent") else 0,
         json.dumps(data.get("media") or []), tier, status, json.dumps(flags), data.get("ip_hash"),
         now() if status == "approved" else None),
    )
    db.commit()
    return rid


def get_report(rid):
    return get_db().one("SELECT * FROM reports WHERE id=?", (rid,))


def audit(report_id, actor, action, detail=None):
    db = get_db()
    db.execute("INSERT INTO audit_log (id, report_id, actor, action, detail, at) VALUES (?,?,?,?,?,?)",
               (new_id(), report_id, actor, action, detail, now()))
    db.commit()


def merge_candidates(report):
    """Approved reports within DEDUP_KM and DEDUP_DAYS of this one."""
    if report.get("lat") is None:
        return []
    cfg = current_app.config
    db = get_db()
    lo_lat, hi_lat, lo_lng, hi_lng = bbox(report["lat"], report["lng"], cfg["DEDUP_KM"])
    rows = db.query(
        "SELECT * FROM reports WHERE status='approved' AND id<>? AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",
        (report["id"], lo_lat, hi_lat, lo_lng, hi_lng))
    out = []
    for r in rows:
        if haversine_km(report["lat"], report["lng"], r["lat"], r["lng"]) > cfg["DEDUP_KM"]:
            continue
        if report.get("event_date") and r.get("event_date") and len(report["event_date"]) == 10 and len(r["event_date"]) == 10:
            d1 = dt.date.fromisoformat(report["event_date"])
            d2 = dt.date.fromisoformat(r["event_date"])
            if abs((d1 - d2).days) > cfg["DEDUP_DAYS"]:
                continue
        out.append(r)
    return out


def set_status(rid, status, actor, detail=None, **fields):
    db = get_db()
    sets = ["status=?"]
    vals = [status]
    if status == "approved":
        sets.append("approved_at=?")
        vals.append(now())
    for k, v in fields.items():
        sets.append(f"{k}=?")
        vals.append(v)
    vals.append(rid)
    db.execute(f"UPDATE reports SET {', '.join(sets)} WHERE id=?", vals)
    db.commit()
    audit(rid, actor, status, detail)


def update_fields(rid, actor, action, **fields):
    db = get_db()
    sets = ", ".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE reports SET {sets} WHERE id=?", list(fields.values()) + [rid])
    db.commit()
    audit(rid, actor, action, json.dumps({k: (v if not isinstance(v, str) or len(v) < 200 else v[:200]) for k, v in fields.items()}))


def refuzz(rid, actor, min_m, max_m):
    r = get_report(rid)
    if r is None or r["lat"] is None:
        return
    plat, plng = fuzz(r["lat"], r["lng"], min_m, max_m, seed=rid + str(min_m))
    update_fields(rid, actor, "fuzz", public_lat=plat, public_lng=plng)


def public_shape(r, full=False):
    """What the public may see. Never the exact coordinates or contact."""
    narrative = r.get("redacted_narrative") or r.get("narrative")
    if r["source_type"] == "crawler" and not full:
        narrative = r.get("source_excerpt") or (narrative[:280] + ("..." if len(narrative) > 280 else ""))
    elif r["source_type"] == "crawler":
        narrative = r.get("source_excerpt") or narrative[:600]
    out = {
        "id": r["id"],
        "category": r["category"],
        "subcategory": r.get("subcategory"),
        "event_date": r.get("event_date"),
        "event_date_precision": r.get("event_date_precision"),
        "first_told_date": r.get("first_told_date"),
        "public_record_date": r.get("public_record_date"),
        "county": r.get("county"),
        "state": r.get("state"),
        "place": r.get("place"),
        "lat": r.get("public_lat"),
        "lng": r.get("public_lng"),
        "narrative": narrative,
        "source_type": r["source_type"],
        "source_name": r.get("source_name"),
        "source_ref": r.get("source_ref") if r["source_type"] == "crawler" else None,
        "witness": r.get("witness_display") or "Anonymous",
        "credibility_tier": r["credibility_tier"],
        "credibility_label": TIER_LABEL.get(r["credibility_tier"], r["credibility_tier"]),
        "credibility_note": r.get("credibility_note"),
        "approved_at": r.get("approved_at"),
        "media": [m for m in media_list(r) if m.get("kind") == "photo"],
        "county_slug": slug(r.get("county")),
        "url": f"/report/{r['id']}",
    }
    if r.get("merged_into"):
        out["merged_into"] = r["merged_into"]
    return out


def public_filters(args):
    """Build WHERE for the public endpoints from query args."""
    where = ["status='approved'", "public_lat IS NOT NULL"]
    vals = []
    if args.get("category") in CATEGORIES:
        where.append("category=?")
        vals.append(args["category"])
    if args.get("tier") in TIERS:
        where.append("credibility_tier=?")
        vals.append(args["tier"])
    if args.get("source") in SOURCE_TYPES:
        where.append("source_type=?")
        vals.append(args["source"])
    st = norm_state(args.get("state"))
    if st:
        where.append("state=?")
        vals.append(st)
    if args.get("county"):
        where.append("LOWER(county)=?")
        vals.append(norm_county(args["county"]).lower())
    if args.get("from"):
        where.append("event_date >= ?")
        vals.append(args["from"][:10])
    if args.get("to"):
        where.append("event_date <= ?")
        vals.append(args["to"][:10] + ("" if len(args["to"]) >= 10 else "-99"))
    return " AND ".join(where), vals


def nearby(r, km=25, limit=10):
    if r.get("public_lat") is None:
        return []
    db = get_db()
    lo_lat, hi_lat, lo_lng, hi_lng = bbox(r["public_lat"], r["public_lng"], km)
    rows = db.query(
        "SELECT * FROM reports WHERE status='approved' AND id<>? AND public_lat BETWEEN ? AND ? AND public_lng BETWEEN ? AND ? ORDER BY approved_at DESC LIMIT ?",
        (r["id"], lo_lat, hi_lat, lo_lng, hi_lng, limit * 3))
    rows.sort(key=lambda x: haversine_km(r["public_lat"], r["public_lng"], x["public_lat"], x["public_lng"]))
    return [public_shape(x) for x in rows[:limit]]

"""Geography: public-pin fuzzing, distance, reverse geocoding, state tables."""
import math
import random
import re
import time
import requests
from flask import current_app
from .db import get_db, now

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
NAME_TO_CODE = {v.lower(): k for k, v in STATES.items()}

# Rough centroids for state pages and the region picker
CENTROIDS = {
    "AL": (32.8, -86.8), "CT": (41.6, -72.7), "GA": (32.7, -83.4), "IL": (40.0, -89.2), "IN": (39.9, -86.3),
    "IA": (42.1, -93.5), "KS": (38.5, -98.4), "KY": (37.5, -85.3), "ME": (45.4, -69.2), "MD": (39.0, -76.8),
    "MA": (42.3, -71.8), "MI": (44.3, -85.4), "MN": (46.3, -94.3), "MS": (32.7, -89.7), "MO": (38.4, -92.5),
    "NE": (41.5, -99.8), "NH": (43.7, -71.6), "NY": (42.9, -75.5), "NC": (35.6, -79.4), "ND": (47.4, -100.5),
    "OH": (40.3, -82.8), "PA": (40.9, -77.8), "RI": (41.7, -71.5), "SC": (33.9, -80.9), "SD": (44.4, -100.2),
    "TN": (35.9, -86.4), "VT": (44.1, -72.7), "VA": (37.5, -78.9), "WV": (38.6, -80.6), "WI": (44.6, -90.0),
}

EARTH_KM = 6371.0088


def norm_state(s):
    if not s:
        return None
    s = s.strip()
    if len(s) == 2 and s.upper() in STATES:
        return s.upper()
    return NAME_TO_CODE.get(s.lower())


def norm_county(c):
    if not c:
        return None
    c = re.sub(r"\s+(county|parish|borough)\s*$", "", c.strip(), flags=re.I)
    return c.strip().title() if c.strip() else None


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def haversine_km(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


def bbox(lat, lng, km):
    dlat = km / 111.0
    dlng = km / (111.0 * max(math.cos(math.radians(lat)), 0.05))
    return lat - dlat, lat + dlat, lng - dlng, lng + dlng


def fuzz(lat, lng, min_m=None, max_m=None, seed=None):
    """Offset a point by a random bearing and distance. Deterministic per report when seeded,
    so a republished pin does not drift and give away the average."""
    cfg = current_app.config
    min_m = cfg["FUZZ_MIN_M"] if min_m is None else min_m
    max_m = cfg["FUZZ_MAX_M"] if max_m is None else max_m
    rng = random.Random(seed) if seed is not None else random
    d = rng.uniform(min_m, max_m) / 1000.0
    brg = rng.uniform(0, 2 * math.pi)
    p1, l1 = math.radians(lat), math.radians(lng)
    ang = d / EARTH_KM
    p2 = math.asin(math.sin(p1) * math.cos(ang) + math.cos(p1) * math.sin(ang) * math.cos(brg))
    l2 = l1 + math.atan2(math.sin(brg) * math.sin(ang) * math.cos(p1), math.cos(ang) - math.sin(p1) * math.sin(p2))
    return round(math.degrees(p2), 5), round(math.degrees(l2), 5)


_last_geocode = [0.0]


def reverse_geocode(lat, lng):
    """County, state, place from Nominatim, cached and rate-limited to 1 req/sec."""
    if not current_app.config["GEOCODE_ENABLED"]:
        return None
    db = get_db()
    key = f"{round(lat, 3)},{round(lng, 3)}"
    hit = db.one("SELECT county, state, place FROM geocode_cache WHERE key=?", (key,))
    if hit:
        return hit
    wait = 1.05 - (time.time() - _last_geocode[0])
    if wait > 0:
        time.sleep(wait)
    try:
        r = requests.get(
            current_app.config["NOMINATIM_URL"],
            params={"lat": lat, "lon": lng, "format": "jsonv2", "zoom": 10, "addressdetails": 1},
            headers={"User-Agent": current_app.config["NOMINATIM_USER_AGENT"]},
            timeout=10,
        )
        _last_geocode[0] = time.time()
        if r.status_code != 200:
            return None
        a = r.json().get("address", {})
    except Exception:
        return None
    out = {
        "county": norm_county(a.get("county")),
        "state": norm_state(a.get("state")),
        "place": a.get("town") or a.get("village") or a.get("city") or a.get("hamlet"),
    }
    db.execute("INSERT OR REPLACE INTO geocode_cache (key, county, state, place, at) VALUES (?,?,?,?,?)"
               if not db.pg else
               "INSERT INTO geocode_cache (key, county, state, place, at) VALUES (?,?,?,?,?) ON CONFLICT (key) DO UPDATE SET county=EXCLUDED.county, state=EXCLUDED.state, place=EXCLUDED.place, at=EXCLUDED.at",
               (key, out["county"], out["state"], out["place"], now()))
    db.commit()
    return out


def in_water_or_interstate_guess(lat, lng):
    """Cheap hoax flag: coordinates with no decimals, or on the exact (0,0) / null island."""
    if lat == 0 and lng == 0:
        return True
    if float(lat).is_integer() and float(lng).is_integer():
        return True
    return False

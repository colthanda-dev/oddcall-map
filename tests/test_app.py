"""End-to-end smoke tests on the in-memory SQLite DB. Run: python -m pytest -q"""
import io
import json
import re
try:
    import pytest
except ImportError:  # tests/run.py supplies fixtures by hand
    class pytest:
        @staticmethod
        def fixture(*a, **k):
            return lambda f: f
from PIL import Image
from app import create_app
from app.config import TestConfig
from app import db as _db


@pytest.fixture()
def app(tmp_path):
    _db.reset_memory_db()
    TestConfig.MEDIA_DIR = tmp_path / "media"
    app = create_app(TestConfig)
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, app):
    with app.app_context():
        pass
    r = client.post("/mod/login", data={"email": "colt@example.test"})
    assert r.status_code == 302
    link = app.extensions["outbox"][-1]["text"]
    token = re.search(r"/mod/magic/(\S+)", link).group(1)
    r = client.get(f"/mod/magic/{token}")
    assert r.status_code == 302 and r.headers["Location"].endswith("/mod/")


def jpeg_bytes():
    img = Image.new("RGB", (40, 40), (200, 100, 20))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def test_pages_render_empty(client):
    for path in ("/", "/about", "/submit", "/api/reports.geojson", "/api/stats", "/robots.txt", "/sitemap.xml"):
        assert client.get(path).status_code == 200, path
    assert client.get("/report/nope").status_code == 404
    assert client.get("/mod/").status_code == 302  # login required


def test_web_form_to_publish(client, app):
    data = {
        "category": "cryptid", "subcategory": "dogman",
        "narrative": "Something on two legs crossed the road by the tipple and stopped to look back at the truck.",
        "lat": "39.4512", "lng": "-82.1023", "place": "Millfield", "county": "Athens", "state": "OH",
        "event_date": "2019-07-14", "first_told_date": "2019-07-15",
        "name": "Colt Handa", "anonymity": "initials", "contact": "colt@example.test",
        "broadcast": "yes", "age_ok": "yes",
        "photos": (io.BytesIO(jpeg_bytes()), "p.jpg"),
    }
    r = client.post("/submit", data=data, content_type="multipart/form-data")
    assert r.status_code == 302 and r.headers["Location"].endswith("/submit/thanks")

    # not public yet
    gj = client.get("/api/reports.geojson").get_json()
    assert gj["features"] == []

    login(client, app)
    q = client.get("/mod/").get_data(as_text=True)
    rid = re.search(r"/mod/report/([0-9a-f-]{36})", q).group(1)
    page = client.get(f"/mod/report/{rid}").get_data(as_text=True)
    assert "C.H." in page and "colt@example.test" in page and "39.4512" in page

    r = client.post(f"/mod/report/{rid}/approve", data={"tier": "witness_direct"})
    assert r.status_code == 302

    gj = client.get("/api/reports.geojson").get_json()
    assert len(gj["features"]) == 1
    p = gj["features"][0]["properties"]
    assert p["county"] == "Athens" and p["state"] == "OH" and p["credibility_tier"] == "witness_direct"
    lng, lat = gj["features"][0]["geometry"]["coordinates"]
    # fuzzed: not the exact point, but within ~1 km
    assert (lat, lng) != (39.4512, -82.1023)
    from app.geo import haversine_km
    assert 0.25 <= haversine_km(lat, lng, 39.4512, -82.1023) <= 0.85

    page = client.get(f"/report/{rid}").get_data(as_text=True)
    assert "C.H." in page and "Jul 14, 2019" in page and "colt@example.test" not in page and "39.4512" not in page
    assert client.get("/county/OH/athens").status_code == 200
    assert client.get("/state/OH").status_code == 200
    assert client.get("/state/ohio").status_code == 200
    assert "/county/OH/athens" in client.get("/sitemap.xml").get_data(as_text=True)


def test_hotline_sms_flow(client, app):
    def sms(body, phone="+17405550123"):
        return client.post("/hooks/twilio/sms", data={"From": phone, "Body": body, "NumMedia": "0", "MessageSid": "SM1"}).get_data(as_text=True)

    assert "Where did this happen" in sms("Orange light over the strip mine hovering for ten minutes then gone")
    assert "When?" in sms("Millfield, Athens County, Ohio")
    assert "What kind" in sms("10/19/26")
    assert "public map" in sms("1")
    assert "Logged" in sms("YES")

    login(client, app)
    q = client.get("/mod/").get_data(as_text=True)
    rid = re.search(r"/mod/report/([0-9a-f-]{36})", q).group(1)
    page = client.get(f"/mod/report/{rid}").get_data(as_text=True)
    assert "hotline sms" in page and "Athens" in page and "2026-10-19" in page and "+17405550123" in page
    # no location pin yet: approve must refuse until lat/lng set
    r = client.post(f"/mod/report/{rid}/approve", data={}, follow_redirects=True)
    assert "No location" in r.get_data(as_text=True)
    client.post(f"/mod/report/{rid}/edit", data={"category": "uap", "event_date": "2026-10-19", "lat": "39.43", "lng": "-82.11", "county": "Athens", "state": "OH", "tier": "witness_direct"})
    client.post(f"/mod/report/{rid}/approve", data={})
    assert len(client.get("/api/reports.geojson").get_json()["features"]) == 1
    # MORE appends
    assert "Send the extra detail" in sms("MORE")


def test_voicemail_and_do_not_air(client, app):
    assert "<Record" in client.post("/hooks/twilio/voice", data={"From": "+17405550199"}).get_data(as_text=True)
    client.post("/hooks/twilio/recording", data={"RecordingSid": "RE1", "RecordingUrl": "http://127.0.0.1:9/none", "From": "+17405550199"})
    client.post("/hooks/twilio/transcription", data={"RecordingSid": "RE1", "TranscriptionText": "Saw a panther on 33. Please do not air this."})
    login(client, app)
    q = client.get("/mod/").get_data(as_text=True)
    rid = re.search(r"/mod/report/([0-9a-f-]{36})", q).group(1)
    page = client.get(f"/mod/report/{rid}").get_data(as_text=True)
    assert "panther" in page and "Broadcast: no" in page


def test_crawler_import_and_dedup(client, app, tmp_path):
    csvp = tmp_path / "r.csv"
    csvp.write_text(
        "source,url,date,posted,lat,lon,state,county,city,summary,shape\n"
        "nuforc,https://nuforc.org/s/1,2021-08-03,2021-08-10,40.1,-82.9,OH,Delaware,Lewis Center,Three orange lights in a line over the fields.,triangle\n"
        "bfro,https://bfro.net/r/2,1998,2001-05-01,38.9,-81.1,WV,Jackson,,Wood knocks and a smell like a wet dog near the creek. Long detailed report follows.,\n"
        "nuforc,https://nuforc.org/s/1,2021-08-03,2021-08-10,40.1,-82.9,OH,Delaware,Lewis Center,Three orange lights in a line over the fields.,triangle\n"
    )
    from app.importers.crawler import rows_from_file, import_rows
    with app.app_context():
        res = import_rows(rows_from_file(csvp))
    assert res == {"inserted": 2, "skipped": 1}
    gj = client.get("/api/reports.geojson").get_json()
    assert len(gj["features"]) == 2
    props = {f["properties"]["state"]: f["properties"] for f in gj["features"]}
    assert props["OH"]["category"] == "uap" and props["OH"]["credibility_tier"] == "unverified_public"
    assert props["WV"]["category"] == "cryptid" and props["WV"]["public_record_date"] == "2001-05-01"
    assert client.get("/api/reports.geojson?category=cryptid").get_json()["features"][0]["properties"]["state"] == "WV"
    assert client.get("/api/reports.geojson?from=2000-01-01").get_json()["features"][0]["properties"]["state"] == "OH"
    rid = props["WV"]["id"]
    page = client.get(f"/report/{rid}").get_data(as_text=True)
    assert "bfro.net" in page and "Public record" in page


def test_merge_candidates_and_alerts(client, app):
    from app.reports import create_report, merge_candidates, get_report
    with app.app_context():
        a = create_report({"category": "light", "narrative": "x" * 30, "lat": 39.0, "lng": -82.0, "state": "OH", "county": "Meigs", "event_date": "2020-01-01"}, "web_form", status="approved")
        b = create_report({"category": "light", "narrative": "y" * 30, "lat": 39.005, "lng": -82.005, "state": "OH", "county": "Meigs", "event_date": "2020-01-03"}, "web_form")
        c = create_report({"category": "light", "narrative": "z" * 30, "lat": 39.5, "lng": -82.0, "state": "OH", "county": "Hocking", "event_date": "2020-01-03"}, "web_form")
        assert [r["id"] for r in merge_candidates(get_report(b))] == [a]
        assert merge_candidates(get_report(c)) == []
    # county alert
    client.post("/subscribe", data={"email": "fan@example.test", "state": "OH", "county": "Hocking"})
    link = app.extensions["outbox"][-1]["text"]
    token = re.search(r"/confirm/(\S+)", link).group(1)
    client.get(f"/confirm/{token}")
    login(client, app)
    n0 = len(app.extensions["outbox"])
    client.post(f"/mod/report/{c}/approve", data={})
    sent = [m for m in app.extensions["outbox"][n0:] if m["to"] == "fan@example.test"]
    assert len(sent) == 1 and "Hocking" in sent[0]["subject"]
    # merge b into a
    client.post(f"/mod/report/{b}/merge", data={"into": a})
    r = client.get(f"/report/{b}")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/report/{a}")


def test_hoax_flags_and_honeypot(client, app):
    base = {"category": "other", "narrative": "A very long and specific narrative that is more than eighty characters so the duplicate text check runs on it properly.",
            "lat": "0", "lng": "0", "age_ok": "yes"}
    client.post("/submit", data=base)
    client.post("/submit", data=base)
    login(client, app)
    q = client.get("/mod/").get_data(as_text=True)
    assert "identical_text" in q and "coords_suspicious" in q
    r = client.post("/submit", data=dict(base, website="spam"))
    assert r.status_code == 302
    assert q.count("coords_suspicious") == 2  # honeypot submission was dropped


def test_two_way_hotline(client, app):
    """Moderator texts back and calls back from the hotline number; the witness's answer lands in the thread."""
    import app.conversation as conv
    sent = []

    def fake_post(what, data):
        sent.append((what, data))
        if data.get("To") == "+17405550999":
            return False, None, conv.TWILIO_ERRORS[21610]
        return True, "SMfake" + str(len(sent)), None
    orig = conv._post
    conv._post = fake_post
    try:
        def sms(body, phone="+17405550123"):
            return client.post("/hooks/twilio/sms", data={"From": phone, "Body": body, "NumMedia": "0", "MessageSid": "SM9"}).get_data(as_text=True)
        for b in ("Thing on the haul road with eyes like brake lights", "Glouster, Ohio", "SKIP", "2", "YES"):
            sms(b)
        login(client, app)
        rid = re.search(r"/mod/report/([0-9a-f-]{36})", client.get("/mod/").get_data(as_text=True)).group(1)
        page = client.get(f"/mod/report/{rid}").get_data(as_text=True)
        assert "Talk to the witness" in page and "Call them back" in page

        r = client.post(f"/mod/report/{rid}/text", data={"body": "Can we air this on Oddcall Radio with your voice changed?"}, follow_redirects=True)
        assert "Sent from the hotline number" in r.get_data(as_text=True)
        assert sent[-1][0] == "Messages" and sent[-1][1]["From"] == "+17405085757" and sent[-1][1]["To"] == "+17405550123"

        # their answer goes to the thread, not into a new report questionnaire
        assert "Where did this happen" not in sms("Yeah go ahead, change my voice")
        page = client.get(f"/mod/report/{rid}").get_data(as_text=True)
        assert "Yeah go ahead, change my voice" in page
        assert "Witnesses waiting on you" in client.get("/mod/").get_data(as_text=True)
        assert any("texted back" in m["subject"] for m in app.extensions["outbox"])

        # NEW starts a fresh report even inside an open thread
        assert "New report" in sms("NEW")
        assert "Where did this happen" in sms("Second thing, different night, lights over the reservoir")

        # callback rings the moderator's cell, then dials the witness with the hotline as caller ID
        client.post(f"/mod/report/{rid}/call")
        what, data = sent[-1]
        assert what == "Calls" and data["To"] == "+17405550101" and data["From"] == "+17405085757"
        assert 'callerId="+17405085757"' in data["Twiml"] and "+17405550123" in data["Twiml"]

        # STOP never starts a report
        assert "Where did this happen" not in sms("STOP", phone="+17405550777")
    finally:
        conv._post = orig

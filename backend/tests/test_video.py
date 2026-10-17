"""Video consultations: offering them, booking, the join window and the private room link."""
from datetime import datetime, timedelta

from conftest import MONDAY


def video_doctor(platform, fee=None):
    doc = platform.doctor(name="Dr. Online", km_from_home=40)
    res = doc.put("/api/doctor/video-settings", json={"offers_video": True, "video_fee": fee})
    assert res.status_code == 200, res.text
    return doc


def test_offer_search_and_book(platform):
    plain = platform.doctor(name="Dr. Clinic Only")
    patient = platform.patient()
    platform.set_home(patient)
    assert platform.book(patient, plain, visit_type="video").status_code == 400
    doc = video_doctor(platform, fee=500)
    assert doc.get("/api/doctor/video-settings").json() == {"offers_video": True, "video_fee": 500}
    from conftest import HOME
    found = platform.client().get(f"/api/doctors?video=true&lat={HOME[0]}&lng={HOME[1]}").json()
    assert [d["name"] for d in found] == ["Dr. Online"] and found[0]["offers_video"] and found[0]["distance_km"] > 30
    res = platform.book(patient, doc, visit_type="video")
    assert res.status_code == 200, res.text
    appt = res.json()["appointment"]
    assert appt["visit_type"] == "video" and appt["fee"] == 500 and appt["status"] == "confirmed"
    assert appt["video_join"] == f"/video/{appt['id']}" and "video_room" not in appt
    mail = platform.mails_to(patient.user["email"])[-1]
    assert "Video consultation" in mail["text"] and f"/video/{appt['id']}" in mail["text"] and "meet.jit.si" not in mail["text"]
    assert "Video consultation" in mail["ics"]
    doc.patch(f"/api/appointments/{appt['id']}", json={"payment_status": "paid"})
    receipt = patient.get(f"/api/appointments/{appt['id']}/receipt").json()
    assert "(video consultation)" in receipt["lines"][0]["description"]


def test_join_window_and_room_privacy(platform, monkeypatch):
    doc = video_doctor(platform)
    patient = platform.patient()
    appt = platform.book(patient, doc, visit_type="video").json()["appointment"]
    assert appt["fee"] == 800  # no video fee set: the service fee
    status = patient.get(f"/api/appointments/{appt['id']}/video").json()
    assert status["state"] == "early" and "url" not in status and "join" not in status
    page = patient.get(f"/video/{appt['id']}", follow_redirects=False)
    assert page.status_code == 200 and "opens" in page.text

    start = datetime.fromisoformat(f"{MONDAY}T10:00")
    monkeypatch.setattr(platform.settings, "now", lambda: start - timedelta(minutes=5))
    status = patient.get(f"/api/appointments/{appt['id']}/video").json()
    assert status["state"] == "open" and status["join"] == f"/video/{appt['id']}"
    for who in (patient, doc):
        go = who.get(f"/video/{appt['id']}", follow_redirects=False)
        assert go.status_code == 303 and go.headers["location"].startswith("https://meet.jit.si/pyshio-")
    room = patient.get(f"/video/{appt['id']}", follow_redirects=False).headers["location"]
    assert room == doc.get(f"/video/{appt['id']}", follow_redirects=False).headers["location"]
    stranger = platform.patient(name="Not Invited")
    assert stranger.get(f"/video/{appt['id']}", follow_redirects=False).status_code == 404
    assert platform.admin().get(f"/api/appointments/{appt['id']}/video").status_code == 403
    anon = platform.client().get(f"/video/{appt['id']}", follow_redirects=False)
    assert anon.status_code == 303 and anon.headers["location"] == f"/login?next=/video/{appt['id']}"

    monkeypatch.setattr(platform.settings, "now", lambda: start + timedelta(minutes=120))
    assert patient.get(f"/api/appointments/{appt['id']}/video").json()["state"] == "ended"


def test_clinic_booking_unchanged(platform):
    doc = video_doctor(platform)
    patient = platform.patient()
    appt = platform.book(patient, doc).json()["appointment"]
    assert appt["visit_type"] == "clinic" and appt["video_join"] is None
    assert patient.get(f"/api/appointments/{appt['id']}/video").json() == {"state": "not_video"}


def test_pwa_files(platform):
    c = platform.client()
    manifest = c.get("/manifest.webmanifest")
    assert manifest.status_code == 200 and manifest.headers["content-type"].startswith("application/manifest+json")
    data = manifest.json()
    assert data["display"] == "standalone" and {i["sizes"] for i in data["icons"]} == {"192x192", "512x512"}
    for icon in data["icons"]:
        res = c.get(icon["src"])
        assert res.status_code == 200 and res.content[:8] == b"\x89PNG\r\n\x1a\n"
    sw = c.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"] and sw.headers["service-worker-allowed"] == "/"
    assert c.get("/offline").status_code == 200
    assert c.get("/features/notifications/bell.js").status_code == 200
    assert c.get("/features/labs/logic.py").status_code == 404  # slice code is never served

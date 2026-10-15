"""Home visits: patient requests, doctor sends charges & time, patient accepts or declines."""
import pytest

from conftest import MONDAY, TUESDAY


@pytest.fixture
def setup(platform):
    doctor = platform.doctor(name="Dr. Home", km_from_home=1, offers_home_visits=True, home_visit_radius_km=4, home_visit_fee=200)
    patient = platform.patient(name="Asha Rao")
    platform.set_home(patient)
    platform.outbox.clear()
    return doctor, patient


def request_visit(platform, patient, doctor, **extra):
    return platform.book(patient, doctor, visit_type="home", reason="Knee pain, can't travel", **extra)


def test_full_home_visit_flow(platform, setup):
    doctor, patient = setup
    listed = platform.client().get("/api/doctors?home_visit=true").json()
    assert [d["name"] for d in listed] == ["Dr. Home"] and listed[0]["home_visit_fee"] == 200

    res = request_visit(platform, patient, doctor)
    assert res.status_code == 200, res.text
    appt = res.json()["appointment"]
    assert appt["status"] == "requested" and appt["visit_type"] == "home" and appt["visit_address"] == "Flat 4, Shivajinagar, Pune"
    assert appt["travel_fee"] == 200 and appt["total_fee"] == 1000
    assert any("Home-visit request" in m["subject"] for m in platform.mails_to(doctor.user["email"]))
    # The requested time is held for the doctor
    slots = platform.client().get(f"/api/doctors/{doctor.slug}/slots?date={MONDAY}&service_id={doctor.service_id}").json()["slots"]
    assert "10:00" not in slots
    inbox = doctor.get("/api/doctor/requests").json()
    assert inbox["counts"]["home_visits"] == 1 and inbox["home_visits"][0]["visit_address"].startswith("Flat 4")

    # Patient can't accept before the doctor answers
    assert patient.post(f"/api/appointments/{appt['id']}/accept-visit").status_code == 400
    quote = doctor.post(f"/api/doctor/appointments/{appt['id']}/home-visit", json={
        "fee": 1200, "travel_fee": 300, "appointment_date": TUESDAY, "appointment_time": "17:00", "duration": 60,
        "note": "Please keep your recent X-ray ready."})
    assert quote.status_code == 200, quote.text
    q = quote.json()
    assert q["status"] == "awaiting_patient" and q["total_fee"] == 1500 and q["appointment_date"] == TUESDAY and q["duration"] == 60
    mail = [m for m in platform.mails_to(patient.user["email"]) if "Home visit" in m["subject"]][0]
    assert "₹1,500" in mail["subject"] and "X-ray" in mail["text"]
    # Monday 10:00 is free again, Tuesday 17:00 is held
    assert "10:00" in platform.client().get(f"/api/doctors/{doctor.slug}/slots?date={MONDAY}&service_id={doctor.service_id}").json()["slots"]

    ok = patient.post(f"/api/appointments/{appt['id']}/accept-visit")
    assert ok.status_code == 200 and ok.json()["appointment"]["status"] == "confirmed"
    assert any("confirmed" in m["subject"].lower() for m in platform.mails_to(patient.user["email"]))
    # The doctor's note is in the appointment's messages; the receipt includes travel
    detail = patient.get(f"/api/appointments/{appt['id']}").json()
    assert any("X-ray" in m["body"] for m in detail["messages"])
    doctor.patch(f"/api/appointments/{appt['id']}", json={"payment_status": "paid"})
    receipt = patient.get(f"/api/appointments/{appt['id']}/receipt").json()
    assert receipt["total_paise"] == 150000 and receipt["delivery_fee_paise"] == 30000


def test_rules(platform, setup):
    doctor, patient = setup
    clinic_only = platform.doctor(name="Dr. Clinic Only", km_from_home=1)
    assert request_visit(platform, patient, clinic_only).status_code == 400
    far = platform.patient(name="Far Patient")
    platform.set_home(far, km_from_home=9)
    res = request_visit(platform, far, doctor)
    assert res.status_code == 400 and "visits within 4 km" in res.json()["detail"]
    no_home = platform.patient(name="No Address")
    assert request_visit(platform, no_home, doctor).status_code == 400
    assert request_visit(platform, no_home, doctor, visit_address="12 MG Road, Pune").status_code == 200
    # A clinic booking still works as before
    assert platform.book(patient, doctor, time="11:00").json()["appointment"]["status"] == "confirmed"


def test_decline_and_others_cant_touch(platform, setup):
    doctor, patient = setup
    appt = request_visit(platform, patient, doctor).json()["appointment"]
    other_doc = platform.doctor(name="Dr. Other", km_from_home=1)
    body = {"fee": 900, "appointment_date": MONDAY, "appointment_time": "10:00"}
    assert other_doc.post(f"/api/doctor/appointments/{appt['id']}/home-visit", json=body).status_code == 404
    assert doctor.post(f"/api/doctor/appointments/{appt['id']}/home-visit", json=body).status_code == 200
    # Patient declines the charges
    res = patient.post(f"/api/appointments/{appt['id']}/cancel", json={"message": "Too expensive, sorry"})
    assert res.status_code == 200 and res.json()["appointment"]["status"] == "cancelled"
    assert doctor.post(f"/api/doctor/appointments/{appt['id']}/home-visit", json=body).status_code == 400
    # Doctor can decline a request too
    appt2 = request_visit(platform, patient, doctor, time="11:00").json()["appointment"]
    assert doctor.post(f"/api/appointments/{appt2['id']}/cancel", json={"message": "Not visiting that day"}).json()["appointment"]["status"] == "cancelled"


def test_unanswered_requests_expire(platform, setup, monkeypatch):
    from datetime import datetime, timedelta
    doctor, patient = setup
    appt = request_visit(platform, patient, doctor).json()["appointment"]
    real = datetime.now()
    monkeypatch.setattr(platform.settings, "now", lambda: real + timedelta(days=12))
    import importlib, jobs
    importlib.reload(jobs).release_holds()
    import sqlite3
    conn = sqlite3.connect(platform.settings.DB_PATH)
    assert conn.execute("SELECT status FROM appointments WHERE id = ?", (appt["id"],)).fetchone()[0] == "expired"

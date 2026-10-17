"""Health profile + allergy checks, medicine reminders / adherence and follow-ups."""
from datetime import datetime, time, timedelta

import pytest

from test_network import prescribe, world  # noqa: F401  (fixture)


def at(platform, monkeypatch, when):
    monkeypatch.setattr(platform.settings, "now", lambda: when)


def today(platform):
    return platform.settings.now().date()


# ---------- Dosage text -> times of day ----------
@pytest.mark.parametrize("dosage,slots", [
    ("1-0-1", ["morning", "night"]),
    ("1-1-1", ["morning", "afternoon", "night"]),
    ("0-0-1", ["night"]),
    ("1/2-0-1/2", ["morning", "night"]),
    ("1-0-0-1", ["morning", "night"]),
    ("1-1-1-1", ["morning", "afternoon", "evening", "night"]),
    ("BD", ["morning", "night"]),
    ("TDS after food", ["morning", "afternoon", "night"]),
    ("1 at bedtime", ["night"]),
    ("1 spoon daily", ["morning"]),
    ("SOS", []),
    ("Once a week", []),
    ("", []),
])
def test_dosage_slots(platform, dosage, slots):
    from features.medicine_reminders.logic import slots_for
    assert [s for s, _ in slots_for(dosage)] == slots


# ---------- Today's doses ----------
def test_today_schedule_and_logging(platform, world):
    rx = prescribe(platform, world)
    patient = world["patient"]
    day = patient.get("/api/my/doses").json()
    groups = {g["slot"]: g for g in day["groups"]}
    assert set(groups) == {"morning", "night"} and groups["morning"]["doses"][0]["name"] == "Dolo 650"
    assert [m["name"] for m in day["as_needed"]] == ["Vitamin D3 60K"]  # weekly: not on the daily list
    dose = groups["morning"]["doses"][0]
    body = {"source": dose["source"], "source_id": dose["source_id"], "dose_date": day["date"], "slot": "morning",
            "status": "taken"}
    res = patient.post("/api/my/doses", json=body)
    assert res.status_code == 200, res.text
    assert res.json()["summary"] == {"due": 2, "taken": 1, "skipped": 0}
    assert patient.post("/api/my/doses", json=body).json()["summary"]["taken"] == 1  # same dose twice = once
    assert patient.post("/api/my/doses", json=body | {"status": "skipped"}).json()["summary"] == {"due": 2, "taken": 0, "skipped": 1}
    assert patient.post("/api/my/doses", json=body | {"status": "pending"}).json()["summary"]["skipped"] == 0
    assert patient.post("/api/my/doses", json=body | {"slot": "afternoon"}).status_code == 404  # not scheduled
    tomorrow = (today(platform) + timedelta(days=1)).isoformat()
    assert patient.post("/api/my/doses", json=body | {"dose_date": tomorrow}).status_code == 400
    other = platform.patient(name="Someone Else")
    assert other.post("/api/my/doses", json=body).status_code == 404
    # Own medicines join the schedule
    assert patient.post("/api/my/medicines", json={"name": "Thyronorm 50", "dosage": "1-0-0"}).status_code == 200
    names = [d["name"] for g in patient.get("/api/my/doses").json()["groups"] if g["slot"] == "morning" for d in g["doses"]]
    assert names == ["Dolo 650", "Thyronorm 50"]
    assert rx["id"]


def test_adherence_for_patient_and_doctor(platform, world, monkeypatch):
    prescribe(platform, world)
    patient = world["patient"]
    day0 = today(platform)
    morning = patient.get("/api/my/doses").json()["groups"][0]["doses"][0]
    patient.post("/api/my/doses", json={"source": morning["source"], "source_id": morning["source_id"],
                                        "dose_date": day0.isoformat(), "slot": "morning", "status": "taken"})
    at(platform, monkeypatch, datetime.combine(day0 + timedelta(days=1), time(23, 0)))
    adh = patient.get("/api/my/adherence?days=7").json()
    assert (adh["due"], adh["taken"], adh["percent"]) == (4, 1, 25)
    assert adh["medicines"][0]["name"] == "Dolo 650" and adh["week"][-2]["percent"] == 50
    seen = world["gp"].get(f"/api/doctor/patients/{patient.user['id']}/adherence").json()
    assert seen["percent"] == 25 and seen["tracking"] is True
    stranger = platform.doctor(name="Dr. Stranger")
    assert stranger.get(f"/api/doctor/patients/{patient.user['id']}/adherence").status_code == 404


def test_reminder_times_digest_and_nudges(platform, world, monkeypatch):
    prescribe(platform, world)
    patient = world["patient"]
    res = patient.put("/api/my/reminders", json={"times": {"morning": "07:00", "night": "22:00"}, "email_digest": True})
    assert res.status_code == 200 and res.json()["times"]["morning"] == "07:00" and res.json()["times"]["afternoon"] == "14:00"
    assert patient.put("/api/my/reminders", json={"times": {"morning": "7am"}}).status_code == 422
    import jobs
    day0 = today(platform)
    at(platform, monkeypatch, datetime.combine(day0, time(6, 30)))
    assert jobs.dose_digest() == 0  # before the morning dose time
    at(platform, monkeypatch, datetime.combine(day0, time(7, 20)))
    platform.outbox.clear()
    assert jobs.dose_digest() == 1 and jobs.dose_digest() == 0
    mail = platform.mails_to(patient.user["email"])[0]
    assert mail["subject"].startswith("Today's medicines") and "Dolo 650" in mail["text"]
    assert jobs.dose_nudges() == 1 and jobs.dose_nudges() == 0
    titles = [i["title"] for i in patient.get("/api/notifications").json()["items"]]
    assert "Time for your morning medicines" in titles


# ---------- Health profile & allergies ----------
def test_health_profile_and_allergy_warning(platform, world):
    patient = world["patient"]
    res = patient.put("/api/my/health-profile", json={"blood_group": "O+", "allergies": "Penicillin, peanuts",
                                                      "chronic_conditions": "Asthma", "emergency_name": "Ravi Rao",
                                                      "emergency_phone": "+91 98200 11111"})
    assert res.status_code == 200, res.text
    assert patient.get("/api/my/health-profile").json()["allergies"] == "Penicillin, peanuts"
    assert patient.put("/api/my/health-profile", json={"blood_group": "Z+"}).status_code == 422
    gp = world["gp"]
    seen = gp.get(f"/api/doctor/patients/{patient.user['id']}/health-profile").json()
    assert seen["blood_group"] == "O+" and seen["emergency_phone"] == "+91 98200 11111"
    assert platform.doctor(name="Dr. Nobody").get(f"/api/doctor/patients/{patient.user['id']}/health-profile").status_code == 404
    check = gp.post("/api/doctor/allergy-check", json={"patient_id": patient.user["id"],
                                                        "items": [{"name": "Mox 500", "generic_name": "Amoxicillin"}]})
    assert check.json()["warnings"] and "penicillin" in check.json()["warnings"][0]
    items = [{"name": "Augmentin 625", "generic_name": "Amoxicillin + Clavulanic acid", "dosage": "1-0-1", "duration": "5 days",
              "quantity": 10}]
    # An allergy match is a major safety alert: issuing waits until the doctor acknowledges it (features/rx_safety)
    blocked = gp.post(f"/api/appointments/{world['appt']['id']}/prescriptions", json={"items": items})
    assert blocked.status_code == 409
    codes = [a["code"] for a in blocked.json()["detail"]["alerts"] if a["kind"] == "allergy"]
    rx = prescribe(platform, world, items=items, acknowledged_alerts=codes)
    assert rx["warnings"] and "Augmentin" in rx["warnings"][0]
    safe = prescribe(platform, world)
    assert safe["warnings"] == []


# ---------- Follow-ups ----------
def test_follow_up_shown_reminded_once_and_skipped_when_booked(platform, world, monkeypatch):
    gp, patient = world["gp"], world["patient"]
    day0 = today(platform)
    bad = gp.post(f"/api/appointments/{world['appt']['id']}/prescriptions",
                  json={"items": [{"name": "Dolo 650", "dosage": "1-0-1", "quantity": 10}],
                        "follow_up_on": (day0 - timedelta(days=1)).isoformat()})
    assert bad.status_code == 422
    due = (day0 + timedelta(days=10)).isoformat()
    rx = prescribe(platform, world, follow_up_on=due)
    assert rx["follow_up_on"] == due
    ups = patient.get("/api/my/follow-ups").json()
    assert len(ups) == 1 and ups[0]["days_left"] == 10 and ups[0]["book_path"].startswith(f"/doctors/{gp.slug}?service=")
    import jobs
    assert jobs.remind_follow_ups() == 0  # too early
    at(platform, monkeypatch, datetime.combine(day0 + timedelta(days=9), time(9, 0)))
    platform.outbox.clear()
    assert jobs.remind_follow_ups() == 1 and jobs.remind_follow_ups() == 0
    assert platform.mails_to(patient.user["email"])[0]["subject"].startswith("Follow-up with")


def test_follow_up_already_booked_is_not_reminded(platform, world, monkeypatch):
    gp, patient = world["gp"], world["patient"]
    day0 = today(platform)
    prescribe(platform, world, follow_up_on=(day0 + timedelta(days=5)).isoformat())
    monkeypatch.setattr(platform.settings, "now", lambda: datetime.combine(day0, time(12, 0)) + timedelta(seconds=5))
    later = (day0 + timedelta(days=12))
    while later.weekday() == 6:
        later += timedelta(days=1)
    assert platform.book(patient, gp, day=later.isoformat(), time="11:00").status_code == 200
    assert patient.get("/api/my/follow-ups").json()[0]["booked_for"] == later.isoformat()
    import jobs
    at(platform, monkeypatch, datetime.combine(day0 + timedelta(days=4), time(9, 0)))
    assert jobs.remind_follow_ups() == 0

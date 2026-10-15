"""Risk nudges: no-show score and one "Still coming?" email; weekly note when medicine-taking drops."""
import uuid
from datetime import datetime, time, timedelta

from test_network import prescribe, world  # noqa: F401  (fixture)


def at(platform, monkeypatch, when):
    monkeypatch.setattr(platform.settings, "now", lambda: when)


def past_visits(platform, w, statuses):
    with platform.db.get_db() as conn:
        for n, status in enumerate(statuses):
            conn.execute("INSERT INTO appointments (id, reference, doctor_id, patient_id, service_name, duration, fee, appointment_date, "
                         "appointment_time, status, payment_method, payment_status, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, 'Consultation', 30, 500, ?, '10:00', ?, 'clinic', 'unpaid', ?, ?)",
                         (str(uuid.uuid4()), f"PY-OLD{n:02d}", w["gp"].user["id"], w["patient"].user["id"],
                          f"2025-0{n + 1}-10", status, "2025-01-01T09:00:00", "2025-01-01T09:00:00"))


def test_score_reasons_and_band(platform, world):
    past_visits(platform, world, ["no_show", "no_show", "completed"])
    risk = world["gp"].get("/api/doctor/visit-risk").json()[world["appt"]["id"]]
    assert risk["band"] in ("medium", "high") and any("missed 2 of 3" in r for r in risk["reasons"])
    assert "pays at the clinic" in risk["reasons"]
    assert world["patient"].get("/api/doctor/visit-risk").status_code in (401, 403)
    from features.risk_nudges.logic import band
    assert (band(80), band(40), band(10), band(None)) == ("high", "medium", "low", None)


def test_still_coming_nudge_sent_once_and_skipped_when_confirmed(platform, world, monkeypatch):
    w = world
    past_visits(platform, w, ["no_show", "no_show", "no_show", "completed"])
    appt = w["appt"]
    start = datetime.strptime(f"{appt['appointment_date']} {appt['appointment_time']}", "%Y-%m-%d %H:%M")
    import jobs
    at(platform, monkeypatch, start - timedelta(hours=10))
    assert jobs.nudge_risky_appointments() == 0           # too early
    at(platform, monkeypatch, start - timedelta(hours=4))
    platform.outbox.clear()
    assert jobs.nudge_risky_appointments() == 1 and jobs.nudge_risky_appointments() == 0
    assert platform.mails_to(w["patient"].user["email"])[0]["subject"].startswith("Still coming")
    # A confirmed patient isn't nudged (fresh visit)
    other = platform.patient(name="Confirmed Patient")
    platform.set_home(other)
    booked = platform.book(other, w["gp"], time="11:00").json()["appointment"]
    assert other.post(f"/api/appointments/{booked['id']}/confirm-attendance").json()["confirmed"]
    assert w["patient"].post(f"/api/appointments/{booked['id']}/confirm-attendance").status_code == 404  # not theirs
    with platform.db.get_db() as conn:
        conn.execute("UPDATE appointments SET no_show_risk = 90 WHERE id = ?", (booked["id"],))
    at(platform, monkeypatch, datetime.strptime(f"{booked['appointment_date']} 11:00", "%Y-%m-%d %H:%M") - timedelta(hours=4))
    assert not [m for m in platform.mails_to(other.user["email"]) if m["subject"].startswith("Still coming")]


def test_adherence_drop_alert_weekly(platform, world, monkeypatch):
    w = world
    day0 = platform.settings.now().date()
    prescribe(platform, w, items=[{"name": "Telma 40", "generic_name": "Telmisartan", "dosage": "1-0-1", "duration": "60 days",
                                    "quantity": 120}])
    dose = w["patient"].get("/api/my/doses").json()["groups"][0]["doses"][0]
    with platform.db.get_db() as conn:
        for d in range(20):   # taken every dose for 20 days, then nothing for the last week
            for slot in ("morning", "night"):
                conn.execute("INSERT INTO dose_logs (patient_id, source, source_id, name, dose_date, slot, status, logged_at) "
                             "VALUES (?, ?, ?, ?, ?, ?, 'taken', ?)", (w["patient"].user["id"], dose["source"], dose["source_id"], "Telma 40",
                                                                    (day0 + timedelta(days=d)).isoformat(), slot,
                                                                    f"{(day0 + timedelta(days=d)).isoformat()}T09:00:00"))
    at(platform, monkeypatch, datetime.combine(day0 + timedelta(days=27), time(23, 0)))
    import jobs
    platform.outbox.clear()
    assert jobs.adherence_alerts() == 1 and jobs.adherence_alerts() == 0     # once a week
    mail = platform.mails_to(w["patient"].user["email"])[0]
    assert mail["subject"] == "How are your medicines going?" and "0%" in mail["text"]

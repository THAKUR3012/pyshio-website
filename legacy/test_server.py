import importlib
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def next_weekday(weekday: int, min_days_ahead: int = 2) -> str:
    """Next date with the given weekday (Mon=0) at least min_days_ahead from today."""
    d = date.today() + timedelta(days=min_days_ahead)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d.isoformat()


MONDAY = next_weekday(0)
FRIDAY = next_weekday(4)
SATURDAY = next_weekday(5)
SUNDAY = next_weekday(6)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSHIO_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("PYSHIO_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("PYSHIO_ENV_FILE", str(tmp_path / "no.env"))  # never read a real .env
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("PYSHIO_SECRET", raising=False)
    import server as srv
    return importlib.reload(srv)


@pytest.fixture
def outbox(server, monkeypatch):
    """Capture emails instead of sending them."""
    sent = []

    def fake_send(to, subject, text, html=None, ics=None):
        sent.append({"to": to, "subject": subject, "text": text, "html": html, "ics": ics})
        return True

    monkeypatch.setattr(server.mailer, "send_email", fake_send)
    return sent


@pytest.fixture
def client(server):
    return TestClient(server.app)


@pytest.fixture
def admin(client):
    token = client.post("/api/admin/login", json={"password": "s3cret"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def booking(**overrides):
    data = {
        "patient_name": "Asha Rao",
        "email": "Asha@Example.com",
        "phone": "+91 98765 43210",
        "service": "followup",
        "practitioner": "dr-sarah",
        "reason": "Lower back pain",
        "appointment_date": MONDAY,
        "appointment_time": "10:00",
    }
    data.update(overrides)
    return data


def db_rows(server, sql="SELECT * FROM appointments", params=()):
    conn = sqlite3.connect(server.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


# ---------- Data is stored properly ----------
def test_booking_is_persisted_with_all_fields(client, server):
    res = client.post("/api/book", json=booking())
    assert res.status_code == 200, res.text
    body = res.json()

    rows = db_rows(server)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == body["id"]
    assert row["reference"] == body["reference"] and row["reference"].startswith("PY-")
    assert row["patient_name"] == "Asha Rao"
    assert row["email"] == "asha@example.com"  # normalised
    assert row["phone"] == "+91 98765 43210"
    assert row["service"] == "followup"
    assert row["practitioner"] == "dr-sarah"
    assert row["reason"] == "Lower back pain"
    assert row["appointment_date"] == MONDAY
    assert row["appointment_time"] == "10:00"
    assert row["status"] == "confirmed"
    assert row["created_at"] and row["updated_at"]
    assert body["service_name"] == "Follow-up Session"
    assert body["practitioner_name"] == "Dr. Sarah Chen"


def test_whitespace_is_trimmed_and_blank_reason_is_null(client, server):
    client.post("/api/book", json=booking(patient_name="  Asha   Rao ", reason="   "))
    row = db_rows(server)[0]
    assert row["patient_name"] == "Asha Rao"
    assert row["reason"] is None


def test_booked_slot_disappears_from_availability(client):
    client.post("/api/book", json=booking())
    slots = client.get(f"/api/slots?date={MONDAY}&practitioner=dr-sarah").json()
    assert "10:00" not in slots["slots"]
    assert "10:00" in slots["booked"]


# ---------- Double booking ----------
def test_same_practitioner_same_slot_is_rejected(client):
    assert client.post("/api/book", json=booking()).status_code == 200
    res = client.post("/api/book", json=booking(email="other@example.com"))
    assert res.status_code == 409


def test_concurrent_bookings_only_one_wins(client, server):
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(lambda _: client.post("/api/book", json=booking()).status_code, range(8)))
    assert codes.count(200) == 1
    assert codes.count(409) == 7
    assert len(db_rows(server)) == 1


def test_other_practitioners_stay_bookable_at_same_time(client):
    client.post("/api/book", json=booking(practitioner="dr-sarah"))
    slots = client.get(f"/api/slots?date={MONDAY}&practitioner=dr-james").json()["slots"]
    assert "10:00" in slots
    assert client.post("/api/book", json=booking(practitioner="dr-james")).status_code == 200


def test_any_practitioner_is_assigned_until_everyone_is_busy(client):
    assigned = set()
    for i in range(3):
        res = client.post("/api/book", json=booking(practitioner="any", email=f"p{i}@example.com"))
        assert res.status_code == 200
        assigned.add(res.json()["practitioner"])
    assert assigned == {"dr-sarah", "dr-james", "dr-emily"}
    assert client.post("/api/book", json=booking(practitioner="any")).status_code == 409
    assert "10:00" not in client.get(f"/api/slots?date={MONDAY}").json()["slots"]


def test_long_service_blocks_following_slot(client):
    # Initial assessment is 60 min -> occupies 10:00 and 10:30
    client.post("/api/book", json=booking(service="initial"))
    slots = client.get(f"/api/slots?date={MONDAY}&practitioner=dr-sarah").json()["slots"]
    assert "10:30" not in slots
    assert "11:00" in slots
    # And a 60-min booking at 09:30 would overlap 10:00
    assert "09:30" not in client.get(
        f"/api/slots?date={MONDAY}&practitioner=dr-sarah&service=initial").json()["slots"]


def test_long_service_cannot_run_into_lunch_or_past_closing(client):
    slots = client.get(f"/api/slots?date={MONDAY}&service=initial").json()["slots"]
    assert "11:30" not in slots  # would run into 12:00 lunch break
    assert "17:30" not in slots  # would run past 18:00
    assert "17:00" in slots


def test_cancelled_slot_becomes_available_again(client, admin):
    appt = client.post("/api/book", json=booking()).json()
    client.delete(f"/api/appointments/{appt['id']}", headers=admin)
    assert client.post("/api/book", json=booking(email="next@example.com")).status_code == 200


# ---------- Clinic hours & dates ----------
def test_sunday_is_closed(client):
    res = client.get(f"/api/slots?date={SUNDAY}").json()
    assert res["open"] is False and res["slots"] == []
    assert client.post("/api/book", json=booking(appointment_date=SUNDAY)).status_code == 400


def test_saturday_and_friday_hours(client):
    sat = client.get(f"/api/slots?date={SATURDAY}").json()["all_slots"]
    assert sat[-1] == "11:30" and "13:00" not in sat
    fri = client.get(f"/api/slots?date={FRIDAY}").json()["all_slots"]
    assert fri[-1] == "16:30"
    assert client.post("/api/book", json=booking(appointment_date=FRIDAY, appointment_time="17:00")).status_code == 409


def test_past_and_far_future_dates_rejected(client):
    past = (date.today() - timedelta(days=1)).isoformat()
    far = (date.today() + timedelta(days=200)).isoformat()
    assert client.post("/api/book", json=booking(appointment_date=past)).status_code == 400
    assert client.post("/api/book", json=booking(appointment_date=far)).status_code == 400
    assert client.post("/api/book", json=booking(appointment_date="06/10/2026")).status_code == 400
    assert client.post("/api/book", json=booking(appointment_time="10:15")).status_code == 400


# ---------- Validation ----------
@pytest.mark.parametrize("field,value", [
    ("patient_name", "A"),
    ("email", "not-an-email"),
    ("phone", "12"),
    ("phone", "call me maybe"),
    ("service", "massage"),
    ("practitioner", "dr-nobody"),
    ("reason", "x" * 501),
])
def test_invalid_input_rejected(client, server, field, value):
    res = client.post("/api/book", json=booking(**{field: value}))
    assert res.status_code == 422
    assert isinstance(res.json()["detail"], str)
    assert db_rows(server) == []


def test_legacy_labels_still_accepted(client):
    res = client.post("/api/book", json=booking(service="Initial Assessment (60 min)", practitioner="Any available"))
    assert res.status_code == 200
    assert res.json()["service"] == "initial"


# ---------- Patient self-service ----------
def test_patient_can_lookup_cancel_and_reschedule(client, server):
    appt = client.post("/api/book", json=booking()).json()
    creds = {"reference": appt["reference"].lower(), "email": "ASHA@example.com"}

    found = client.post("/api/my-booking", json=creds)
    assert found.status_code == 200 and found.json()["id"] == appt["id"]
    assert "notes" not in found.json()

    moved = client.post("/api/my-booking/reschedule", json={**creds, "appointment_date": MONDAY, "appointment_time": "14:00"})
    assert moved.status_code == 200
    row = db_rows(server)[0]
    assert row["appointment_time"] == "14:00" and row["practitioner"] == "dr-sarah"

    assert client.post("/api/my-booking/cancel", json=creds).status_code == 200
    assert db_rows(server)[0]["status"] == "cancelled"
    assert client.post("/api/my-booking/cancel", json=creds).status_code == 400


def test_patient_lookup_needs_matching_email(client):
    appt = client.post("/api/book", json=booking()).json()
    res = client.post("/api/my-booking", json={"reference": appt["reference"], "email": "someone@else.com"})
    assert res.status_code == 404
    assert client.post("/api/my-booking/cancel", json={"reference": appt["reference"], "email": "x@y.com"}).status_code == 404


# ---------- Admin ----------
def test_admin_endpoints_require_login(client):
    appt = client.post("/api/book", json=booking()).json()
    assert client.get("/api/appointments").status_code == 401
    assert client.delete(f"/api/appointments/{appt['id']}").status_code == 401
    assert client.get("/api/appointments", headers={"Authorization": "Bearer 9999999999.forged"}).status_code == 401
    assert client.post("/api/admin/login", json={"password": "wrong"}).status_code == 401


def test_admin_list_filters_and_search(client, admin):
    client.post("/api/book", json=booking())
    client.post("/api/book", json=booking(patient_name="Ravi Kumar", email="ravi@example.com",
                                          practitioner="dr-james", appointment_date=FRIDAY))
    assert len(client.get("/api/appointments", headers=admin).json()) == 2
    assert [a["patient_name"] for a in client.get(f"/api/appointments?date={FRIDAY}", headers=admin).json()] == ["Ravi Kumar"]
    assert len(client.get("/api/appointments?practitioner=dr-sarah", headers=admin).json()) == 1
    assert len(client.get("/api/appointments?q=ravi", headers=admin).json()) == 1


def test_admin_status_notes_and_reschedule(client, server, admin):
    appt = client.post("/api/book", json=booking()).json()
    other = client.post("/api/book", json=booking(email="b@example.com", appointment_time="11:00")).json()

    res = client.patch(f"/api/appointments/{appt['id']}", headers=admin,
                       json={"status": "completed", "notes": "Good progress"})
    assert res.status_code == 200
    row = db_rows(server, "SELECT * FROM appointments WHERE id = ?", (appt["id"],))[0]
    assert row["status"] == "completed" and row["notes"] == "Good progress"

    # Moving onto another patient's slot is refused
    clash = client.patch(f"/api/appointments/{appt['id']}", headers=admin, json={"appointment_time": "11:00"})
    assert clash.status_code == 409

    moved = client.patch(f"/api/appointments/{other['id']}", headers=admin,
                         json={"appointment_time": "15:00", "practitioner": "dr-emily"})
    assert moved.status_code == 200
    assert moved.json()["appointment_time"] == "15:00" and moved.json()["practitioner"] == "dr-emily"

    assert client.patch(f"/api/appointments/{appt['id']}", headers=admin, json={"status": "bogus"}).status_code == 422


def test_admin_stats_and_csv_export(client, admin):
    client.post("/api/book", json=booking(patient_name="=HYPERLINK(evil)"))
    stats = client.get("/api/admin/stats", headers=admin).json()
    assert stats["upcoming_7_days"] in (0, 1)  # depends on how far away MONDAY is
    csv_text = client.get("/api/admin/export.csv", headers=admin).text
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("Sr No,reference")
    assert "'=HYPERLINK(evil)" in lines[1]


def test_blocked_time_removes_slots_and_reports_conflicts(client, admin):
    client.post("/api/book", json=booking(practitioner="dr-emily", appointment_time="09:00"))
    res = client.post("/api/admin/blocks", headers=admin, json={
        "block_date": MONDAY, "start_time": "09:00", "end_time": "12:00",
        "practitioner": "dr-emily", "reason": "Training"})
    assert res.status_code == 200
    assert len(res.json()["conflicts"]) == 1

    emily = client.get(f"/api/slots?date={MONDAY}&practitioner=dr-emily").json()["slots"]
    assert "09:30" not in emily and "13:00" in emily
    assert "09:30" in client.get(f"/api/slots?date={MONDAY}&practitioner=dr-sarah").json()["slots"]

    # Whole-clinic holiday
    client.post("/api/admin/blocks", headers=admin, json={"block_date": FRIDAY, "reason": "Holiday"})
    assert client.get(f"/api/slots?date={FRIDAY}").json()["slots"] == []

    block_id = client.get("/api/admin/blocks", headers=admin).json()[0]["id"]
    assert client.delete(f"/api/admin/blocks/{block_id}", headers=admin).status_code == 200


# ---------- Migration of data written by the original version ----------
def test_legacy_rows_are_migrated(tmp_path, monkeypatch):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE appointments (id TEXT PRIMARY KEY, patient_name TEXT NOT NULL, email TEXT NOT NULL,
        phone TEXT NOT NULL, service TEXT NOT NULL, practitioner TEXT, reason TEXT, appointment_date TEXT NOT NULL,
        appointment_time TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT DEFAULT 'confirmed')""")
    conn.execute("INSERT INTO appointments VALUES ('a1','Old','old@x.com','1234567','Initial Assessment (60 min)',"
                 "'Any available',NULL,?, '10:00','2026-01-01T00:00:00','confirmed')", (MONDAY,))
    conn.execute("INSERT INTO appointments VALUES ('a2','Old2','old2@x.com','1234567','Manual Therapy (45 min)',"
                 "'Any available',NULL,?, '10:00','2026-01-01T00:00:00','confirmed')", (MONDAY,))
    conn.commit()
    conn.close()

    monkeypatch.setenv("PYSHIO_DB", str(db))
    import server as srv
    srv = importlib.reload(srv)
    rows = db_rows(srv, "SELECT * FROM appointments ORDER BY id")
    assert [r["service"] for r in rows] == ["initial", "manual"]
    assert all(r["practitioner"] in srv.PRACTITIONERS for r in rows)
    assert rows[0]["practitioner"] != rows[1]["practitioner"]  # not stacked on the same person
    assert all(r["reference"] for r in rows)


# ---------- Emails ----------
def test_booking_sends_confirmation_with_calendar_invite(client, outbox):
    appt = client.post("/api/book", json=booking()).json()
    assert len(outbox) == 1
    mail = outbox[0]
    assert mail["to"] == "asha@example.com"
    assert appt["reference"] in mail["subject"] and appt["reference"] in mail["text"]
    assert "Dr. Sarah Chen" in mail["text"]
    assert "BEGIN:VEVENT" in mail["ics"] and "DTSTART:" + MONDAY.replace("-", "") + "T100000" in mail["ics"]


def test_patient_html_is_escaped_in_email(client, outbox):
    client.post("/api/book", json=booking(patient_name="<b>Asha</b>"))
    assert "<b>Asha</b>" not in outbox[0]["html"]
    assert "&lt;b&gt;Asha&lt;/b&gt;" in outbox[0]["html"]


def test_patient_reschedule_and_cancel_send_emails(client, outbox):
    appt = client.post("/api/book", json=booking()).json()
    creds = {"reference": appt["reference"], "email": "asha@example.com"}
    client.post("/api/my-booking/reschedule", json={**creds, "appointment_date": MONDAY, "appointment_time": "14:00"})
    client.post("/api/my-booking/cancel", json=creds)
    subjects = [m["subject"] for m in outbox]
    assert subjects[1].startswith("Appointment rescheduled") and outbox[1]["ics"]
    assert subjects[2].startswith("Appointment cancelled") and outbox[2]["ics"] is None


def test_admin_changes_email_patient_only_when_relevant(client, admin, outbox):
    a = client.post("/api/book", json=booking()).json()
    b = client.post("/api/book", json=booking(email="b@example.com", appointment_time="11:00")).json()
    outbox.clear()
    client.patch(f"/api/appointments/{a['id']}", headers=admin, json={"notes": "x"})
    client.patch(f"/api/appointments/{a['id']}", headers=admin, json={"status": "completed"})
    assert outbox == []
    client.patch(f"/api/appointments/{b['id']}", headers=admin, json={"appointment_time": "15:00"})
    client.delete(f"/api/appointments/{b['id']}", headers=admin)
    assert [m["subject"].split(" –")[0] for m in outbox] == ["Appointment rescheduled", "Appointment cancelled"]


def test_email_failure_does_not_break_booking(client, server, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", "1")  # nothing listening -> connection error, logged
    res = client.post("/api/book", json=booking())
    assert res.status_code == 200
    assert len(db_rows(server)) == 1


def test_reminders_sent_once_for_appointments_within_24h(server, outbox, monkeypatch):
    import send_reminders
    send_reminders = importlib.reload(send_reminders)
    soon = server.clinic_now() + timedelta(hours=3)
    later = server.clinic_now() + timedelta(days=3)
    conn = sqlite3.connect(server.DB_PATH)
    for appt_id, when, status in [("soon", soon, "confirmed"), ("later", later, "confirmed"), ("gone", soon, "cancelled")]:
        conn.execute(
            "INSERT INTO appointments (id, reference, patient_name, email, phone, service, practitioner, "
            "appointment_date, appointment_time, created_at, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (appt_id, f"PY-{appt_id.upper()}", "P", f"{appt_id}@example.com", "1234567", "followup", "dr-sarah",
             when.date().isoformat(), when.strftime("%H:%M"), "2026-01-01T00:00:00", status))
    conn.commit()
    conn.close()

    assert send_reminders.send_due_reminders() == 1
    assert [m["to"] for m in outbox] == ["soon@example.com"]
    assert outbox[0]["subject"].startswith("Appointment reminder")
    assert send_reminders.send_due_reminders() == 0  # not sent twice

    # A failed send is retried on the next run
    monkeypatch.setattr(server.mailer, "send_email", lambda *a, **k: False)
    db_rows(server)  # noqa
    c = sqlite3.connect(server.DB_PATH)
    c.execute("UPDATE appointments SET reminder_sent_at = NULL WHERE id = 'soon'")
    c.commit()
    c.close()
    assert send_reminders.send_due_reminders() == 0
    assert db_rows(server, "SELECT reminder_sent_at FROM appointments WHERE id = 'soon'")[0]["reminder_sent_at"] is None


# ---------- Hosting (GoDaddy / cPanel) ----------
def test_clinic_time_uses_configured_timezone(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    monkeypatch.setenv("PYSHIO_DB", str(tmp_path / "tz.db"))
    monkeypatch.setenv("PYSHIO_ENV_FILE", str(tmp_path / "no.env"))
    monkeypatch.setenv("PYSHIO_TIMEZONE", "America/Phoenix")
    import server as srv
    srv = importlib.reload(srv)
    expected = datetime.now(ZoneInfo("America/Phoenix")).replace(tzinfo=None)
    assert abs((srv.clinic_now() - expected).total_seconds()) < 5


def test_admin_login_survives_restart_and_multiple_workers(server, client, admin):
    reloaded = importlib.reload(server)  # simulates another Passenger worker / a restart
    assert reloaded.TOKEN_SECRET == server.TOKEN_SECRET
    assert TestClient(reloaded.app).get("/api/admin/stats", headers=admin).status_code == 200


def test_env_file_is_loaded(tmp_path, monkeypatch):
    from envfile import load_env
    env = tmp_path / ".env"
    env.write_text('# comment\nPYSHIO_TEST_A=hello\nPYSHIO_TEST_B="quoted value"\nPYSHIO_TEST_C=from-file\n')
    monkeypatch.delenv("PYSHIO_TEST_A", raising=False)
    monkeypatch.delenv("PYSHIO_TEST_B", raising=False)
    monkeypatch.setenv("PYSHIO_TEST_C", "from-env")
    load_env(env)
    import os
    assert os.environ["PYSHIO_TEST_A"] == "hello"
    assert os.environ["PYSHIO_TEST_B"] == "quoted value"
    assert os.environ["PYSHIO_TEST_C"] == "from-env"  # real env wins


def test_passenger_wsgi_entry_point_serves_app(server, outbox):
    import io
    import json
    from wsgiref.util import setup_testing_defaults
    import passenger_wsgi
    passenger_wsgi = importlib.reload(passenger_wsgi)

    def call(method, path, body=b""):
        environ = {}
        setup_testing_defaults(environ)
        environ.update({"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": "",
                        "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(body)),
                        "wsgi.input": io.BytesIO(body)})
        status = []
        chunks = passenger_wsgi.application(environ, lambda s, h, exc=None: status.append(s))
        body = b"".join(chunks)  # status is only set once the body is consumed
        return status[0], body

    status, page = call("GET", "/")
    assert status.startswith("200") and b"Book Your Appointment" in page
    status, body = call("POST", "/api/book", json.dumps(booking()).encode())
    assert status.startswith("200"), body
    assert json.loads(body)["reference"].startswith("PY-")
    assert len(outbox) == 1  # background email task ran under WSGI too

"""Appointments: booking, online payment, cancel / reschedule, messages, receipts, walk-ins, doctor and admin appointment lists, slot scheduling and reminders.

This file: scheduled jobs (run by jobs.py).
"""
from __future__ import annotations

from datetime import timedelta

from shared import settings
from shared.db import get_db, write_txn


def release_holds() -> None:
    with get_db() as conn:
        with write_txn(conn):
            scheduling.expire_stale_holds(conn)
            medicine_orders.expire_unpaid_orders(conn)


REMINDER_WINDOW = timedelta(hours=24)


def send_due_reminders() -> int:
    now = settings.now()
    horizon = now + REMINDER_WINDOW
    sent = 0
    with get_db() as conn:
        rows = conn.execute(
            appointments_logic.APPOINTMENT_SELECT + " WHERE a.status = 'confirmed' AND a.reminder_sent_at IS NULL "
            "AND a.appointment_date BETWEEN ? AND ?",
            (now.date().isoformat(), horizon.date().isoformat())
        ).fetchall()
        for row in rows:
            if not now < appointments_logic.appointment_start(row) <= horizon:
                continue
            # Claim the row first so overlapping cron runs can't double-send
            claimed = conn.execute(
                "UPDATE appointments SET reminder_sent_at = ? WHERE id = ? AND reminder_sent_at IS NULL",
                (now.isoformat(timespec="seconds"), row["id"])
            ).rowcount
            if not claimed:
                continue
            if appointments_emails.send_appointment_emails("reminder", dict(row)):
                sent += 1
            else:
                # Leave it unclaimed so the next run retries
                conn.execute("UPDATE appointments SET reminder_sent_at = NULL WHERE id = ?", (row["id"],))
    return sent


def send_test_email(address: str) -> bool:
    sample = {
        "id": "test", "reference": "PY-TEST01", "patient_name": "Test Patient", "patient_email": address,
        "patient_phone": "98765 43210", "doctor_name": "Dr. Sample Physio", "doctor_email": address,
        "clinic_name": "Sample Clinic", "address": "1 MG Road", "city": "Pune",
        "service_name": "Consultation", "duration": 30, "fee": 800, "payment_method": "clinic",
        "payment_status": "unpaid", "reason": None,
        "appointment_date": (settings.now() + timedelta(days=1)).date().isoformat(), "appointment_time": "10:00",
    }
    return appointments_emails.send_appointment_emails("reminder", sample) == 1


# Imported last, so slices can depend on each other in any order
from features.appointments import emails as appointments_emails  # noqa: E402
from features.appointments import logic as appointments_logic  # noqa: E402
from features.appointments import scheduling  # noqa: E402
from features.medicine_orders import logic as medicine_orders  # noqa: E402

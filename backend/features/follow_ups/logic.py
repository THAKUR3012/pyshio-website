"""Follow-ups: the doctor sets a follow-up date on a prescription; the patient is reminded and books in one tap.

This file: logic and queries.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from shared import settings
from shared import validators as v

# A follow-up counts as booked once there's a newer appointment with the same doctor in one of these states
BOOKED = ("pending_payment", "requested", "awaiting_patient", "confirmed", "completed")
SHOW_OVERDUE_DAYS = 30


def check_follow_up_date(value: Optional[str]) -> Optional[str]:
    """Request-model validator: an optional date after today, within a year."""
    value = v.optional_text(value)
    if value is None:
        return None
    try:
        d = date.fromisoformat(value)
    except ValueError:
        raise ValueError("Follow-up date must be YYYY-MM-DD")
    today = settings.now().date()
    if not today < d <= today + timedelta(days=365):
        raise ValueError("Follow-up date must be after today and within a year")
    return value


FOLLOW_UP_SELECT = """
    SELECT r.id, r.reference, r.follow_up_on, r.created_at, r.doctor_id, r.patient_id, r.family_member_id,
           du.name AS doctor_name, d.slug AS doctor_slug, a.service_id, pu.name AS patient_name, pu.email AS patient_email,
           fm.name AS member_name, fm.relation AS member_relation,
           (SELECT MIN(n.appointment_date) FROM appointments n WHERE n.patient_id = r.patient_id AND n.doctor_id = r.doctor_id
              AND n.created_at > r.created_at AND n.status IN ('pending_payment', 'requested', 'awaiting_patient',
              'confirmed', 'completed')) AS booked_for
    FROM prescriptions r
    JOIN users du ON du.id = r.doctor_id
    JOIN doctors d ON d.user_id = r.doctor_id
    JOIN users pu ON pu.id = r.patient_id
    LEFT JOIN appointments a ON a.id = r.appointment_id
    LEFT JOIN family_members fm ON fm.id = r.family_member_id
"""


def book_path(row) -> str:
    path = f"/doctors/{row['doctor_slug']}"
    params = []
    if row["service_id"]:
        params.append(f"service={row['service_id']}")
    if row["family_member_id"]:
        params.append(f"for={row['family_member_id']}")
    return path + ("?" + "&".join(params) if params else "")


def follow_up_view(row) -> dict:
    today = settings.now().date()
    due = date.fromisoformat(row["follow_up_on"])
    return {"prescription_id": row["id"], "reference": row["reference"], "follow_up_on": row["follow_up_on"],
            "doctor_name": row["doctor_name"], "doctor_slug": row["doctor_slug"], "days_left": (due - today).days,
            "overdue": due < today, "booked_for": row["booked_for"], "book_path": book_path(row),
            "for_name": row["member_name"], "for_relation": row["member_relation"]}


def my_follow_ups(conn, patient_id: int) -> list:
    since = (settings.now().date() - timedelta(days=SHOW_OVERDUE_DAYS)).isoformat()
    rows = conn.execute(FOLLOW_UP_SELECT + " WHERE r.patient_id = ? AND r.follow_up_on IS NOT NULL AND r.follow_up_on >= ? "
                        "AND r.status = 'active' ORDER BY r.follow_up_on", (patient_id, since)).fetchall()
    return [follow_up_view(r) for r in rows]

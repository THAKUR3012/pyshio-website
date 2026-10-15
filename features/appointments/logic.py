"""Appointments: booking, online payment, cancel / reschedule, messages, receipts, walk-ins, doctor and admin appointment lists, slot scheduling and reminders.

This file: logic and queries.
"""
from __future__ import annotations

import csv
import io
import secrets
from datetime import datetime
from fastapi import BackgroundTasks, HTTPException, Response

from shared import mailer, payments, settings
from shared.common import stamp
from shared.db import get_db, write_txn


REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


APPOINTMENT_SELECT = """
    SELECT a.*, pu.name AS patient_name, pu.email AS patient_email, pu.phone AS patient_phone,
           pu.dob AS patient_dob, pu.sex AS patient_sex,
           du.name AS doctor_name, du.email AS doctor_email, d.slug AS doctor_slug,
           d.clinic_name, d.address, d.city, d.qualification AS doctor_qualification,
           d.registration_no AS doctor_registration_no,
           (SELECT COUNT(*) FROM messages m WHERE m.appointment_id = a.id) AS message_count,
           (SELECT rating FROM reviews rv WHERE rv.appointment_id = a.id) AS my_rating,
           fm.name AS member_name, fm.relation AS member_relation, fm.dob AS member_dob, fm.sex AS member_sex
    FROM appointments a
    LEFT JOIN family_members fm ON fm.id = a.family_member_id
    JOIN users pu ON pu.id = a.patient_id
    JOIN users du ON du.id = a.doctor_id
    JOIN doctors d ON d.user_id = a.doctor_id
"""

APPOINTMENT_FIELDS = [
    "id", "reference", "doctor_id", "patient_id", "service_id", "service_name", "duration", "fee",
    "appointment_date", "appointment_time", "status", "reason", "payment_method", "payment_status",
    "cancelled_by", "created_at", "updated_at", "patient_name", "doctor_name", "doctor_slug",
    "clinic_name", "address", "city", "message_count", "my_rating", "visit_type", "visit_address", "travel_fee",
    "visit_note", "quoted_at", "family_member_id", "member_name", "member_relation",
]


def appointment_view(row, viewer_role: str) -> dict:
    view = {k: row[k] for k in APPOINTMENT_FIELDS}
    view["total_fee"] = row["fee"] + (row["travel_fee"] or 0)
    # Who the visit is for: the account holder, or a family member they booked for
    view["patient_display"] = person_label(row)
    view["video_join"] = join_path(row["id"]) if row["visit_type"] == "video" else None
    if viewer_role in ("doctor", "admin"):
        member = bool(row["member_name"])
        view.update(patient_email=row["patient_email"], patient_phone=row["patient_phone"],
                    patient_dob=row["member_dob"] if member else row["patient_dob"],
                    patient_sex=row["member_sex"] if member else row["patient_sex"], doctor_notes=row["doctor_notes"])
    if viewer_role == "admin":
        view.update(doctor_email=row["doctor_email"], razorpay_order_id=row["razorpay_order_id"],
                    razorpay_payment_id=row["razorpay_payment_id"], refund_id=row["refund_id"])
    return view


def fetch_appointment(conn, appt_id: str):
    return conn.execute(APPOINTMENT_SELECT + " WHERE a.id = ?", (appt_id,)).fetchone()


def appointment_for(conn, appt_id: str, user: dict):
    """Load an appointment the user is allowed to see; 404 otherwise (don't reveal it exists)."""
    row = fetch_appointment(conn, appt_id)
    allowed = row and (
        user["role"] == "admin"
        or (user["role"] == "doctor" and row["doctor_id"] == user["id"])
        or (user["role"] == "patient" and row["patient_id"] == user["id"])
    )
    if not allowed:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return row


def appointment_start(row) -> datetime:
    return datetime.strptime(f"{row['appointment_date']} {row['appointment_time']}", "%Y-%m-%d %H:%M")


def new_reference(conn) -> str:
    while True:
        ref = "PY-" + "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(6))
        if not conn.execute("SELECT 1 FROM appointments WHERE reference = ?", (ref,)).fetchone():
            return ref


def notify(background: BackgroundTasks, kind: str, row, previous=None, **kwargs):
    """Send emails after the response, so a slow mail server never delays the user."""
    background.add_task(appointments_emails.send_appointment_emails, kind, dict(row),
                        dict(previous) if previous else None, **kwargs)


def add_message(conn, appt_id: str, user: dict, body: str) -> None:
    conn.execute("INSERT INTO messages (appointment_id, author_id, author_role, body, created_at) VALUES (?, ?, ?, ?, ?)",
                 (appt_id, user["id"], user["role"], body, stamp()))


VISIT_STATUSES = ("confirmed", "completed", "no_show")


def visit_counts(conn, patient_id: int) -> dict:
    """{doctor_id: (visits so far, last visit date)} counting only visits that have happened."""
    today = settings.now().date().isoformat()
    return {r["doctor_id"]: (r["n"], r["last"]) for r in conn.execute(
        "SELECT doctor_id, COUNT(*) AS n, MAX(appointment_date) AS last FROM appointments WHERE patient_id = ? "
        "AND (status IN ('completed', 'no_show') OR (status = 'confirmed' AND appointment_date <= ?)) GROUP BY doctor_id",
        (patient_id, today))}


# ---------- Booking & payments ----------
def checkout_details(row, user: dict) -> dict:
    return {
        "key_id": settings.RAZORPAY_KEY_ID, "order_id": row["razorpay_order_id"], "amount": row["fee"] * 100,
        "currency": "INR", "name": settings.PLATFORM_NAME,
        "description": f"{row['service_name']} with {row['doctor_name']}",
        "prefill": {"name": user["name"], "email": user["email"], "contact": user["phone"] or ""},
        "hold_minutes": settings.PAYMENT_HOLD_MINUTES,
    }


def issue_refund(appt_id: str) -> str:
    """Refund an online payment in full; returns a sentence for the patient."""
    with get_db() as conn:
        # Claim the refund first so two callers can't both refund the same payment
        claimed = conn.execute("UPDATE appointments SET refund_id = 'pending', updated_at = ? WHERE id = ? "
                               "AND refund_id IS NULL AND razorpay_payment_id IS NOT NULL "
                               "AND payment_status IN ('paid', 'refund_failed')", (stamp(), appt_id)).rowcount
        row = conn.execute("SELECT * FROM appointments WHERE id = ?", (appt_id,)).fetchone()
        if not claimed:
            return "A refund for this booking is already being processed." if row["payment_status"] != "refunded" \
                else f"Your refund of {mailer.rupees(row['fee'])} has already been issued."
        try:
            result = payments.refund(row["razorpay_payment_id"], row["fee"])
        except payments.PaymentError:
            conn.execute("UPDATE appointments SET payment_status = 'refund_failed', refund_id = NULL, updated_at = ? "
                         "WHERE id = ?", (stamp(), appt_id))
            return (f"Your refund of {mailer.rupees(row['fee'])} could not be processed automatically. "
                    f"Our team will refund it manually.")
        conn.execute("UPDATE appointments SET payment_status = 'refunded', refund_id = ?, updated_at = ? WHERE id = ?",
                     (result.get("id"), stamp(), appt_id))
    return f"A full refund of {mailer.rupees(row['fee'])} has been issued. It usually reaches you in 5–7 working days."


def mark_paid(appt_id: str, payment_id: str, background: BackgroundTasks) -> str:
    """Record a successful payment. Confirms the booking, or refunds if the slot was lost meanwhile."""
    with get_db() as conn:
        with write_txn(conn):
            row = conn.execute("SELECT * FROM appointments WHERE id = ?", (appt_id,)).fetchone()
            if row["payment_status"] in ("paid", "refunded", "refund_failed"):
                return row["status"]  # already handled (verify and webhook can both arrive)
            conn.execute("UPDATE appointments SET payment_status = 'paid', razorpay_payment_id = ?, updated_at = ? "
                         "WHERE id = ?", (payment_id, stamp(), appt_id))
            outcome = "refund"
            if row["status"] in ("pending_payment", "expired"):
                doctor = get_doctor(conn, row["doctor_id"])
                try:
                    scheduling.check_slot(conn, doctor, row["appointment_date"], row["appointment_time"],
                                          row["duration"], exclude_id=appt_id)
                    conn.execute("UPDATE appointments SET status = 'confirmed' WHERE id = ?", (appt_id,))
                    outcome = "confirmed"
                except HTTPException:
                    conn.execute("UPDATE appointments SET status = 'cancelled', cancelled_by = 'system' WHERE id = ?",
                                 (appt_id,))
        if outcome == "confirmed":
            notify(background, "confirmed", fetch_appointment(conn, appt_id))
            return "confirmed"
    note = "Your payment arrived after the slot was taken, so the booking couldn't be completed. " + issue_refund(appt_id)
    with get_db() as conn:
        notify(background, "cancelled", fetch_appointment(conn, appt_id), by="system", refund_note=note)
    return "refunded"


# ---------- Appointments (patient, doctor, admin) ----------
def appointment_query(where: str, params: list, date_from=None, date_to=None, status=None, q=None, doctor_id=None):
    sql = APPOINTMENT_SELECT + f" WHERE {where}"
    params = list(params)
    if date_from:
        sql += " AND a.appointment_date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND a.appointment_date <= ?"
        params.append(date_to)
    if status:
        sql += " AND a.status = ?"
        params.append(status)
    else:
        sql += " AND a.status NOT IN ('pending_payment', 'expired')"
    if doctor_id:
        sql += " AND a.doctor_id = ?"
        params.append(doctor_id)
    if q:
        like = f"%{q.strip().lower()}%"
        sql += (" AND (lower(pu.name) LIKE ? OR lower(pu.email) LIKE ? OR pu.phone LIKE ? "
                "OR lower(a.reference) LIKE ? OR lower(du.name) LIKE ?)")
        params += [like] * 5
    return sql + " ORDER BY a.appointment_date, a.appointment_time", params


def month_bounds():
    today = settings.now().date()
    return today.replace(day=1).isoformat(), today.isoformat()


def csv_response(rows, viewer_role: str, filename: str) -> Response:
    fields = ["reference", "appointment_date", "appointment_time", "duration", "patient_name", "patient_email",
              "patient_phone", "doctor_name", "service_name", "fee", "payment_method", "payment_status", "status",
              "reason", "doctor_notes", "created_at"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Sr No"] + fields)
    for i, r in enumerate(rows, 1):
        view = appointment_view(r, viewer_role)
        cells = []
        for f in fields:
            text = "" if view.get(f) is None else str(view.get(f))
            # Stop spreadsheet apps treating user-entered text as a formula
            cells.append("'" + text if text[:1] in ("=", "+", "-", "@") else text)
        writer.writerow([i] + cells)
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def wind_down_appointments(where: str, params: tuple, reason: str, background: BackgroundTasks) -> int:
    """Cancel upcoming appointments matching `where` (admin action), refund online fees and tell both sides."""
    with get_db() as conn:
        with write_txn(conn):
            rows = conn.execute(f"SELECT * FROM appointments WHERE {where} AND status IN ('confirmed', 'pending_payment', 'requested', 'awaiting_patient') "
                                "AND appointment_date >= ?", (*params, settings.now().date().isoformat())).fetchall()
            rows = [r for r in rows if r["status"] == "pending_payment" or appointment_start(r) > settings.now()]
            for r in rows:
                conn.execute("UPDATE appointments SET status = 'cancelled', cancelled_by = 'admin', updated_at = ? "
                             "WHERE id = ?", (stamp(), r["id"]))
    for r in rows:
        if r["status"] == "pending_payment":
            continue
        note = issue_refund(r["id"]) if r["payment_status"] == "paid" else None
        with get_db() as conn:
            notify(background, "cancelled", fetch_appointment(conn, r["id"]), by="admin", message=reason, refund_note=note)
    return len(rows)


# Imported last, so slices can depend on each other in any order
from features.doctors.logic import get_doctor  # noqa: E402
from features.family.logic import person_label  # noqa: E402
from features.video_consults.logic import join_path  # noqa: E402
from features.appointments import emails as appointments_emails  # noqa: E402
from features.appointments import scheduling  # noqa: E402

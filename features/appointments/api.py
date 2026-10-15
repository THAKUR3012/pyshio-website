"""Appointments: booking, online payment, cancel / reschedule, messages, receipts, walk-ins, doctor and admin appointment lists, slot scheduling and reminders.

This file: HTTP endpoints and request models.
"""
import secrets
import uuid
from datetime import timedelta
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional

from shared import auth, payments, settings, validators as v
from shared.auth import admin_user, doctor_user
from shared.common import audit, hold_cutoff, stamp
from shared.db import get_db, write_txn


router = APIRouter()

VISIT_LABELS = {"home": " (home visit)", "video": " (video consultation)"}


class WalkInBooking(BaseModel):
    """A doctor books a phone or walk-in patient who may not have an account yet."""
    name: str
    phone: str
    email: Optional[str] = None
    service_id: int
    appointment_date: str
    appointment_time: str
    reason: Optional[str] = Field(default=None, max_length=1000)

    _name = field_validator("name")(v.name)
    _phone = field_validator("phone")(v.phone)
    _reason = field_validator("reason")(v.optional_text)

    @field_validator("email")
    @classmethod
    def _email(cls, value):
        value = v.optional_text(value)
        return v.email(value) if value else None


class BookingRequest(BaseModel):
    doctor_slug: str
    service_id: int
    appointment_date: str
    appointment_time: str
    reason: Optional[str] = Field(default=None, max_length=1000)
    payment_method: Literal["clinic", "online"] = "clinic"
    visit_type: Literal["clinic", "home", "video"] = "clinic"
    visit_address: Optional[str] = Field(default=None, max_length=300)
    family_member_id: Optional[int] = None  # booking for a family member (features/family)

    _reason = field_validator("reason", "visit_address")(v.optional_text)


class PaymentVerify(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class CancelRequest(BaseModel):
    message: Optional[str] = Field(default=None, max_length=1000)

    _message = field_validator("message")(v.optional_text)


class RescheduleRequest(CancelRequest):
    appointment_date: str
    appointment_time: str


class MessageIn(BaseModel):
    body: str = Field(max_length=2000)

    _body = field_validator("body")(v.required_text("Message", 2000))


class AppointmentUpdate(BaseModel):
    status: Optional[Literal["completed", "no_show"]] = None
    doctor_notes: Optional[str] = Field(default=None, max_length=5000)
    payment_status: Optional[Literal["paid"]] = None


@router.post("/api/appointments")
def book(data: BookingRequest, background: BackgroundTasks, user: dict = Depends(auth.require("patient"))):
    with get_db() as conn:
        doctor = bookable_doctor(conn, data.doctor_slug)
        service = conn.execute("SELECT * FROM services WHERE id = ? AND doctor_id = ? AND active = 1",
                               (data.service_id, doctor["user_id"])).fetchone()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")
        member = member_for(conn, user["id"], data.family_member_id)
        home = data.visit_type == "home"
        if home:
            return book_home_visit(conn, doctor, service, data, background, user)
        video = data.visit_type == "video"
        fee = service["fee"]
        if video:
            check_offers_video(doctor)
            fee = consult_fee(doctor, service)
        online = data.payment_method == "online"
        if online and not (doctor_public(doctor)["accepts_online_payment"] and fee > 0):
            raise HTTPException(status_code=400, detail="This doctor doesn't accept online payment for this service")
        if not online and not doctor["accepts_clinic_payment"]:
            raise HTTPException(status_code=400, detail="This doctor only accepts online payment")

        appt_id = str(uuid.uuid4())
        with write_txn(conn):
            scheduling.expire_stale_holds(conn)
            scheduling.check_slot(conn, doctor, data.appointment_date, data.appointment_time, service["duration"])
            conn.execute(
                """INSERT INTO appointments (id, reference, doctor_id, patient_id, service_id, service_name, duration,
                   fee, appointment_date, appointment_time, status, reason, payment_method, payment_status,
                   created_at, updated_at, visit_type, family_member_id, video_room)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (appt_id, new_reference(conn), doctor["user_id"], user["id"], service["id"], service["name"],
                 service["duration"], fee, data.appointment_date, data.appointment_time,
                 "pending_payment" if online else "confirmed", data.reason, data.payment_method,
                 "pending" if online else "unpaid", stamp(), stamp(), data.visit_type,
                 member["id"] if member else None, new_room() if video else None))

        if not online:
            row = fetch_appointment(conn, appt_id)
            notify(background, "confirmed", row)
            return {"appointment": appointment_view(row, "patient"), "payment": None}

        # Talk to Razorpay outside the write lock; the pending row already holds the slot
        try:
            order = payments.create_order(fee, appt_id, {"appointment_id": appt_id})
        except payments.PaymentError as e:
            conn.execute("UPDATE appointments SET status = 'expired', payment_status = 'unpaid', updated_at = ? "
                         "WHERE id = ?", (stamp(), appt_id))
            raise HTTPException(status_code=502, detail=f"Couldn't start online payment ({e}). Try again or pay at clinic.")
        conn.execute("UPDATE appointments SET razorpay_order_id = ? WHERE id = ?", (order["id"], appt_id))
        row = fetch_appointment(conn, appt_id)
        return {"appointment": appointment_view(row, "patient"), "payment": checkout_details(row, user)}


@router.get("/api/appointments/{appt_id}/payment")
def resume_payment(appt_id: str, user: dict = Depends(auth.require("patient"))):
    """Reopen checkout for a booking whose payment window is still open."""
    with get_db() as conn:
        row = appointment_for(conn, appt_id, user)
        if row["status"] != "pending_payment" or row["created_at"] <= scheduling.hold_cutoff():
            raise HTTPException(status_code=400, detail="This payment window has closed. Please book again.")
        return checkout_details(row, user)


@router.post("/api/appointments/{appt_id}/verify-payment")
def verify_payment(appt_id: str, data: PaymentVerify, background: BackgroundTasks,
                   user: dict = Depends(auth.require("patient"))):
    with get_db() as conn:
        row = appointment_for(conn, appt_id, user)
    if row["razorpay_order_id"] != data.razorpay_order_id or not payments.verify_payment_signature(
            data.razorpay_order_id, data.razorpay_payment_id, data.razorpay_signature):
        raise HTTPException(status_code=400, detail="Payment could not be verified")
    outcome = mark_paid(appt_id, data.razorpay_payment_id, background)
    with get_db() as conn:
        return {"outcome": outcome, "appointment": appointment_view(fetch_appointment(conn, appt_id), "patient")}


@router.get("/api/my/appointments")
def my_appointments(user: dict = Depends(auth.require("patient"))):
    with get_db() as conn:
        rows = conn.execute(APPOINTMENT_SELECT + " WHERE a.patient_id = ? AND a.status != 'expired' "
                            "AND NOT (a.status = 'pending_payment' AND a.created_at <= ?) "
                            "ORDER BY a.appointment_date DESC, a.appointment_time DESC",
                            (user["id"], hold_cutoff())).fetchall()
    return [appointment_view(r, "patient") for r in rows]


@router.get("/api/appointments/{appt_id}")
def get_appointment(appt_id: str, user: dict = Depends(auth.require())):
    with get_db() as conn:
        row = appointment_for(conn, appt_id, user)
        msgs = conn.execute("SELECT m.author_role, m.body, m.created_at, u.name AS author_name FROM messages m "
                            "JOIN users u ON u.id = m.author_id WHERE m.appointment_id = ? ORDER BY m.id",
                            (appt_id,)).fetchall()
    return {**appointment_view(row, user["role"]), "messages": [dict(m) for m in msgs]}


@router.get("/api/appointments/{appt_id}/receipt")
def appointment_receipt(appt_id: str, user: dict = Depends(auth.require())):
    """Printable receipt for a consultation fee that has been paid (online or at the clinic)."""
    with get_db() as conn:
        row = appointment_for(conn, appt_id, user)
    if row["payment_status"] not in ("paid", "refunded", "not_refundable"):
        raise HTTPException(status_code=400, detail="A receipt is available once the fee has been paid")
    return {
        "kind": "Consultation receipt", "reference": row["reference"], "date": row["updated_at"][:10],
        "seller": {"name": row["doctor_name"], "detail": f"{row['doctor_qualification']} · Reg. {row['doctor_registration_no']}",
                   "address": f"{row['clinic_name']}, {row['address']}, {row['city']}", "gstin": None},
        "buyer": {"name": row["patient_name"], "phone": row["patient_phone"]},
        "lines": [{"description": f"{row['service_name']}{VISIT_LABELS.get(row['visit_type'], '')} on "
                                  f"{row['appointment_date']} {row['appointment_time']}",
                   "quantity": 1, "unit_paise": row["fee"] * 100, "mrp_paise": None, "amount_paise": row["fee"] * 100}],
        "delivery_fee_paise": (row["travel_fee"] or 0) * 100, "total_paise": (row["fee"] + (row["travel_fee"] or 0)) * 100,
        "payment": {"method": "Online" if row["payment_method"] == "online" else "At clinic",
                    "status": row["payment_status"], "reference": row["razorpay_payment_id"]},
    }


@router.post("/api/appointments/{appt_id}/cancel")
def cancel(appt_id: str, data: CancelRequest, background: BackgroundTasks, user: dict = Depends(auth.require())):
    refund = False
    with get_db() as conn:
        with write_txn(conn):
            row = appointment_for(conn, appt_id, user)
            if row["status"] == "pending_payment":
                conn.execute("UPDATE appointments SET status = 'cancelled', payment_status = 'unpaid', cancelled_by = ?, "
                             "updated_at = ? WHERE id = ?", (user["role"], stamp(), appt_id))
                return {"appointment": appointment_view(fetch_appointment(conn, appt_id), user["role"]),
                        "refund_note": None}
            if row["status"] in ("requested", "awaiting_patient"):  # a home-visit request declined by either side
                conn.execute("UPDATE appointments SET status = 'cancelled', cancelled_by = ?, updated_at = ? WHERE id = ?",
                             (user["role"], stamp(), appt_id))
                if data.message:
                    add_message(conn, appt_id, user, data.message)
                updated = fetch_appointment(conn, appt_id)
                notify(background, "cancelled", updated, by=user["role"], message=data.message)
                return {"appointment": appointment_view(updated, user["role"]), "refund_note": None}
            if row["status"] != "confirmed":
                raise HTTPException(status_code=400, detail=f"This appointment is already {row['status'].replace('_', ' ')}")
            start = appointment_start(row)
            if start <= settings.now() and user["role"] != "admin":
                raise HTTPException(status_code=400, detail="This appointment has already started")
            payment_status = row["payment_status"]
            if payment_status == "paid":
                hours_before = (start - settings.now()).total_seconds() / 3600
                if user["role"] in ("doctor", "admin") or hours_before >= settings.REFUND_CUTOFF_HOURS:
                    refund = True
                else:
                    payment_status = "not_refundable"
            conn.execute("UPDATE appointments SET status = 'cancelled', cancelled_by = ?, payment_status = ?, "
                         "updated_at = ? WHERE id = ?", (user["role"], payment_status, stamp(), appt_id))
            if data.message:
                add_message(conn, appt_id, user, data.message)
    note = issue_refund(appt_id) if refund else None
    if row["payment_status"] == "paid" and not refund:
        note = (f"Cancelled less than {settings.REFUND_CUTOFF_HOURS} hours before the appointment, "
                f"so the fee is not refundable.")
    with get_db() as conn:
        updated = fetch_appointment(conn, appt_id)
    notify(background, "cancelled", updated, by=user["role"], message=data.message, refund_note=note)
    return {"appointment": appointment_view(updated, user["role"]), "refund_note": note}


@router.post("/api/appointments/{appt_id}/reschedule")
def reschedule(appt_id: str, data: RescheduleRequest, background: BackgroundTasks,
               user: dict = Depends(auth.require())):
    with get_db() as conn:
        with write_txn(conn):
            previous = appointment_for(conn, appt_id, user)
            if previous["status"] != "confirmed":
                raise HTTPException(status_code=400, detail="Only confirmed appointments can be rescheduled")
            if appointment_start(previous) <= settings.now():
                raise HTTPException(status_code=400, detail="This appointment has already started")
            hours_before = (appointment_start(previous) - settings.now()).total_seconds() / 3600
            if user["role"] == "patient" and previous["payment_status"] == "paid" \
                    and hours_before < settings.REFUND_CUTOFF_HOURS:
                raise HTTPException(status_code=400, detail=(
                    f"Paid appointments can't be moved online within {settings.REFUND_CUTOFF_HOURS} hours of the visit. "
                    f"Please message or call the clinic."))
            doctor = get_doctor(conn, previous["doctor_id"])
            scheduling.expire_stale_holds(conn)
            scheduling.check_slot(conn, doctor, data.appointment_date, data.appointment_time,
                                  previous["duration"], exclude_id=appt_id)
            conn.execute("UPDATE appointments SET appointment_date = ?, appointment_time = ?, reminder_sent_at = NULL, "
                         "updated_at = ? WHERE id = ?", (data.appointment_date, data.appointment_time, stamp(), appt_id))
            if data.message:
                add_message(conn, appt_id, user, data.message)
        updated = fetch_appointment(conn, appt_id)
    notify(background, "rescheduled", updated, previous=previous, by=user["role"], message=data.message)
    return {"appointment": appointment_view(updated, user["role"])}


@router.post("/api/appointments/{appt_id}/messages")
def post_message(appt_id: str, data: MessageIn, background: BackgroundTasks,
                 user: dict = Depends(auth.require("patient", "doctor"))):
    with get_db() as conn:
        row = appointment_for(conn, appt_id, user)
        add_message(conn, appt_id, user, data.body)
    notify(background, "message", row, by=user["role"], message=data.body)
    return {"ok": True}


@router.patch("/api/appointments/{appt_id}")
def update_appointment(appt_id: str, data: AppointmentUpdate, user: dict = Depends(auth.require("doctor", "admin"))):
    changes = data.model_dump(exclude_unset=True)
    with get_db() as conn:
        with write_txn(conn):
            row = appointment_for(conn, appt_id, user)
            if data.status:
                if row["status"] not in ("confirmed", "completed", "no_show"):
                    raise HTTPException(status_code=400, detail="Only confirmed appointments can be marked")
                if appointment_start(row) > settings.now():
                    raise HTTPException(status_code=400, detail="You can mark this once the appointment time has passed")
            if data.payment_status and (row["payment_method"] != "clinic" or row["status"] == "cancelled"):
                raise HTTPException(status_code=400, detail="Only pay-at-clinic appointments can be marked paid")
            if "doctor_notes" in changes:
                changes["doctor_notes"] = v.optional_text(data.doctor_notes)
            changes = {k: val for k, val in changes.items() if k == "doctor_notes" or val is not None}
            if changes:
                changes["updated_at"] = stamp()
                conn.execute(f"UPDATE appointments SET {', '.join(f'{k} = ?' for k in changes)} WHERE id = ?",
                             (*changes.values(), appt_id))
        return appointment_view(fetch_appointment(conn, appt_id), user["role"])


@router.post("/api/doctor/appointments")
def walk_in_booking(data: WalkInBooking, background: BackgroundTasks, user: dict = Depends(doctor_user)):
    """Book a patient who phoned or walked in. Creates their account if needed and invites them to set a password."""
    invite = None
    with get_db() as conn:
        doctor = get_doctor(conn, user["id"])
        if doctor["status"] != "approved":
            raise HTTPException(status_code=403, detail="Your profile must be approved before taking bookings")
        service = conn.execute("SELECT * FROM services WHERE id = ? AND doctor_id = ? AND active = 1",
                               (data.service_id, user["id"])).fetchone()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")
        appt_id = str(uuid.uuid4())
        with write_txn(conn):
            patient = conn.execute("SELECT * FROM users WHERE email = ?", (data.email,)).fetchone() if data.email else None
            if patient and patient["role"] != "patient":
                raise HTTPException(status_code=409, detail="That email belongs to a non-patient account")
            if not patient:
                email = data.email or f"walkin-{uuid.uuid4().hex[:12]}@walkin.invalid"
                cur = conn.execute("INSERT INTO users (role, name, email, phone, password_hash, created_at) "
                                   "VALUES ('patient', ?, ?, ?, ?, ?)",
                                   (data.name, email, data.phone, auth.hash_password(secrets.token_urlsafe(16)), stamp()))
                patient = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
                invite = patient if data.email else None
            scheduling.expire_stale_holds(conn)
            scheduling.check_slot(conn, doctor, data.appointment_date, data.appointment_time, service["duration"])
            conn.execute(
                """INSERT INTO appointments (id, reference, doctor_id, patient_id, service_id, service_name, duration,
                   fee, appointment_date, appointment_time, status, reason, payment_method, payment_status,
                   created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, 'clinic', 'unpaid', ?, ?)""",
                (appt_id, new_reference(conn), user["id"], patient["id"], service["id"], service["name"],
                 service["duration"], service["fee"], data.appointment_date, data.appointment_time, data.reason,
                 stamp(), stamp()))
        row = fetch_appointment(conn, appt_id)
    if invite:
        background.add_task(accounts_emails.send_invite, invite["name"], invite["email"], user["name"],
                            auth.create_reset_token(invite["id"]))
    notify(background, "confirmed", row, by="doctor")
    return {"appointment": appointment_view(row, "doctor"), "account_created": bool(invite) or not data.email}


@router.get("/api/doctor/appointments")
def doctor_appointments(date_from: Optional[str] = None, date_to: Optional[str] = None, status: Optional[str] = None,
                        q: Optional[str] = None, user: dict = Depends(doctor_user)):
    sql, params = appointment_query("a.doctor_id = ?", [user["id"]], date_from, date_to, status, q)
    with get_db() as conn:
        return [appointment_view(r, "doctor") for r in conn.execute(sql, params)]


@router.get("/api/doctor/stats")
def doctor_stats(user: dict = Depends(doctor_user)):
    today = settings.now().date()
    m_start, m_end = month_bounds()
    with get_db() as conn:
        def one(sql, *params):
            return conn.execute(sql, params).fetchone()[0]
        return {
            "today": one("SELECT COUNT(*) FROM appointments WHERE doctor_id = ? AND appointment_date = ? "
                         "AND status IN ('confirmed','completed','no_show')", user["id"], today.isoformat()),
            "upcoming_7_days": one("SELECT COUNT(*) FROM appointments WHERE doctor_id = ? AND status = 'confirmed' "
                                   "AND appointment_date BETWEEN ? AND ?", user["id"], today.isoformat(),
                                   (today + timedelta(days=7)).isoformat()),
            "completed_month": one("SELECT COUNT(*) FROM appointments WHERE doctor_id = ? AND status = 'completed' "
                                   "AND appointment_date BETWEEN ? AND ?", user["id"], m_start, m_end),
            "no_show_month": one("SELECT COUNT(*) FROM appointments WHERE doctor_id = ? AND status = 'no_show' "
                                 "AND appointment_date BETWEEN ? AND ?", user["id"], m_start, m_end),
            "revenue_month": one("SELECT COALESCE(SUM(fee), 0) FROM appointments WHERE doctor_id = ? "
                                 "AND payment_status = 'paid' AND appointment_date BETWEEN ? AND ?",
                                 user["id"], m_start, m_end),
            "patients": one("SELECT COUNT(DISTINCT patient_id) FROM appointments WHERE doctor_id = ? "
                            "AND status NOT IN ('pending_payment','expired')", user["id"]),
        }


@router.get("/api/doctor/export.csv")
def doctor_export(date_from: Optional[str] = None, date_to: Optional[str] = None, status: Optional[str] = None,
                  q: Optional[str] = None, user: dict = Depends(doctor_user)):
    sql, params = appointment_query("a.doctor_id = ?", [user["id"]], date_from, date_to, status, q)
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return csv_response(rows, "doctor", f"appointments-{settings.now().date()}.csv")


@router.get("/api/admin/appointments")
def admin_appointments(date_from: Optional[str] = None, date_to: Optional[str] = None, status: Optional[str] = None,
                       q: Optional[str] = None, doctor_id: Optional[int] = None, user: dict = Depends(admin_user)):
    sql, params = appointment_query("1 = 1", [], date_from, date_to, status, q, doctor_id)
    with get_db() as conn:
        return [appointment_view(r, "admin") for r in conn.execute(sql + " LIMIT 1000", params)]


@router.get("/api/admin/export.csv")
def admin_export(date_from: Optional[str] = None, date_to: Optional[str] = None, status: Optional[str] = None,
                 q: Optional[str] = None, doctor_id: Optional[int] = None, user: dict = Depends(admin_user)):
    sql, params = appointment_query("1 = 1", [], date_from, date_to, status, q, doctor_id)
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return csv_response(rows, "admin", f"pyshio-appointments-{settings.now().date()}.csv")


@router.post("/api/admin/appointments/{appt_id}/retry-refund")
def admin_retry_refund(appt_id: str, user: dict = Depends(admin_user)):
    with get_db() as conn:
        row = appointment_for(conn, appt_id, user)
    if row["payment_status"] != "refund_failed":
        raise HTTPException(status_code=400, detail="No failed refund on this appointment")
    note = issue_refund(appt_id)
    with get_db() as conn:
        audit(conn, user["id"], "appointment.retry_refund", f"appointment:{appt_id}", note)
        updated = fetch_appointment(conn, appt_id)
    if updated["payment_status"] == "refunded":
        appointments_emails.send_refund_done(updated["patient_name"], updated["patient_email"], updated["reference"], note)
    return {"note": note, "appointment": appointment_view(updated, "admin")}


# Imported last, so slices can depend on each other in any order
from features.appointments.logic import add_message, appointment_for, appointment_query, APPOINTMENT_SELECT, appointment_start, appointment_view, checkout_details, csv_response, fetch_appointment, issue_refund, mark_paid, month_bounds, new_reference, notify  # noqa: E402
from features.doctors.logic import bookable_doctor, doctor_public, get_doctor  # noqa: E402
from features.home_visits.logic import book_home_visit  # noqa: E402
from features.family.logic import member_for  # noqa: E402
from features.video_consults.logic import check_offers_video, consult_fee, new_room  # noqa: E402
from features.accounts import emails as accounts_emails  # noqa: E402
from features.appointments import emails as appointments_emails  # noqa: E402
from features.appointments import scheduling  # noqa: E402

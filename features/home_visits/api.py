"""Home visits: the patient asks, the doctor sends charges and time, the patient accepts.

This file: HTTP endpoints and request models.
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import Optional

from shared import auth, settings, validators as v
from shared.common import stamp
from shared.db import get_db, write_txn


router = APIRouter()


class HomeVisitQuote(BaseModel):
    """The doctor's answer to a home-visit request: charges, final time and anything the patient should know."""
    fee: int = Field(ge=0, le=100000)
    travel_fee: int = Field(default=0, ge=0, le=20000)
    appointment_date: str
    appointment_time: str
    duration: Optional[int] = Field(default=None, ge=10, le=480)
    note: Optional[str] = Field(default=None, max_length=1000)

    _date = field_validator("appointment_date")(v.iso_date)
    _time = field_validator("appointment_time")(v.hhmm)
    _note = field_validator("note")(v.optional_text)


@router.post("/api/doctor/appointments/{appt_id}/home-visit")
def quote_home_visit(appt_id: str, data: HomeVisitQuote, background: BackgroundTasks,
                     user: dict = Depends(auth.require("doctor"))):
    """The doctor sends the charges, the final time and a note; the patient then accepts or declines."""
    with get_db() as conn:
        with write_txn(conn):
            row = appointment_for(conn, appt_id, user)
            if row["visit_type"] != "home" or row["status"] not in ("requested", "awaiting_patient"):
                raise HTTPException(status_code=400, detail="Only open home-visit requests can be updated")
            duration = data.duration or row["duration"]
            doctor = get_doctor(conn, user["id"])
            scheduling.expire_stale_holds(conn)
            scheduling.check_slot(conn, doctor, data.appointment_date, data.appointment_time, duration, exclude_id=appt_id)
            conn.execute("UPDATE appointments SET fee = ?, travel_fee = ?, appointment_date = ?, appointment_time = ?, duration = ?, "
                         "visit_note = ?, status = 'awaiting_patient', quoted_at = ?, updated_at = ? WHERE id = ?",
                         (data.fee, data.travel_fee, data.appointment_date, data.appointment_time, duration, data.note, stamp(),
                          stamp(), appt_id))
            if data.note:
                add_message(conn, appt_id, user, data.note)
        updated = fetch_appointment(conn, appt_id)
    background.add_task(home_visits_emails.send_home_visit_quote, dict(updated))
    return appointment_view(updated, "doctor")


@router.post("/api/appointments/{appt_id}/accept-visit")
def accept_home_visit(appt_id: str, background: BackgroundTasks, user: dict = Depends(auth.require("patient"))):
    with get_db() as conn:
        with write_txn(conn):
            row = appointment_for(conn, appt_id, user)
            if row["status"] != "awaiting_patient":
                raise HTTPException(status_code=400, detail="There are no charges waiting for your answer")
            if appointment_start(row) <= settings.now():
                raise HTTPException(status_code=400, detail="This visit time has passed. Please book again.")
            conn.execute("UPDATE appointments SET status = 'confirmed', updated_at = ? WHERE id = ?", (stamp(), appt_id))
        updated = fetch_appointment(conn, appt_id)
    notify(background, "confirmed", updated)
    return {"appointment": appointment_view(updated, "patient")}


# Imported last, so slices can depend on each other in any order
from features.appointments.logic import add_message, appointment_for, appointment_start, appointment_view, fetch_appointment, notify  # noqa: E402
from features.doctors.logic import get_doctor  # noqa: E402
from features.appointments import scheduling  # noqa: E402
from features.home_visits import emails as home_visits_emails  # noqa: E402

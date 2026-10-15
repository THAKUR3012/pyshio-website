"""Medicine reminders: today's doses from prescriptions and own medicines, taken / skipped log and adherence.

This file: HTTP endpoints and request models.
"""
import json
from datetime import date, timedelta
from typing import Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator

from shared import settings
from shared import validators as v
from shared.auth import doctor_user, patient_user
from shared.common import stamp
from shared.db import get_db


router = APIRouter()

Slot = Literal["morning", "afternoon", "evening", "night"]
BACKDATE_DAYS = 7


class DoseIn(BaseModel):
    source: Literal["course", "rx", "own"]
    source_id: str
    dose_date: str
    slot: Slot
    status: Literal["taken", "skipped", "pending"]

    _date = field_validator("dose_date")(v.iso_date)


class RemindersIn(BaseModel):
    times: Dict[Slot, str]
    email_digest: bool = False

    @field_validator("times")
    @classmethod
    def _times(cls, value):
        return {k: v.hhmm(t) for k, t in value.items()}


def _me(conn, user):
    return conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()


def _day(value: Optional[str]) -> date:
    if not value:
        return settings.now().date()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Dates must be YYYY-MM-DD")


@router.get("/api/my/doses")
def my_doses(day: Optional[str] = Query(default=None, alias="date"), user: dict = Depends(patient_user)):
    with get_db() as conn:
        return day_schedule(conn, _me(conn, user), _day(day))


@router.post("/api/my/doses")
def log_dose(data: DoseIn, user: dict = Depends(patient_user)):
    day = _day(data.dose_date)
    today = settings.now().date()
    if not today - timedelta(days=BACKDATE_DAYS) <= day <= today:
        raise HTTPException(status_code=400, detail=f"You can mark doses for today and the last {BACKDATE_DAYS} days")
    with get_db() as conn:
        me = _me(conn, user)
        if not is_scheduled(conn, me, data.source, data.source_id, day, data.slot):
            raise HTTPException(status_code=404, detail="That dose isn't on your schedule")
        key = (user["id"], data.source, data.source_id, data.dose_date, data.slot)
        if data.status == "pending":
            conn.execute("DELETE FROM dose_logs WHERE patient_id = ? AND source = ? AND source_id = ? AND dose_date = ? "
                         "AND slot = ?", key)
        else:
            name = next((d["name"] for g in day_schedule(conn, me, day)["groups"] for d in g["doses"]
                         if d["source"] == data.source and d["source_id"] == data.source_id), "Medicine")
            conn.execute("INSERT INTO dose_logs (patient_id, source, source_id, dose_date, slot, name, status, logged_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (patient_id, source, source_id, dose_date, slot) "
                         "DO UPDATE SET status = excluded.status, logged_at = excluded.logged_at",
                         (*key, name, data.status, stamp()))
        return day_schedule(conn, me, day)


@router.get("/api/my/adherence")
def my_adherence(days: int = Query(default=30, ge=7, le=90), user: dict = Depends(patient_user)):
    with get_db() as conn:
        return adherence(conn, _me(conn, user), days)


@router.get("/api/my/reminders")
def get_reminders(user: dict = Depends(patient_user)):
    with get_db() as conn:
        me = _me(conn, user)
    return {"times": times_for(me), "email_digest": bool(me["dose_email_digest"]), "slots": SLOTS}


@router.put("/api/my/reminders")
def save_reminders(data: RemindersIn, user: dict = Depends(patient_user)):
    with get_db() as conn:
        conn.execute("UPDATE users SET remind_times = ?, dose_email_digest = ? WHERE id = ?",
                     (json.dumps(data.times), int(data.email_digest), user["id"]))
        me = _me(conn, user)
    return {"times": times_for(me), "email_digest": bool(me["dose_email_digest"]), "slots": SLOTS}


@router.get("/api/doctor/patients/{patient_id}/adherence")
def patient_adherence(patient_id: int, user: dict = Depends(doctor_user)):
    """How regularly a patient of this doctor takes their medicines (last 30 days, as they record it)."""
    with get_db() as conn:
        if not doctor_sees_patient(conn, user["id"], patient_id):
            raise HTTPException(status_code=404, detail="Patient not found")
        patient = conn.execute("SELECT * FROM users WHERE id = ?", (patient_id,)).fetchone()
        result = adherence(conn, patient, 30)
        # Without any recorded doses 0 % only means the patient doesn't use reminders
        result["tracking"] = bool(conn.execute("SELECT 1 FROM dose_logs WHERE patient_id = ? LIMIT 1",
                                               (patient_id,)).fetchone())
    return result


# Imported last, so slices can depend on each other in any order
from features.health_profile.logic import doctor_sees_patient  # noqa: E402
from features.medicine_reminders.logic import adherence, day_schedule, is_scheduled, SLOTS, times_for  # noqa: E402

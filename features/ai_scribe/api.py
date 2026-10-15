"""AI scribe: the doctor types or dictates consultation notes; Pyshio drafts the prescription for them to review.

This file: HTTP endpoints and request models.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from shared import auth
from shared.auth import doctor_user
from shared.db import get_db

router = APIRouter()


class ScribeIn(BaseModel):
    appointment_id: str
    notes: str = Field(min_length=3, max_length=4000)


@router.post("/api/doctor/scribe")
def scribe(data: ScribeIn, user: dict = Depends(doctor_user)):
    """A draft prescription from notes. Nothing is saved; the doctor reviews every line and issues it."""
    with get_db() as conn:
        if auth.rate_limited(conn, f"scribe:{user['id']}", 40, 60):
            raise HTTPException(status_code=429, detail="Too many drafts in a short time. Please wait a few minutes.")
        appt = conn.execute("SELECT patient_id, family_member_id FROM appointments WHERE id = ? AND doctor_id = ?",
                            (data.appointment_id, user["id"])).fetchone()
        if not appt:
            raise HTTPException(status_code=404, detail="Appointment not found")
        profile = profile_for(conn, appt["patient_id"], appt["family_member_id"])
        holder = conn.execute("SELECT name FROM users WHERE id = ?", (appt["patient_id"],)).fetchone()
        return draft(conn, data.notes, names=[profile["name"], holder["name"]], age=profile["age"], sex=profile.get("sex"),
                     user_id=user["id"])


# Imported last, so slices can depend on each other in any order
from features.ai_scribe.logic import draft  # noqa: E402
from features.health_profile.logic import profile_for  # noqa: E402

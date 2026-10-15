"""Prescription safety: interactions, duplicate salts, allergies, age, pregnancy and dose checks before a prescription is issued.

This file: HTTP endpoints and request models.
"""
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from shared import auth
from shared.auth import doctor_user
from shared.db import get_db

router = APIRouter()


class CheckLine(BaseModel):
    medicine_id: Optional[int] = None
    name: Optional[str] = Field(default=None, max_length=200)
    generic_name: Optional[str] = Field(default=None, max_length=200)
    strength: Optional[str] = Field(default=None, max_length=60)
    form: Optional[str] = Field(default=None, max_length=40)
    dosage: Optional[str] = Field(default=None, max_length=100)
    daily_units: Optional[float] = Field(default=None, gt=0, le=24)


class RxCheckIn(BaseModel):
    patient_id: Optional[int] = None
    appointment_id: Optional[str] = None
    family_member_id: Optional[int] = None
    patient_age: Optional[int] = Field(default=None, ge=0, le=120)
    pregnancy: Optional[Literal["pregnant", "breastfeeding"]] = None
    condition_ids: List[int] = Field(default=[], max_length=10)
    items: List[CheckLine] = Field(default=[], max_length=30)


@router.post("/api/doctor/rx-check")
def rx_check(data: RxCheckIn, user: dict = Depends(doctor_user)):
    """Live safety panel while the doctor writes a prescription."""
    with get_db() as conn:
        if auth.rate_limited(conn, f"rx-check:{user['id']}", 120, 10):
            raise HTTPException(status_code=429, detail="Too many checks. Please wait a minute.")
        patient_id, member_id = data.patient_id, data.family_member_id
        if data.appointment_id:
            appt = conn.execute("SELECT patient_id, family_member_id FROM appointments WHERE id = ? AND doctor_id = ?",
                                (data.appointment_id, user["id"])).fetchone()
            if not appt:
                raise HTTPException(status_code=404, detail="Appointment not found")
            patient_id, member_id = appt["patient_id"], appt["family_member_id"]
        if not patient_id or not doctor_sees_patient(conn, user["id"], patient_id):
            raise HTTPException(status_code=404, detail="Patient not found")
        profile = profile_for(conn, patient_id, member_id)
        return check(conn, items=[i.model_dump() for i in data.items], patient_id=patient_id, family_member_id=member_id,
                     age=data.patient_age if data.patient_age is not None else profile["age"],
                     pregnancy=pregnancy_from(conn, data.condition_ids, data.pregnancy),
                     allergies=profile["allergies"], who=profile["name"])


# Imported last, so slices can depend on each other in any order
from features.health_profile.logic import doctor_sees_patient, profile_for  # noqa: E402
from features.rx_safety.logic import check, pregnancy_from  # noqa: E402

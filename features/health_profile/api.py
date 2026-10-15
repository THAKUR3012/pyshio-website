"""Health profile: blood group, allergies, long-term conditions and an emergency contact; allergy checks for doctors.

This file: HTTP endpoints and request models.
"""
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from shared import validators as v
from shared.auth import doctor_user, patient_user
from shared.db import get_db


router = APIRouter()

BloodGroup = Literal["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]


class HealthProfileIn(BaseModel):
    blood_group: Optional[BloodGroup] = None
    allergies: Optional[str] = None
    chronic_conditions: Optional[str] = None
    emergency_name: Optional[str] = None
    emergency_phone: Optional[str] = None

    _allergies = field_validator("allergies")(v.short_list_text("Allergies"))
    _conditions = field_validator("chronic_conditions")(v.short_list_text("Conditions"))
    _ename = field_validator("emergency_name")(v.short_list_text("Emergency contact name", 100))

    @field_validator("emergency_phone")
    @classmethod
    def _ephone(cls, value):
        value = v.optional_text(value)
        return v.phone(value) if value else None


class CheckItem(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    generic_name: Optional[str] = Field(default=None, max_length=200)


class AllergyCheckIn(BaseModel):
    patient_id: int
    family_member_id: Optional[int] = None
    items: List[CheckItem] = Field(default=[], max_length=30)


@router.get("/api/my/health-profile")
def my_health_profile(user: dict = Depends(patient_user)):
    with get_db() as conn:
        return own_profile(conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone())


@router.put("/api/my/health-profile")
def save_health_profile(data: HealthProfileIn, user: dict = Depends(patient_user)):
    with get_db() as conn:
        conn.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k in FIELDS)} WHERE id = ?",
                     (*[getattr(data, k) for k in FIELDS], user["id"]))
        return own_profile(conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone())


@router.get("/api/doctor/patients/{patient_id}/health-profile")
def patient_health_profile(patient_id: int, member_id: Optional[int] = None, user: dict = Depends(doctor_user)):
    """What the doctor needs before treating: blood group, allergies, conditions, emergency contact."""
    with get_db() as conn:
        if not doctor_sees_patient(conn, user["id"], patient_id):
            raise HTTPException(status_code=404, detail="Patient not found")
        return profile_for(conn, patient_id, member_id)


@router.post("/api/doctor/allergy-check")
def allergy_check(data: AllergyCheckIn, user: dict = Depends(doctor_user)):
    with get_db() as conn:
        if not doctor_sees_patient(conn, user["id"], data.patient_id):
            raise HTTPException(status_code=404, detail="Patient not found")
        profile = profile_for(conn, data.patient_id, data.family_member_id)
    return {"warnings": allergy_warnings(profile["allergies"], [i.model_dump() for i in data.items], profile["name"]),
            "allergies": profile["allergies"]}


# Imported last, so slices can depend on each other in any order
from features.health_profile.logic import allergy_warnings, doctor_sees_patient, FIELDS, own_profile, profile_for  # noqa: E402

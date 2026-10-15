"""Family members: a patient keeps profiles for dependants and books care for them.

This file: HTTP endpoints and request models.
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from shared import validators as v
from shared.auth import patient_user
from shared.common import stamp
from shared.db import get_db


router = APIRouter()

Relation = Literal["spouse", "son", "daughter", "father", "mother", "brother", "sister", "grandfather", "grandmother",
                   "grandchild", "other"]
BloodGroup = Literal["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]


class FamilyMemberIn(BaseModel):
    name: str
    relation: Relation
    dob: Optional[str] = None
    sex: Optional[Literal["M", "F", "O"]] = None
    blood_group: Optional[BloodGroup] = None
    allergies: Optional[str] = None
    conditions: Optional[str] = None

    _name = field_validator("name")(v.name)
    _dob = field_validator("dob")(v.birth_date)
    _allergies = field_validator("allergies")(v.short_list_text("Allergies"))
    _conditions = field_validator("conditions")(v.short_list_text("Conditions"))


FIELDS = ("name", "relation", "dob", "sex", "blood_group", "allergies", "conditions")


@router.get("/api/my/family")
def my_family(user: dict = Depends(patient_user)):
    with get_db() as conn:
        return members(conn, user["id"])


@router.post("/api/my/family")
def add_member(data: FamilyMemberIn, user: dict = Depends(patient_user)):
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM family_members WHERE patient_id = ? AND deleted_at IS NULL",
                             (user["id"],)).fetchone()[0]
        if count >= MAX_MEMBERS:
            raise HTTPException(status_code=400, detail=f"You can keep up to {MAX_MEMBERS} family members")
        cur = conn.execute(f"INSERT INTO family_members (patient_id, {', '.join(FIELDS)}, created_at) "
                           f"VALUES (?, {', '.join('?' * len(FIELDS))}, ?)",
                           (user["id"], *[getattr(data, k) for k in FIELDS], stamp()))
        return member_view(member_for(conn, user["id"], cur.lastrowid))


@router.put("/api/my/family/{member_id}")
def update_member(member_id: int, data: FamilyMemberIn, user: dict = Depends(patient_user)):
    with get_db() as conn:
        member_for(conn, user["id"], member_id)
        conn.execute(f"UPDATE family_members SET {', '.join(f'{k} = ?' for k in FIELDS)} WHERE id = ?",
                     (*[getattr(data, k) for k in FIELDS], member_id))
        return member_view(member_for(conn, user["id"], member_id))


@router.delete("/api/my/family/{member_id}")
def remove_member(member_id: int, user: dict = Depends(patient_user)):
    """Past appointments and prescriptions keep the member's name; the profile just stops being bookable."""
    with get_db() as conn:
        member_for(conn, user["id"], member_id)
        conn.execute("UPDATE family_members SET deleted_at = ? WHERE id = ?", (stamp(), member_id))
    return {"ok": True}


# Imported last, so slices can depend on each other in any order
from features.family.logic import MAX_MEMBERS, member_for, member_view, members  # noqa: E402

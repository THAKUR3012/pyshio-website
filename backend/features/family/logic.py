"""Family members: a patient keeps profiles for dependants and books care for them.

The account holder stays the owner of everything (appointments, prescriptions, orders, payments); a family member
is who the care is *for*. Doctors see the member's name, age and health profile.

This file: logic and queries.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException


RELATIONS = ["spouse", "son", "daughter", "father", "mother", "brother", "sister", "grandfather", "grandmother",
             "grandchild", "other"]
MAX_MEMBERS = 10


def member_view(row) -> dict:
    return {"id": row["id"], "name": row["name"], "relation": row["relation"], "dob": row["dob"], "sex": row["sex"],
            "age": age_from(row["dob"]), "blood_group": row["blood_group"], "allergies": row["allergies"],
            "conditions": row["conditions"], "label": label(row["name"], row["relation"])}


def label(name: str, relation: Optional[str]) -> str:
    return f"{name} ({relation})" if relation else name


def members(conn, patient_id: int) -> list:
    return [member_view(r) for r in conn.execute(
        "SELECT * FROM family_members WHERE patient_id = ? AND deleted_at IS NULL ORDER BY id", (patient_id,))]


def member_for(conn, patient_id: int, member_id: Optional[int], allow_removed: bool = False):
    """The patient's own family member (404 for anyone else's)."""
    if not member_id:
        return None
    row = conn.execute("SELECT * FROM family_members WHERE id = ? AND patient_id = ?" +
                       ("" if allow_removed else " AND deleted_at IS NULL"), (member_id, patient_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Family member not found")
    return row


def person_label(row, holder_key: str = "patient_name") -> str:
    """'Riya Sharma (daughter) – booked by Asha Sharma' for a row joined with member_name / member_relation."""
    keys = row.keys() if hasattr(row, "keys") else row
    if "member_name" in keys and row["member_name"]:
        return f"{label(row['member_name'], row['member_relation'])} – booked by {row[holder_key]}"
    return row[holder_key]


# Imported last, so slices can depend on each other in any order
from features.health_profile.logic import age_from  # noqa: E402

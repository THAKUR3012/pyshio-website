"""Follow-ups: the doctor sets a follow-up date on a prescription; the patient is reminded and books in one tap.

This file: HTTP endpoints and request models.
"""
from fastapi import APIRouter, Depends

from shared.auth import patient_user
from shared.db import get_db


router = APIRouter()


@router.get("/api/my/follow-ups")
def follow_ups(user: dict = Depends(patient_user)):
    """Follow-ups doctors asked for: upcoming, overdue (last 30 days) and whether one is already booked."""
    with get_db() as conn:
        return my_follow_ups(conn, user["id"])


# Imported last, so slices can depend on each other in any order
from features.follow_ups.logic import my_follow_ups  # noqa: E402

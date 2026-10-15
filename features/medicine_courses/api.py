"""Medicine courses: who is on which medicine until when, refill predictions and pharmacy demand forecast.

This file: HTTP endpoints and request models.
"""
from datetime import timedelta
from fastapi import APIRouter, Depends, Query
from typing import Optional

from shared import settings
from shared.auth import patient_user, pharmacy_user
from shared.db import get_db

router = APIRouter()


@router.get("/api/pharmacy/courses")
def pharmacy_courses(status: Optional[str] = None, user: dict = Depends(pharmacy_user)):
    """This pharmacy's own customers: who is on long-term medicines and when they'll need more."""
    since = (settings.now().date() - timedelta(days=120)).isoformat()
    with get_db() as conn:
        rows = courses_view(conn, "c.pharmacy_id = ? AND (c.is_long_term = 1 OR c.started_on >= ?)",
                            (user["id"], since), include_patient=True)
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    if status:
        rows = [r for r in rows if r["status"] == status]
    return {"courses": rows, "counts": counts}


@router.get("/api/my/courses")
def my_courses(user: dict = Depends(patient_user)):
    with get_db() as conn:
        return courses_view(conn, "c.patient_id = ?", (user["id"],), include_patient=False)


@router.get("/api/pharmacy/forecast")
def pharmacy_forecast(days: int = Query(7, ge=1, le=90), user: dict = Depends(pharmacy_user)):
    """Estimated demand near the store: recent prescribing (trend and weekday pattern) plus long-term courses running out."""
    with get_db() as conn:
        return forecast(conn, get_pharmacy(conn, user["id"]), days)


# Imported last, so slices can depend on each other in any order
from features.medicine_courses.logic import courses_view, forecast  # noqa: E402
from features.pharmacies.logic import get_pharmacy  # noqa: E402

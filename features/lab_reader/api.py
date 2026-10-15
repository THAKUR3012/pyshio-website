"""Lab reports: read values from the report file for the lab to confirm, and explain results to patients in plain language.

This file: HTTP endpoints.
"""
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from shared import auth, settings
from shared.auth import lab_user
from shared.db import get_db

router = APIRouter()


@router.post("/api/lab/bookings/{booking_id}/report/read")
async def read_report_values(booking_id: str, file: UploadFile = File(...), user: dict = Depends(lab_user)):
    """Suggested key results from the report file. Nothing is saved: the lab checks them and uploads the report."""
    content = await file.read(settings.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if not content or len(content) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Upload a PDF, JPG or PNG under {settings.MAX_UPLOAD_MB} MB")
    with get_db() as conn:
        labs_logic.booking_for(conn, booking_id, user)
        if auth.rate_limited(conn, f"lab-read:{user['id']}", 60, 60):
            raise HTTPException(status_code=429, detail="Too many reads in a short time. Please wait a few minutes.")
        return read_report(conn, booking_id, content, user["id"])


@router.get("/api/records/{record_id}/explain")
def explain_record(record_id: int, lang: Literal["en", "hi"] = "en", user: dict = Depends(auth.require("patient", "doctor"))):
    """A plain-language explanation of a report's results (the patient, or a doctor who may see the record)."""
    with get_db() as conn:
        row = conn.execute(health_records.RECORD_SELECT + " WHERE h.id = ?", (record_id,)).fetchone()
        if not row or not health_records.can_view(conn, user, row):
            raise HTTPException(status_code=404, detail="Record not found")
        if auth.rate_limited(conn, f"explain:{user['id']}", 60, 60):
            raise HTTPException(status_code=429, detail="Too many requests. Please wait a few minutes.")
        values = health_records.record_values(conn, record_id)
        if not values:
            raise HTTPException(status_code=400, detail="This report has no results to explain yet")
        trends = health_records.trends_for(conn, row["patient_id"], user["id"] if user["role"] == "doctor" else None)
        return explain(conn, row, values, trends, lang, user["id"])


# Imported last, so slices can depend on each other in any order
from features.health_records import logic as health_records  # noqa: E402
from features.lab_reader.logic import explain, read_report  # noqa: E402
from features.labs import logic as labs_logic  # noqa: E402

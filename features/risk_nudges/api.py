"""Risk nudges: a no-show score for upcoming visits with one "Still coming?" nudge, and gentle alerts when medicine-taking drops.

This file: HTTP endpoints.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from shared import settings
from shared.auth import doctor_user, patient_user
from shared.common import stamp
from shared.db import get_db

router = APIRouter()


@router.post("/api/appointments/{appt_id}/confirm-attendance")
def confirm_attendance(appt_id: str, user: dict = Depends(patient_user)):
    """The patient says they're coming (no more nudges for this visit)."""
    with get_db() as conn:
        cur = conn.execute("UPDATE appointments SET attendance_confirmed_at = ? WHERE id = ? AND patient_id = ? "
                           "AND status = 'confirmed'", (stamp(), appt_id, user["id"]))
        if not cur.rowcount:
            raise HTTPException(status_code=404, detail="Upcoming appointment not found")
    return {"ok": True, "confirmed": True}


@router.get("/api/doctor/visit-risk")
def visit_risk(date: Optional[str] = None, user: dict = Depends(doctor_user)):
    """For the doctor's list: which confirmed visits may need a reminder call (a band, with the reasons)."""
    with get_db() as conn:
        sql = "SELECT * FROM appointments WHERE doctor_id = ? AND status = 'confirmed'"
        params = [user["id"]]
        if date:
            sql += " AND appointment_date = ?"
            params.append(date)
        else:
            sql += " AND appointment_date >= ?"
            params.append(settings.now().date().isoformat())
        out = {}
        for r in conn.execute(sql + " ORDER BY appointment_date, appointment_time LIMIT 200", params).fetchall():
            s = risk.no_show_score(conn, r)
            out[r["id"]] = {"band": s["band"], "reasons": s["reasons"],
                            "confirmed_by_patient": bool(r["attendance_confirmed_at"])}
        return out


# Imported last, so slices can depend on each other in any order
from features.risk_nudges import logic as risk  # noqa: E402

"""Patient history: visits, prescriptions and spend over time, for the patient and their doctors.

This file: HTTP endpoints and request models.
"""
import csv
import io
from fastapi import APIRouter, Depends, HTTPException, Response
from typing import Optional

from shared import auth, settings
from shared.auth import doctor_user
from shared.common import csv_safe
from shared.db import get_db

router = APIRouter()


@router.get("/api/my/history")
def my_history(user: dict = Depends(auth.require("patient"))):
    with get_db() as conn:
        return patient_history(conn, user["id"])


@router.get("/api/my/history.csv")
def my_history_csv(user: dict = Depends(auth.require("patient"))):
    with get_db() as conn:
        events = patient_history(conn, user["id"])["events"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Sr No", "Date", "Type", "Reference", "Details", "Doctor / Pharmacy", "Amount (Rs)", "Status",
                     "Payment"])
    for n, e in enumerate(events, 1):
        amount = "" if e["amount_paise"] is None else f"{e['amount_paise'] / 100:.2f}"
        writer.writerow([n, e["date"][:16].replace("T", " "), e["kind"].title(), e["reference"], csv_safe(e["title"]),
                         csv_safe(e["party"]), amount, e["status"], e.get("payment_status") or ""])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="my-health-history.csv"'})


@router.get("/api/my/doctors")
def my_doctors(user: dict = Depends(auth.require("patient"))):
    """Doctors this patient has seen or booked, for quick "book again"."""
    today = settings.now().date().isoformat()
    with get_db() as conn:
        rows = conn.execute(
            DOCTOR_SELECT.replace("FROM doctors d", ", x.visits, x.first_visit, x.last_visit, x.next_date, x.next_time, "
                                  "x.prescriptions FROM doctors d") +
            """ JOIN (SELECT a.doctor_id,
                       SUM(a.status IN ('completed', 'no_show') OR (a.status = 'confirmed' AND a.appointment_date <= ?)) AS visits,
                       MIN(CASE WHEN a.status IN ('confirmed', 'completed', 'no_show') THEN a.appointment_date END) AS first_visit,
                       MAX(CASE WHEN a.status IN ('completed', 'no_show') OR (a.status = 'confirmed' AND a.appointment_date <= ?)
                           THEN a.appointment_date END) AS last_visit,
                       MIN(CASE WHEN a.status = 'confirmed' AND a.appointment_date > ? THEN a.appointment_date END) AS next_date,
                       NULL AS next_time,
                       (SELECT COUNT(*) FROM prescriptions r WHERE r.patient_id = a.patient_id AND r.doctor_id = a.doctor_id)
                           AS prescriptions
                FROM appointments a WHERE a.patient_id = ? AND a.status NOT IN ('pending_payment', 'expired')
                GROUP BY a.doctor_id) x ON x.doctor_id = d.user_id
            ORDER BY x.last_visit IS NULL, x.last_visit DESC""", (today, today, today, user["id"])).fetchall()
    return [{**doctor_public(r), "bookable": r["status"] == "approved" and bool(r["is_active"]),
             "visits": r["visits"] or 0, "first_visit": r["first_visit"], "last_visit": r["last_visit"],
             "next_appointment": r["next_date"], "prescriptions": r["prescriptions"]} for r in rows]


@router.get("/api/doctor/patients")
def doctor_patients(q: Optional[str] = None, user: dict = Depends(doctor_user)):
    sql = """SELECT u.id, u.name, u.email, u.phone, COUNT(*) AS visits,
                    SUM(a.status = 'completed') AS completed,
                    MAX(CASE WHEN a.status IN ('confirmed','completed','no_show') THEN a.appointment_date END) AS last_date
             FROM appointments a JOIN users u ON u.id = a.patient_id
             WHERE a.doctor_id = ? AND a.status NOT IN ('pending_payment', 'expired')"""
    params = [user["id"]]
    if q:
        like = f"%{q.strip().lower()}%"
        sql += " AND (lower(u.name) LIKE ? OR lower(u.email) LIKE ? OR u.phone LIKE ?)"
        params += [like, like, like]
    sql += " GROUP BY u.id ORDER BY last_date DESC"
    with get_db() as conn:
        return [dict(r) for r in conn.execute(sql, params)]


@router.get("/api/doctor/patients/{patient_id}")
def doctor_patient_history(patient_id: int, user: dict = Depends(doctor_user)):
    """A patient's visits with *this* doctor only."""
    with get_db() as conn:
        rows = conn.execute(APPOINTMENT_SELECT + " WHERE a.doctor_id = ? AND a.patient_id = ? "
                            "AND a.status NOT IN ('pending_payment', 'expired') "
                            "ORDER BY a.appointment_date DESC, a.appointment_time DESC",
                            (user["id"], patient_id)).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {"patient": {"id": patient_id, "name": rows[0]["patient_name"], "email": rows[0]["patient_email"],
                        "phone": rows[0]["patient_phone"]},
            "appointments": [appointment_view(r, "doctor") for r in rows]}


# Imported last, so slices can depend on each other in any order
from features.appointments.logic import APPOINTMENT_SELECT, appointment_view  # noqa: E402
from features.doctors.logic import doctor_public, DOCTOR_SELECT  # noqa: E402
from features.patient_history.logic import patient_history  # noqa: E402

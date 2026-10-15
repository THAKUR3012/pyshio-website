"""Reviews: patients rate doctors and pharmacies after a visit or order.

This file: HTTP endpoints and request models.
"""
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal, Optional

from shared import validators as v
from shared.auth import patient_user
from shared.common import stamp
from shared.db import get_db

router = APIRouter()


# ---------- Reviews ----------
class ReviewIn(BaseModel):
    appointment_id: Optional[str] = None
    order_id: Optional[str] = None
    rating: int = Field(ge=1, le=5)
    comment: Optional[str] = Field(default=None, max_length=1000)

    _comment = field_validator("comment")(v.optional_text)

    @model_validator(mode="after")
    def _one(self):
        if bool(self.appointment_id) == bool(self.order_id):
            raise ValueError("Review either an appointment or an order")
        return self


@router.post("/api/reviews")
def add_review(data: ReviewIn, user: dict = Depends(patient_user)):
    """Patients rate a doctor after a completed visit, or a pharmacy after a completed order (once each)."""
    with get_db() as conn:
        if data.appointment_id:
            row = conn.execute("SELECT * FROM appointments WHERE id = ? AND patient_id = ?",
                               (data.appointment_id, user["id"])).fetchone()
            target_role, target_id = "doctor", row["doctor_id"] if row else None
        else:
            row = conn.execute("SELECT * FROM medicine_orders WHERE id = ? AND patient_id = ?",
                               (data.order_id, user["id"])).fetchone()
            target_role, target_id = "pharmacy", row["pharmacy_id"] if row else None
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        if row["status"] != "completed":
            raise HTTPException(status_code=400, detail="You can rate this once it has been completed")
        try:
            conn.execute("INSERT INTO reviews (patient_id, target_role, target_id, appointment_id, order_id, rating, comment, "
                         "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (user["id"], target_role, target_id, data.appointment_id, data.order_id, data.rating,
                          data.comment, stamp()))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="You've already rated this")
    return {"ok": True}


@router.get("/api/reviews")
def list_reviews(target_role: Literal["doctor", "pharmacy"], target_id: int, limit: int = Query(20, ge=1, le=100)):
    with get_db() as conn:
        rows = conn.execute("SELECT rv.rating, rv.comment, rv.created_at, u.name FROM reviews rv JOIN users u ON "
                            "u.id = rv.patient_id WHERE rv.target_role = ? AND rv.target_id = ? ORDER BY rv.id DESC LIMIT ?",
                            (target_role, target_id, limit)).fetchall()
    # Only first name + initial, never the full identity
    return [{"rating": r["rating"], "comment": r["comment"], "date": r["created_at"][:10],
             "by": (r["name"].split()[0] + (" " + r["name"].split()[-1][0] + "." if len(r["name"].split()) > 1 else ""))
             if r["name"] != "Deleted user" else "Former patient"} for r in rows]

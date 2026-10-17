"""Settlements: online payments owed to doctors and pharmacies, and payouts.

This file: HTTP endpoints and request models.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional

from shared import mailer, validators as v
from shared.auth import admin_user, doctor_user, pharmacy_user
from shared.common import audit, paise, stamp
from shared.db import get_db, write_txn


router = APIRouter()


class PayoutIn(BaseModel):
    payee_role: Literal["doctor", "pharmacy"]
    payee_id: int
    amount: float = Field(gt=0, le=100_000_000)
    reference: Optional[str] = Field(default=None, max_length=100)
    note: Optional[str] = Field(default=None, max_length=300)

    _texts = field_validator("reference", "note")(v.optional_text)


@router.get("/api/doctor/earnings")
def doctor_earnings(user: dict = Depends(doctor_user)):
    with get_db() as conn:
        payouts = [dict(r) for r in conn.execute("SELECT amount_paise, reference, note, created_at FROM payouts "
                                                 "WHERE payee_role = 'doctor' AND payee_id = ? ORDER BY id DESC", (user["id"],))]
        return {**settlement_for(conn, "doctor", user["id"]), "payouts": payouts}


@router.get("/api/pharmacy/earnings")
def pharmacy_earnings(user: dict = Depends(pharmacy_user)):
    with get_db() as conn:
        payouts = [dict(r) for r in conn.execute("SELECT amount_paise, reference, note, created_at FROM payouts "
                                                 "WHERE payee_role = 'pharmacy' AND payee_id = ? ORDER BY id DESC", (user["id"],))]
        return {**settlement_for(conn, "pharmacy", user["id"]), "payouts": payouts}


@router.get("/api/admin/settlements")
def admin_settlements(user: dict = Depends(admin_user)):
    with get_db() as conn:
        result = []
        for r in conn.execute("SELECT d.user_id AS id, u.name, d.clinic_name AS detail FROM doctors d "
                              "JOIN users u ON u.id = d.user_id ORDER BY u.name"):
            result.append({"payee_role": "doctor", "payee_id": r["id"], "name": r["name"], "detail": r["detail"],
                           **settlement_for(conn, "doctor", r["id"])})
        for r in conn.execute("SELECT p.user_id AS id, p.store_name AS name, p.city AS detail FROM pharmacies p "
                              "ORDER BY p.store_name"):
            result.append({"payee_role": "pharmacy", "payee_id": r["id"], "name": r["name"], "detail": r["detail"],
                           **settlement_for(conn, "pharmacy", r["id"])})
    return [s for s in result if s["earned_paise"] or s["paid_out_paise"]]


@router.post("/api/admin/payouts")
def admin_payout(data: PayoutIn, user: dict = Depends(admin_user)):
    with get_db() as conn:
        with write_txn(conn):
            table = "doctors" if data.payee_role == "doctor" else "pharmacies"
            if not conn.execute(f"SELECT 1 FROM {table} WHERE user_id = ?", (data.payee_id,)).fetchone():
                raise HTTPException(status_code=404, detail="Payee not found")
            due = settlement_for(conn, data.payee_role, data.payee_id)["due_paise"]
            if paise(data.amount) > due:
                raise HTTPException(status_code=400, detail=f"Only {mailer.money(due)} is due")
            conn.execute("INSERT INTO payouts (payee_role, payee_id, amount_paise, reference, note, created_by, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", (data.payee_role, data.payee_id, paise(data.amount),
                                                          data.reference, data.note, user["id"], stamp()))
            audit(conn, user["id"], "payout.recorded", f"{data.payee_role}:{data.payee_id}",
                  f"{mailer.money(paise(data.amount))} {data.reference or ''}".strip())
            payee = conn.execute("SELECT name, email FROM users WHERE id = ?", (data.payee_id,)).fetchone()
        settlements_emails.send_payout_recorded(payee["name"], payee["email"], paise(data.amount), data.reference)
        return settlement_for(conn, data.payee_role, data.payee_id)


@router.get("/api/admin/payouts")
def admin_payouts(user: dict = Depends(admin_user)):
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT po.*, u.name AS payee_name, a.name AS created_by_name FROM payouts po JOIN users u ON u.id = po.payee_id "
            "LEFT JOIN users a ON a.id = po.created_by ORDER BY po.id DESC LIMIT 500")]


# Imported last, so slices can depend on each other in any order
from features.settlements.logic import settlement_for  # noqa: E402
from features.settlements import emails as settlements_emails  # noqa: E402

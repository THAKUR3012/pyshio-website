"""Settlements: online payments owed to doctors and pharmacies, and payouts.

This file: logic and queries.
"""
from __future__ import annotations

from shared import settings


# ---------- Settlements (online payments owed to doctors & pharmacies) ----------
def settlement_for(conn, role: str, payee_id: int) -> dict:
    if role == "pharmacy":
        earned = conn.execute("SELECT COALESCE(SUM(total_paise), 0) FROM medicine_orders WHERE pharmacy_id = ? "
                              "AND payment_method = 'online' AND payment_status = 'paid' AND status = 'completed'",
                              (payee_id,)).fetchone()[0]
    else:
        earned = conn.execute("SELECT COALESCE(SUM(fee), 0) * 100 FROM appointments WHERE doctor_id = ? "
                              "AND payment_method = 'online' AND payment_status IN ('paid', 'not_refundable') "
                              "AND status IN ('completed', 'no_show', 'cancelled')", (payee_id,)).fetchone()[0]
    commission = int(round(earned * settings.COMMISSION_PERCENT / 100))
    paid = conn.execute("SELECT COALESCE(SUM(amount_paise), 0) FROM payouts WHERE payee_role = ? AND payee_id = ?",
                        (role, payee_id)).fetchone()[0]
    return {"earned_paise": earned, "commission_paise": commission, "paid_out_paise": paid,
            "due_paise": earned - commission - paid, "commission_percent": settings.COMMISSION_PERCENT}

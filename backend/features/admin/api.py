"""Admin: users, audit trail, platform stats and housekeeping.

This file: HTTP endpoints and request models.
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from typing import Optional

from shared import settings
from shared.auth import admin_user
from shared.common import audit
from shared.db import get_db


router = APIRouter()


@router.get("/api/admin/stats")
def admin_stats(user: dict = Depends(admin_user)):
    m_start, m_end = month_bounds()
    with get_db() as conn:
        def one(sql, *params):
            return conn.execute(sql, params).fetchone()[0]
        doctors = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM doctors GROUP BY status")}
        month = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM appointments WHERE appointment_date BETWEEN ? AND ? GROUP BY status",
            (m_start, m_end))}
        return {
            "doctors": {s: doctors.get(s, 0) for s in ("pending", "approved", "rejected", "suspended")},
            "patients": one("SELECT COUNT(*) FROM users WHERE role = 'patient'"),
            "appointments_month": {s: month.get(s, 0) for s in ("confirmed", "completed", "no_show", "cancelled")},
            "online_revenue_month": one("SELECT COALESCE(SUM(fee), 0) FROM appointments WHERE payment_method = 'online' "
                                        "AND payment_status = 'paid' AND appointment_date BETWEEN ? AND ?", m_start, m_end),
            "refunds_failed": one("SELECT COUNT(*) FROM appointments WHERE payment_status = 'refund_failed'"),
        }


@router.get("/api/admin/users")
def admin_users(role: Optional[str] = None, q: Optional[str] = None, limit: int = Query(200, ge=1, le=1000),
                user: dict = Depends(admin_user)):
    sql, params = "SELECT id, role, name, email, phone, is_active, created_at, last_login_at, deleted_at FROM users " \
                  "WHERE 1 = 1", []
    if role:
        sql += " AND role = ?"
        params.append(role)
    if q:
        like = f"%{q.strip().lower()}%"
        sql += " AND (lower(name) LIKE ? OR lower(email) LIKE ? OR phone LIKE ?)"
        params += [like, like, like]
    with get_db() as conn:
        return [dict(r) | {"is_active": bool(r["is_active"])}
                for r in conn.execute(sql + " ORDER BY created_at DESC LIMIT ?", (*params, limit))]


@router.get("/api/admin/audit")
def admin_audit(limit: int = Query(200, ge=1, le=1000), user: dict = Depends(admin_user)):
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT l.*, u.name AS actor_name, u.role AS actor_role FROM audit_log l LEFT JOIN users u ON u.id = l.actor_id "
            "ORDER BY l.id DESC LIMIT ?", (limit,))]


@router.post("/api/admin/users/{user_id}/active")
def admin_set_active(user_id: int, background: BackgroundTasks, active: bool = Query(...),
                     user: dict = Depends(admin_user)):
    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="You can't disable your own account")
    with get_db() as conn:
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (int(active), user_id))
        if not active:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        audit(conn, user["id"], "user.enable" if active else "user.disable", f"user:{user_id}", target["email"])
    if not active:
        # Don't leave people waiting on an account that can no longer act
        reason = "This account is no longer active on the platform."
        if target["role"] == "doctor":
            wind_down_appointments("doctor_id = ?", (user_id,), reason + " Please book another doctor.", background)
        elif target["role"] == "pharmacy":
            pharmacies_logic.wind_down_pharmacy(user_id, reason, background)
        elif target["role"] == "lab":
            labs.wind_down_lab(user_id, reason, background)
        elif target["role"] == "patient":
            wind_down_appointments("patient_id = ?", (user_id,), reason, background)
            medicine_orders_logic.wind_down_patient_orders(user_id, reason, background)
        background.add_task(accounts_emails.send_account_disabled, target["name"], target["email"])
    return {"ok": True}


@router.get("/api/admin/network-stats")
def network_stats(user: dict = Depends(admin_user)):
    month_start = settings.now().date().replace(day=1).isoformat()
    with get_db() as conn:
        def one(sql, *params):
            return conn.execute(sql, params).fetchone()[0]
        pharm = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM pharmacies GROUP BY status")}
        return {
            "pharmacies": {s: pharm.get(s, 0) for s in ("pending", "approved", "rejected", "suspended")},
            "prescriptions_month": one("SELECT COUNT(*) FROM prescriptions WHERE created_at >= ?", month_start),
            "orders_month": one("SELECT COUNT(*) FROM medicine_orders WHERE created_at >= ? AND status NOT IN "
                                "('pending_payment', 'expired')", month_start),
            "medicine_sales_month_paise": one("SELECT COALESCE(SUM(total_paise), 0) FROM medicine_orders "
                                              "WHERE status = 'completed' AND created_at >= ?", month_start),
            "order_refunds_failed": one("SELECT COUNT(*) FROM medicine_orders WHERE payment_status = 'refund_failed'"),
            "catalogue_size": one("SELECT COUNT(*) FROM medicines"),
        }


# Imported last, so slices can depend on each other in any order
from features.appointments.logic import month_bounds, wind_down_appointments  # noqa: E402
from features.accounts import emails as accounts_emails  # noqa: E402
from features.labs import logic as labs  # noqa: E402
from features.medicine_orders import logic as medicine_orders_logic  # noqa: E402
from features.pharmacies import logic as pharmacies_logic  # noqa: E402

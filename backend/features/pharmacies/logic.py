"""Pharmacies: registration, profile, nearby search, stats and admin approval.

This file: logic and queries.
"""
from __future__ import annotations

import re
from fastapi import BackgroundTasks, HTTPException

from shared import geo, settings
from shared.common import stamp
from shared.db import get_db


# ---------- Pharmacies ----------
PHARMACY_SELECT = """SELECT p.*, u.name, u.email, u.phone, u.is_active,
    (SELECT ROUND(AVG(rating), 1) FROM reviews rv WHERE rv.target_role = 'pharmacy' AND rv.target_id = p.user_id) AS rating,
    (SELECT COUNT(*) FROM reviews rv WHERE rv.target_role = 'pharmacy' AND rv.target_id = p.user_id) AS rating_count
    FROM pharmacies p JOIN users u ON u.id = p.user_id"""


def pharmacy_public(row, distance_km: float = None) -> dict:
    view = {k: row[k] for k in ("slug", "store_name", "address", "city", "state", "pincode", "lat", "lng", "opening_hours",
                                "delivery_radius_km", "delivery_fee_paise", "flat_discount_percent",
                                "pickup_ready_minutes", "delivery_minutes")}
    view.update(id=row["user_id"], phone=row["phone"], offers_delivery=bool(row["offers_delivery"]),
                accepts_online_payment=bool(row["accepts_online_payment"]) and settings.online_payments_enabled(),
                rating=row["rating"], rating_count=row["rating_count"])
    if distance_km is not None:
        view["distance_km"] = distance_km
    return view


def pharmacy_private(row) -> dict:
    return {**pharmacy_public(row), "name": row["name"], "email": row["email"],
            "drug_licence_no": row["drug_licence_no"], "pharmacist_name": row["pharmacist_name"],
            "pharmacist_reg_no": row["pharmacist_reg_no"], "gstin": row["gstin"], "status": row["status"],
            "status_reason": row["status_reason"], "created_at": row["created_at"], "approved_at": row["approved_at"],
            "accepts_online_payment_setting": bool(row["accepts_online_payment"]), "is_active": bool(row["is_active"])}


def get_pharmacy(conn, pharmacy_id: int):
    row = conn.execute(PHARMACY_SELECT + " WHERE p.user_id = ?", (pharmacy_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Pharmacy not found")
    return row


def nearby_pharmacies(conn, lat: float, lng: float, radius_km: float):
    """[(distance_km, row)] of approved, active pharmacies within the radius, nearest first."""
    min_lat, max_lat, min_lng, max_lng = geo.bounding_box(lat, lng, radius_km)
    rows = conn.execute(PHARMACY_SELECT + " WHERE p.status = 'approved' AND u.is_active = 1 "
                        "AND p.lat BETWEEN ? AND ? AND p.lng BETWEEN ? AND ?",
                        (min_lat, max_lat, min_lng, max_lng)).fetchall()
    return geo.within(rows, lat, lng, radius_km)


def make_slug(conn, name: str, city: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", f"{name} {city}".lower()).strip("-")[:60] or "pharmacy"
    slug, n = base, 2
    while conn.execute("SELECT 1 FROM pharmacies WHERE slug = ?", (slug,)).fetchone():
        slug, n = f"{base}-{n}", n + 1
    return slug


def wind_down_pharmacy(pharmacy_id: int, reason: str, background: BackgroundTasks) -> int:
    with get_db() as conn:
        conn.execute("UPDATE rx_requests SET status = 'declined', updated_at = ? WHERE pharmacy_id = ? "
                     "AND status IN ('open', 'quoted')", (stamp(), pharmacy_id))
        conn.execute("UPDATE quotes SET status = 'withdrawn' WHERE pharmacy_id = ? AND status = 'active'", (pharmacy_id,))
    return wind_down_orders("pharmacy_id = ?", (pharmacy_id,), reason, background, ["patient"])


# Imported last, so slices can depend on each other in any order
from features.medicine_orders.logic import wind_down_orders  # noqa: E402

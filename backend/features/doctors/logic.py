"""Doctors: registration, the public directory and profiles, opening hours, services, leave, admin approval.

This file: logic and queries.
"""
from __future__ import annotations

import re
from fastapi import HTTPException

from shared import settings


WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DEFAULT_HOURS = [(d, "10:00", "13:00") for d in range(6)] + [(d, "17:00", "20:00") for d in range(6)]


DOCTOR_SELECT = """
    SELECT d.*, u.name, u.email, u.phone, u.is_active,
           (SELECT MIN(fee) FROM services s WHERE s.doctor_id = d.user_id AND s.active = 1) AS fee_from,
           (SELECT ROUND(AVG(rating), 1) FROM reviews rv WHERE rv.target_role = 'doctor' AND rv.target_id = d.user_id) AS rating,
           (SELECT COUNT(*) FROM reviews rv WHERE rv.target_role = 'doctor' AND rv.target_id = d.user_id) AS rating_count
    FROM doctors d JOIN users u ON u.id = d.user_id
"""


def doctor_public(row) -> dict:
    return {
        "id": row["user_id"], "slug": row["slug"], "name": row["name"],
        "qualification": row["qualification"], "experience_years": row["experience_years"],
        "specialities": [s for s in (row["specialities"] or "").split("|") if s],
        "bio": row["bio"], "clinic_name": row["clinic_name"], "address": row["address"],
        "city": row["city"], "state": row["state"], "pincode": row["pincode"], "fee_from": row["fee_from"],
        "registration_council": row["registration_council"],
        "accepts_clinic_payment": bool(row["accepts_clinic_payment"]),
        "accepts_online_payment": bool(row["accepts_online_payment"]) and settings.online_payments_enabled(),
        "category": row["category"],
        "category_label": settings.DOCTOR_CATEGORIES.get(row["category"], {}).get("label", row["category"]),
        "can_prescribe": row["category"] in settings.PRESCRIBING_CATEGORIES,
        "lat": row["lat"], "lng": row["lng"], "rating": row["rating"], "rating_count": row["rating_count"],
        "offers_home_visits": bool(row["offers_home_visits"]), "home_visit_radius_km": row["home_visit_radius_km"],
        "home_visit_fee": row["home_visit_fee"],
        "offers_video": bool(row["offers_video"]), "video_fee": row["video_fee"],
    }


def doctor_private(row) -> dict:
    return {
        **doctor_public(row), "email": row["email"], "phone": row["phone"],
        "registration_no": row["registration_no"], "slot_minutes": row["slot_minutes"],
        "accepts_online_payment_setting": bool(row["accepts_online_payment"]),
        "status": row["status"], "status_reason": row["status_reason"],
        "created_at": row["created_at"], "approved_at": row["approved_at"], "is_active": bool(row["is_active"]),
        "share_insights": bool(row["share_insights_at"]), "share_insights_at": row["share_insights_at"],
        "accept_mr_meetings": bool(row["accept_mr_meetings"]),
    }


def get_doctor(conn, doctor_id: int):
    row = conn.execute(DOCTOR_SELECT + " WHERE d.user_id = ?", (doctor_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return row


def bookable_doctor(conn, slug: str):
    row = conn.execute(DOCTOR_SELECT + " WHERE d.slug = ?", (slug,)).fetchone()
    if not row or row["status"] != "approved" or not row["is_active"]:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return row


def make_slug(conn, name: str, city: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", f"{name} {city}".lower()).strip("-")[:60] or "doctor"
    slug, n = base, 2
    while conn.execute("SELECT 1 FROM doctors WHERE slug = ?", (slug,)).fetchone():
        slug, n = f"{base}-{n}", n + 1
    return slug


def doctor_extras(conn, doctor_id: int, include_inactive: bool = False) -> dict:
    services = conn.execute(
        "SELECT id, name, duration, fee, active FROM services WHERE doctor_id = ?"
        + ("" if include_inactive else " AND active = 1") + " ORDER BY fee, name", (doctor_id,)).fetchall()
    hours = conn.execute("SELECT weekday, start_time, end_time FROM doctor_hours WHERE doctor_id = ? "
                         "ORDER BY weekday, start_time", (doctor_id,)).fetchall()
    return {"services": [dict(s, active=bool(s["active"])) for s in services], "hours": [dict(h) for h in hours]}

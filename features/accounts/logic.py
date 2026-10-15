"""Accounts: sign-up, login, password reset, profile, home location, consent, data export and account closure.

This file: logic and queries.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from shared import auth, settings
from shared.db import get_db


def _lower_email(value):
    return (value or "").strip().lower()


# ---------- Serialisation ----------
def user_view(user: dict) -> dict:
    view = {k: user[k] for k in ("id", "role", "name", "email", "phone", "created_at")}
    if user["role"] == "patient":
        view.update(dob=user.get("dob"), sex=user.get("sex"), share_consent=bool(user.get("share_consent_at")),
                    city=user.get("city"), state=user.get("state"))
    return view


def me_payload(user: dict) -> dict:
    data = {"user": user_view(user)}
    if user["role"] == "doctor":
        with get_db() as conn:
            row = conn.execute("SELECT status, status_reason, slug, category, lat, lng FROM doctors WHERE user_id = ?",
                               (user["id"],)).fetchone()
        data["doctor"] = dict(row) if row else None
    if user["role"] == "pharmacy":
        with get_db() as conn:
            row = conn.execute("SELECT status, status_reason, slug, store_name FROM pharmacies WHERE user_id = ?",
                               (user["id"],)).fetchone()
        data["pharmacy"] = dict(row) if row else None
    if user["role"] == "patient":
        data["home"] = {"address": user.get("home_address"), "lat": user.get("home_lat"), "lng": user.get("home_lng"),
                        "city": user.get("city"), "state": user.get("state")}
    if user["role"] == "lab":
        with get_db() as conn:
            row = conn.execute("SELECT status, status_reason, slug, lab_name FROM labs WHERE user_id = ?",
                               (user["id"],)).fetchone()
        data["lab"] = dict(row) if row else None
    if user["role"] in ("company", "mr"):
        data.update(pharma_insights.me_extra(user))
    return data


def _create_user(conn, role: str, data, **extra) -> int:
    return auth.create_user(conn, role, data, **extra)


def registration_throttle(request: Request) -> None:
    with get_db() as conn:
        if auth.rate_limited(conn, f"register:{client_ip(request)}", settings.REGISTRATIONS_PER_HOUR, 60):
            raise HTTPException(status_code=429, detail="Too many sign-ups from this network. Please try again later.")


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# Imported last, so slices can depend on each other in any order
from features.pharma_insights import logic as pharma_insights  # noqa: E402

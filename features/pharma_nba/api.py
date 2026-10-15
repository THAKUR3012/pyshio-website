"""MR next-best-action and company alerts: which doctors to meet next and why; unusual changes in a product's prescribing.

This file: HTTP endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException

from shared import auth
from shared.auth import mr_user
from shared.db import get_db

router = APIRouter()


@router.get("/api/mr/next-actions")
def mr_next_actions(user: dict = Depends(mr_user)):
    with get_db() as conn:
        return next_actions(conn, insights.mr_for(conn, user), user["id"])


@router.get("/api/mr/next-actions/{doctor_id}/brief")
def mr_visit_brief(doctor_id: int, user: dict = Depends(mr_user)):
    with get_db() as conn:
        mr = insights.mr_for(conn, user)
        if auth.rate_limited(conn, f"mr-brief:{user['id']}", 30, 60):
            raise HTTPException(status_code=429, detail="Too many briefs in a short time. Please wait a few minutes.")
        brief = visit_brief(conn, mr, user["id"], doctor_id)
        if not brief:
            raise HTTPException(status_code=404, detail="Doctor not in your plan")
        return brief


@router.get("/api/mr/alerts")
def mr_alerts(user: dict = Depends(mr_user)):
    with get_db() as conn:
        mr = insights.mr_for(conn, user)
        return alerts_for(conn, mr["company_id"], insights.split_areas(mr["care_areas"]) or None)


@router.get("/api/company/alerts")
def company_alerts(user: dict = Depends(auth.require("company"))):
    with get_db() as conn:
        company = insights.company_for(conn, user, approved=True)
        return alerts_for(conn, company["id"])


# Imported last, so slices can depend on each other in any order
from features.pharma_insights import logic as insights  # noqa: E402
from features.pharma_nba.logic import alerts_for, next_actions, visit_brief  # noqa: E402

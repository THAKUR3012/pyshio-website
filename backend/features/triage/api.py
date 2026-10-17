"""Symptom checker: describe the problem in English or Hinglish, get emergency warnings and the right kind of doctor nearby.

This file: HTTP endpoints and request models.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from shared import auth
from shared.db import get_db

router = APIRouter()


class TriageIn(BaseModel):
    text: str = Field(min_length=2, max_length=500)
    age: Optional[int] = Field(default=None, ge=0, le=120)
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lng: Optional[float] = Field(default=None, ge=-180, le=180)


@router.post("/api/triage")
def triage(data: TriageIn, request: Request, user: Optional[dict] = Depends(auth.current_user)):
    """Emergency check first, then the right specialities and the cheapest such doctors nearby. Nothing is stored."""
    key = f"triage:{user['id']}" if user else f"triage-ip:{request.client.host if request.client else 'unknown'}"
    with get_db() as conn:
        if auth.rate_limited(conn, key, 30, 60):
            raise HTTPException(status_code=429, detail="Too many questions in a short time. Please wait a few minutes.")
    return answer(data.text, age=data.age, lat=data.lat, lng=data.lng, user=user)


# Imported last, so slices can depend on each other in any order
from features.triage.logic import answer  # noqa: E402

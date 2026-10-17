"""Video consultations: doctors offer online consults; patient and doctor join a private video room.

This file: HTTP endpoints and request models.
"""
from html import escape
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from shared import auth, settings
from shared.auth import doctor_user
from shared.db import get_db


router = APIRouter()


class VideoSettingsIn(BaseModel):
    offers_video: bool
    video_fee: Optional[int] = Field(default=None, ge=0, le=20000)


@router.get("/api/doctor/video-settings")
def get_video_settings(user: dict = Depends(doctor_user)):
    with get_db() as conn:
        row = conn.execute("SELECT offers_video, video_fee FROM doctors WHERE user_id = ?", (user["id"],)).fetchone()
    return {"offers_video": bool(row["offers_video"]), "video_fee": row["video_fee"]}


@router.put("/api/doctor/video-settings")
def save_video_settings(data: VideoSettingsIn, user: dict = Depends(doctor_user)):
    with get_db() as conn:
        conn.execute("UPDATE doctors SET offers_video = ?, video_fee = ? WHERE user_id = ?",
                     (int(data.offers_video), data.video_fee, user["id"]))
    return data.model_dump()


def _participant_row(conn, appt_id: str, user: dict):
    row = appointment_for(conn, appt_id, user)
    if user["role"] not in ("patient", "doctor"):
        raise HTTPException(status_code=404, detail="Appointment not found")
    return row


@router.get("/api/appointments/{appt_id}/video")
def video_status(appt_id: str, user: dict = Depends(auth.require("patient", "doctor"))):
    with get_db() as conn:
        row = _participant_row(conn, appt_id, user)
    state = join_state(row)
    if state.get("url"):
        state["join"] = f"/video/{appt_id}"
        del state["url"]  # the room link itself is only handed out by /video/<id>
    return state


MESSAGES = {
    "early": "The video room opens {opens} – {mins} minutes before your appointment. Please come back then.",
    "ended": "This video consultation has ended.",
    "not_confirmed": "This appointment isn't confirmed, so the video room isn't available.",
    "not_video": "This appointment isn't a video consultation.",
}


@router.get("/video/{appt_id}", include_in_schema=False)
def join_video(appt_id: str, user: Optional[dict] = Depends(auth.current_user)):
    """Opens the private room for the patient or doctor of this appointment (only while the call window is open)."""
    if not user:
        return RedirectResponse(f"/login?next=/video/{appt_id}", status_code=303)
    with get_db() as conn:
        row = _participant_row(conn, appt_id, user)
    state = join_state(row)
    if state["state"] == "open":
        return RedirectResponse(state["url"], status_code=303)
    text = MESSAGES[state["state"]].format(opens=state.get("opens_at", "").replace("T", " at "),
                                           mins=settings.VIDEO_JOIN_EARLY_MINUTES)
    back = "/doctor" if user["role"] == "doctor" else "/account#appointments"
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Video consultation</title>
<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml"></head>
<body style="font-family:system-ui,sans-serif;background:#f8fafc;margin:0;display:grid;place-items:center;min-height:100vh">
<main style="max-width:420px;margin:16px;background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:28px;text-align:center">
<div style="font-size:40px">🎥</div><h1 style="font-size:20px;color:#0f172a">Video consultation</h1>
<p style="color:#475569">{escape(text)}</p>
<p><a href="{back}" style="color:#0369a1;font-weight:600">Back to my appointments</a></p></main></body></html>""")


# Imported last, so slices can depend on each other in any order
from features.appointments.logic import appointment_for  # noqa: E402
from features.video_consults.logic import join_state  # noqa: E402

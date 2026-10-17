"""Video consultations: doctors offer online consults; patient and doctor join a private video room.

The room is a long random name on Jitsi Meet (settings.VIDEO_BASE_URL). Nobody's name or email is sent to the video
service, and the room link is only handed out through /video/<appointment> to the two people in the appointment,
from a few minutes before the start until a while after the end.

This file: logic and queries.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException

from shared import settings


def new_room() -> str:
    return "pyshio-" + secrets.token_hex(12)


def check_offers_video(doctor) -> None:
    if not doctor["offers_video"]:
        raise HTTPException(status_code=400, detail="This doctor doesn't offer video consultations")


def consult_fee(doctor, service) -> int:
    """A doctor may set one video fee; otherwise the service's fee applies."""
    return doctor["video_fee"] if doctor["video_fee"] is not None else service["fee"]


def join_window(row) -> tuple:
    start = datetime.strptime(f"{row['appointment_date']} {row['appointment_time']}", "%Y-%m-%d %H:%M")
    return (start - timedelta(minutes=settings.VIDEO_JOIN_EARLY_MINUTES),
            start + timedelta(minutes=row["duration"] + settings.VIDEO_JOIN_LATE_MINUTES))


def join_state(row) -> dict:
    """Whether the call can be joined now: early / open / ended / not_video / not_confirmed."""
    if row["visit_type"] != "video" or not row["video_room"]:
        return {"state": "not_video"}
    opens, closes = join_window(row)
    base = {"opens_at": opens.isoformat(timespec="minutes"), "closes_at": closes.isoformat(timespec="minutes")}
    if row["status"] not in ("confirmed", "completed"):
        return base | {"state": "not_confirmed"}
    now = settings.now().replace(tzinfo=None)
    if now < opens:
        return base | {"state": "early"}
    if now > closes:
        return base | {"state": "ended"}
    return base | {"state": "open", "url": f"{settings.VIDEO_BASE_URL}/{row['video_room']}"}


def join_path(appt_id: str) -> str:
    return f"/video/{appt_id}"

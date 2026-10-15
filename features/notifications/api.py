"""Notifications: the bell in the top bar for every role.

This file: HTTP endpoints and request models.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List

from shared.auth import any_user
from shared.common import stamp
from shared.db import get_db

router = APIRouter()


@router.get("/api/notifications")
def my_notifications(limit: int = 30, unread_only: bool = False, user: dict = Depends(any_user)):
    limit = max(1, min(limit, 100))
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM notifications WHERE user_id = ?" + (" AND read_at IS NULL" if unread_only else "")
                            + " ORDER BY id DESC LIMIT ?", (user["id"], limit)).fetchall()
        unread = conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL",
                              (user["id"],)).fetchone()[0]
    return {"unread": unread, "items": [view(r) for r in rows]}


class ReadIn(BaseModel):
    ids: List[int] = Field(default_factory=list, max_length=200)
    all: bool = False


@router.post("/api/notifications/read")
def mark_read(data: ReadIn, user: dict = Depends(any_user)):
    with get_db() as conn:
        if data.all:
            n = conn.execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL",
                             (stamp(), user["id"])).rowcount
        elif data.ids:
            marks = ",".join("?" * len(data.ids))
            n = conn.execute(f"UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL AND id IN ({marks})",
                             (stamp(), user["id"], *data.ids)).rowcount
        else:
            n = 0
        unread = conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL",
                              (user["id"],)).fetchone()[0]
    return {"marked": n, "unread": unread}


@router.delete("/api/notifications/{notification_id}")
def delete_notification(notification_id: int, user: dict = Depends(any_user)):
    with get_db() as conn:
        if not conn.execute("DELETE FROM notifications WHERE id = ? AND user_id = ?", (notification_id, user["id"])).rowcount:
            raise HTTPException(status_code=404, detail="Notification not found")
    return {"ok": True}


# Imported last, so slices can depend on each other in any order
from features.notifications.logic import view  # noqa: E402

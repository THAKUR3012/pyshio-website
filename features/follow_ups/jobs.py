"""Follow-ups: the doctor sets a follow-up date on a prescription; the patient is reminded and books in one tap.

This file: scheduled jobs (run by jobs.py).
"""
from __future__ import annotations

from datetime import timedelta

from shared import settings
from shared.common import stamp
from shared.db import get_db


def remind_follow_ups() -> int:
    """Remind patients a couple of days before a follow-up the doctor asked for (once, unless already booked)."""
    today = settings.now().date()
    soon = (today + timedelta(days=settings.FOLLOW_UP_REMIND_DAYS)).isoformat()
    sent = 0
    with get_db() as conn:
        rows = conn.execute(FOLLOW_UP_SELECT + " WHERE r.status = 'active' AND r.follow_up_on BETWEEN ? AND ? "
                            "AND r.follow_up_reminded_at IS NULL", (today.isoformat(), soon)).fetchall()
        for row in rows:
            claimed = conn.execute("UPDATE prescriptions SET follow_up_reminded_at = ? WHERE id = ? "
                                   "AND follow_up_reminded_at IS NULL", (stamp(), row["id"])).rowcount
            if not claimed or row["booked_for"]:
                continue
            for_label = f"{row['member_name']} ({row['member_relation']})" if row["member_name"] else None
            send_follow_up_due(row["patient_email"], row["patient_name"], row["doctor_name"], row["follow_up_on"],
                               book_path(row), for_label)
            sent += 1
    return sent


# Imported last, so slices can depend on each other in any order
from features.follow_ups.emails import send_follow_up_due  # noqa: E402
from features.follow_ups.logic import book_path, FOLLOW_UP_SELECT  # noqa: E402

"""Labs: registration, test catalogue and prices, tests sent from prescriptions, bookings and reports.

This file: scheduled jobs (run by jobs.py).
"""
from __future__ import annotations

from datetime import timedelta

from shared import settings
from shared.common import stamp
from shared.db import get_db


def chase_labs() -> int:
    """Remind labs about bookings they haven't accepted yet (once per booking)."""
    before = stamp(settings.now() - timedelta(minutes=settings.LAB_REMIND_MINUTES))
    sent = 0
    with get_db() as conn:
        for r in conn.execute("SELECT id FROM lab_bookings WHERE status = 'booked' AND reminded_at IS NULL AND created_at <= ?",
                              (before,)).fetchall():
            if conn.execute("UPDATE lab_bookings SET reminded_at = ? WHERE id = ? AND reminded_at IS NULL",
                            (stamp(), r["id"])).rowcount:
                sent += labs_emails.send_lab_reminder(labs_logic.full_booking(conn, r["id"]), settings.LAB_REMIND_MINUTES)
    return sent


# Imported last, so slices can depend on each other in any order
from features.labs import emails as labs_emails  # noqa: E402
from features.labs import logic as labs_logic  # noqa: E402

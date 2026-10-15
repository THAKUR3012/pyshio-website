"""Notifications: the bell in the top bar for every role.

This file: scheduled jobs (run by jobs.py).
"""
from __future__ import annotations

from datetime import timedelta

from shared import settings
from shared.common import stamp
from shared.db import get_db


def purge_notifications() -> int:
    """Read notifications older than NOTIFICATION_KEEP_DAYS are deleted; unread ones stay until read."""
    before = stamp(settings.now() - timedelta(days=settings.NOTIFICATION_KEEP_DAYS))
    with get_db() as conn:
        return conn.execute("DELETE FROM notifications WHERE read_at IS NOT NULL AND created_at < ?", (before,)).rowcount

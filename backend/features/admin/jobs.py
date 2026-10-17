"""Admin: users, audit trail, platform stats and housekeeping.

This file: scheduled jobs (run by jobs.py).
"""
from __future__ import annotations

from datetime import timedelta

from shared import settings
from shared.common import stamp
from shared.db import get_db


def purge_old_rows() -> None:
    now = settings.now()
    day_ago = stamp(now - timedelta(days=1))
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (stamp(now),))
        conn.execute("DELETE FROM login_failures WHERE failed_at < ?", (day_ago,))
        conn.execute("DELETE FROM password_resets WHERE expires_at < ? OR (used_at IS NOT NULL AND used_at < ?)",
                     (stamp(now), day_ago))
        conn.execute("DELETE FROM rate_limits WHERE hit_at < ?", (day_ago,))
        conn.execute("DELETE FROM ai_calls WHERE created_at < ?", (stamp(now - timedelta(days=180)),))

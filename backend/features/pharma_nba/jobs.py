"""MR next-best-action and company alerts: scheduled work (run by jobs.py every 5 minutes; alerts are once a week)."""
from __future__ import annotations

from shared import settings
from shared.common import stamp
from shared.db import get_db


def company_anomalies() -> int:
    """Look for unusual product changes for every approved company; email the company admin about new ones."""
    sent = 0
    with get_db() as conn:
        companies = conn.execute("SELECT c.id, c.name, u.email, u.name AS admin_name FROM companies c JOIN users u ON u.id = c.admin_id "
                                 "WHERE c.status = 'approved' AND u.is_active = 1").fetchall()
        today = settings.now().date().isoformat()
        for c in companies:
            # One scan per company per day is plenty (the cron runs every 5 minutes)
            if not conn.execute("INSERT OR IGNORE INTO intel_alerts (kind, subject_id, period, created_at) "
                                "VALUES ('anomaly_scan', ?, ?, ?)", (str(c["id"]), today, stamp())).rowcount:
                continue
            fresh = nba.record_alerts(conn, c["id"])
            if fresh and nba_emails.send_company_alerts(c["email"], c["admin_name"], c["name"], fresh):
                sent += 1
    return sent


# Imported last, so slices can depend on each other in any order
from features.pharma_nba import emails as nba_emails  # noqa: E402
from features.pharma_nba import logic as nba  # noqa: E402

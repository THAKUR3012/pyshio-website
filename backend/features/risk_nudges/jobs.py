"""Risk nudges: scheduled work (run by jobs.py every 5 minutes)."""
from __future__ import annotations

from datetime import timedelta

from shared import settings
from shared.common import stamp
from shared.db import get_db

NUDGE_FROM, NUDGE_TO = timedelta(hours=3), timedelta(hours=6)


def nudge_risky_appointments() -> int:
    """Score upcoming visits; send one "Still coming?" to high-risk ones 3-6 hours before, unless already confirmed."""
    now = settings.now()
    sent = 0
    with get_db() as conn:
        risk.score_upcoming(conn)
        rows = conn.execute(
            appointments_logic.APPOINTMENT_SELECT + " WHERE a.status = 'confirmed' AND a.no_show_risk >= ? "
            "AND a.risk_nudged_at IS NULL AND a.attendance_confirmed_at IS NULL AND a.visit_type != 'video' "
            "AND a.appointment_date BETWEEN ? AND ?",
            (risk.HIGH, now.date().isoformat(), (now + NUDGE_TO).date().isoformat())).fetchall()
        for row in rows:
            start = appointments_logic.appointment_start(row)
            if not now + NUDGE_FROM <= start <= now + NUDGE_TO:
                continue
            if not conn.execute("UPDATE appointments SET risk_nudged_at = ? WHERE id = ? AND risk_nudged_at IS NULL",
                                (stamp(now), row["id"])).rowcount:
                continue
            if risk_emails.send_still_coming(dict(row)):
                sent += 1
            else:
                conn.execute("UPDATE appointments SET risk_nudged_at = NULL WHERE id = ?", (row["id"],))
    return sent


def adherence_alerts() -> int:
    """Once a week at most per patient: a gentle note when medicine-taking has dropped sharply."""
    week = settings.now().strftime("%G-W%V")
    sent = 0
    with get_db() as conn:
        patients = conn.execute(
            "SELECT DISTINCT u.* FROM users u JOIN dose_logs d ON d.patient_id = u.id WHERE u.role = 'patient' AND u.is_active = 1 "
            "AND d.logged_at >= ?", (stamp(settings.now() - timedelta(days=28)),)).fetchall()
        for p in patients:
            drop = risk.adherence_drop(conn, p)
            if not drop:
                continue
            claimed = conn.execute("INSERT OR IGNORE INTO intel_alerts (kind, subject_id, period, payload, created_at) "
                                   "VALUES ('adherence', ?, ?, ?, ?)", (str(p["id"]), week, None, stamp())).rowcount
            if claimed and risk_emails.send_adherence_nudge(p["email"], p["name"], drop):
                sent += 1
    return sent


# Imported last, so slices can depend on each other in any order
from features.appointments import logic as appointments_logic  # noqa: E402
from features.risk_nudges import emails as risk_emails  # noqa: E402
from features.risk_nudges import logic as risk  # noqa: E402

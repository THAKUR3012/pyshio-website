"""Medicine reminders: today's doses from prescriptions and own medicines, taken / skipped log and adherence.

This file: scheduled jobs (run by jobs.py).
"""
from __future__ import annotations

from datetime import timedelta

from shared import settings
from shared.db import get_db


def dose_digest() -> int:
    """Patients who asked for it get one 'today's medicines' email, once their morning dose time has come."""
    now = settings.now()
    today = now.date().isoformat()
    sent = 0
    with get_db() as conn:
        users = conn.execute("SELECT * FROM users WHERE role = 'patient' AND dose_email_digest = 1 AND deleted_at IS NULL "
                             "AND is_active = 1 AND (dose_digest_on IS NULL OR dose_digest_on < ?)", (today,)).fetchall()
        for user in users:
            if now.strftime("%H:%M") < times_for(user)["morning"]:
                continue
            claimed = conn.execute("UPDATE users SET dose_digest_on = ? WHERE id = ? AND (dose_digest_on IS NULL "
                                   "OR dose_digest_on < ?)", (today, user["id"], today)).rowcount
            if not claimed:
                continue
            schedule = day_schedule(conn, user, now.date())
            if schedule["summary"]["due"]:
                sent += send_dose_digest(user["email"], user["name"], schedule)
    return sent


def dose_nudges() -> int:
    """'Time for your night medicines' in the bell (and as a device pop-up when the app is open) for patients who
    track their doses, within an hour after each dose time while doses are still unmarked."""
    now = settings.now()
    hhmm = now.strftime("%H:%M")
    hour_ago = (now - timedelta(hours=1)).strftime("%H:%M")
    week_ago = (now.date() - timedelta(days=7)).isoformat()
    sent = 0
    with get_db() as conn:
        users = conn.execute("SELECT * FROM users WHERE role = 'patient' AND is_active = 1 AND deleted_at IS NULL AND "
                             "(dose_email_digest = 1 OR id IN (SELECT DISTINCT patient_id FROM dose_logs WHERE dose_date >= ?))",
                             (week_ago,)).fetchall()
        for user in users:
            times = times_for(user)
            due_slots = [s for s, t in times.items() if hour_ago < t <= hhmm] if hour_ago < hhmm else []
            if not due_slots:
                continue
            schedule = day_schedule(conn, user, now.date())
            for group in schedule["groups"]:
                pending = [d for d in group["doses"] if d["status"] == "pending"]
                if group["slot"] in due_slots and pending:
                    names = ", ".join(d["name"] for d in pending[:4]) + (" …" if len(pending) > 4 else "")
                    if notifications.add(conn, user["id"], f"Time for your {group['label'].lower()} medicines",
                                         names, "/account#today", "reminder"):
                        sent += 1
    return sent


# Imported last, so slices can depend on each other in any order
from features.medicine_reminders.emails import send_dose_digest  # noqa: E402
from features.medicine_reminders.logic import day_schedule, times_for  # noqa: E402
from features.notifications import logic as notifications  # noqa: E402

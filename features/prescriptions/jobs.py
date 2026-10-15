"""Prescriptions: doctors write them (medicines, tests, diagnoses, refills); patients and pharmacies read them.

This file: scheduled jobs (run by jobs.py).
"""
from __future__ import annotations

from datetime import timedelta

from shared import settings
from shared.common import stamp
from shared.db import get_db


def nudge_expiring_prescriptions() -> int:
    now = settings.now()
    soon = stamp(now + timedelta(days=settings.RX_EXPIRY_REMIND_DAYS))
    sent = 0
    with get_db() as conn:
        rows = conn.execute(prescriptions_logic.RX_SELECT + " WHERE r.status = 'active' AND r.valid_until > ? AND r.valid_until <= ? "
                            "AND r.expiry_reminded_at IS NULL", (stamp(now), soon)).fetchall()
        for row in rows:
            remaining = {i["id"] for i in prescriptions_logic.rx_items(conn, row["id"])} - prescriptions_logic.ordered_item_ids(conn, row["id"])
            conn.execute("UPDATE prescriptions SET expiry_reminded_at = ? WHERE id = ?", (stamp(), row["id"]))
            if remaining:
                sent += prescriptions_emails.send_rx_expiring(row["patient_email"], row["patient_name"], row["reference"],
                                                row["valid_until"])
    return sent


def remind_refills() -> int:
    """Patients on long-term medicines with refills left hear from us before their supply runs out."""
    today = settings.now().date()
    soon = (today + timedelta(days=settings.REFILL_REMIND_DAYS)).isoformat()
    sent = 0
    with get_db() as conn:
        due = conn.execute(
            "SELECT c.prescription_id, MIN(c.ends_on) AS ends_on, GROUP_CONCAT(c.name, ', ') AS medicines "
            "FROM courses c JOIN prescriptions r ON r.id = c.prescription_id "
            "WHERE c.is_long_term = 1 AND c.ends_on <= ? AND c.ends_on >= ? AND r.refill_reminded_at IS NULL "
            "GROUP BY c.prescription_id", (soon, (today - timedelta(days=7)).isoformat())).fetchall()
        for d in due:
            row = conn.execute(prescriptions_logic.RX_SELECT + " WHERE r.id = ?", (d["prescription_id"],)).fetchone()
            root_id = row["parent_id"] or row["id"]
            newer = conn.execute("SELECT 1 FROM prescriptions WHERE (id = ? OR parent_id = ?) AND created_at > ? "
                                 "AND status = 'active'", (root_id, root_id, row["created_at"])).fetchone()
            claimed = conn.execute("UPDATE prescriptions SET refill_reminded_at = ? WHERE id = ? "
                                   "AND refill_reminded_at IS NULL", (stamp(), row["id"])).rowcount
            status = prescriptions_logic.refill_status(conn, row)
            if not claimed or newer or not status["left"] or status["reason"] and not status["available_from"]:
                continue  # already refilled, no refills left, or refills expired / cancelled
            root = conn.execute("SELECT reference FROM prescriptions WHERE id = ?", (root_id,)).fetchone()
            sent += prescriptions_emails.send_refill_due(row["patient_email"], row["patient_name"], root["reference"], d["ends_on"],
                                           status["left"], (d["medicines"] or "").split(", "))
    return sent


# Imported last, so slices can depend on each other in any order
from features.prescriptions import emails as prescriptions_emails  # noqa: E402
from features.prescriptions import logic as prescriptions_logic  # noqa: E402

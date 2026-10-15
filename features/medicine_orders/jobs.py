"""Medicine orders: the patient orders from a pharmacy, pays, and the pharmacy fulfils it.

This file: scheduled jobs (run by jobs.py).
"""
from __future__ import annotations

from datetime import timedelta

from shared import settings
from shared.common import RunNow, stamp
from shared.db import get_db, write_txn


def chase_pharmacies() -> dict:
    """Remind pharmacies about placed orders; auto-reject the ones left too long so patients aren't stuck."""
    now = settings.now()
    remind_before = stamp(now - timedelta(minutes=settings.ORDER_REMIND_MINUTES))
    reject_before = stamp(now - timedelta(minutes=settings.ORDER_ACCEPT_MINUTES))
    reminded = rejected = 0
    with get_db() as conn:
        due = conn.execute("SELECT id FROM medicine_orders WHERE status = 'placed' AND updated_at <= ? "
                           "AND reminded_at IS NULL AND updated_at > ?", (remind_before, reject_before)).fetchall()
        for r in due:
            claimed = conn.execute("UPDATE medicine_orders SET reminded_at = ? WHERE id = ? AND reminded_at IS NULL",
                                   (stamp(), r["id"])).rowcount
            if claimed:
                medicine_orders_emails.send_order_reminder(medicine_orders_logic.email_view(conn, r["id"]), settings.ORDER_REMIND_MINUTES)
                reminded += 1
        late = conn.execute("SELECT * FROM medicine_orders WHERE status = 'placed' AND updated_at <= ?",
                            (reject_before,)).fetchall()
    for r in late:
        with get_db() as conn:
            with write_txn(conn):
                cur = conn.execute("UPDATE medicine_orders SET status = 'rejected', status_reason = ?, updated_at = ? "
                                   "WHERE id = ? AND status = 'placed'",
                                   (f"The pharmacy didn't accept within {settings.ORDER_ACCEPT_MINUTES} minutes",
                                    stamp(), r["id"]))
                if not cur.rowcount:
                    continue
                medicine_orders_logic.reopen_requests(conn, r["prescription_id"])
        note = medicine_orders_logic.refund_order(r["id"]) if r["payment_status"] == "paid" else None
        note = (note + " " if note else "") + "Your prescription is open again: pick the next best pharmacy nearby."
        with get_db() as conn:
            medicine_orders_logic.notify_order(RunNow(), conn, r["id"], ["patient", "pharmacy"], note)
        rejected += 1
    return {"reminded": reminded, "auto_rejected": rejected}


# Imported last, so slices can depend on each other in any order
from features.medicine_orders import emails as medicine_orders_emails  # noqa: E402
from features.medicine_orders import logic as medicine_orders_logic  # noqa: E402

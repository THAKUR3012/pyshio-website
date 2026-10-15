"""Small helpers shared by the API modules."""
import logging
import secrets
from datetime import datetime, timedelta

from shared import settings

log = logging.getLogger("pyshio.common")


REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def stamp(dt: datetime = None) -> str:
    return (dt or settings.now()).isoformat(timespec="seconds")


def new_reference(conn, table: str, prefix: str) -> str:
    """Short human-friendly unique reference, e.g. PY-7KQ2MX, RX-…, MO-…"""
    while True:
        ref = f"{prefix}-" + "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(6))
        if not conn.execute(f"SELECT 1 FROM {table} WHERE reference = ?", (ref,)).fetchone():
            return ref


def paise(rupees) -> int:
    """₹ amount (int/float/str) -> integer paise, avoiding float drift."""
    return int(round(float(rupees) * 100))


def hold_cutoff() -> str:
    """Unpaid online bookings/orders created before this no longer hold anything."""
    return stamp(settings.now() - timedelta(minutes=settings.PAYMENT_HOLD_MINUTES))


def csv_safe(value) -> str:
    """Stop spreadsheet apps treating user-entered text as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def audit(conn, actor_id, action: str, target: str = None, detail: str = None) -> None:
    """Record who did what (admin actions, account changes) for later review."""
    conn.execute("INSERT INTO audit_log (actor_id, action, target, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                 (actor_id, action, target, detail, stamp()))


class RunNow:
    """Stands in for FastAPI's BackgroundTasks: run each task immediately."""
    def add_task(self, fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
        except Exception:
            log.exception("Background task %s failed", getattr(fn, "__name__", fn))

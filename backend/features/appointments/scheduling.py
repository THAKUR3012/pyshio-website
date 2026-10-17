"""Appointments: booking, online payment, cancel / reschedule, messages, receipts, walk-ins, doctor and admin appointment lists, slot scheduling and reminders.

This file: scheduling.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from fastapi import HTTPException
from typing import List, Optional, Tuple

from shared import settings


# Statuses that occupy the doctor's time
# (home-visit requests hold the time while the doctor sends charges and the patient answers)
BUSY_STATUSES = ("confirmed", "completed", "no_show", "requested", "awaiting_patient")


def to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid date. Use YYYY-MM-DD")


def hold_cutoff() -> str:
    """Unpaid online bookings older than this no longer hold their slot."""
    return (settings.now() - timedelta(minutes=settings.PAYMENT_HOLD_MINUTES)).isoformat(timespec="seconds")


def expire_stale_holds(conn) -> None:
    now = settings.now()
    conn.execute(
        "UPDATE appointments SET status = 'expired', updated_at = ? "
        "WHERE status = 'pending_payment' AND created_at <= ?",
        (now.isoformat(timespec="seconds"), hold_cutoff())
    )
    # Home-visit requests nobody finalised before the visit time
    conn.execute("UPDATE appointments SET status = 'expired', updated_at = ? WHERE status IN ('requested', 'awaiting_patient') "
                 "AND appointment_date || ' ' || appointment_time <= ?", (now.isoformat(timespec="seconds"), now.strftime("%Y-%m-%d %H:%M")))


def working_windows(conn, doctor_id: int, d: date) -> List[Tuple[int, int]]:
    rows = conn.execute(
        "SELECT start_time, end_time FROM doctor_hours WHERE doctor_id = ? AND weekday = ? ORDER BY start_time",
        (doctor_id, d.weekday())
    ).fetchall()
    return [(to_minutes(r["start_time"]), to_minutes(r["end_time"])) for r in rows]


def busy_intervals(conn, doctor_id: int, date_str: str, exclude_id: Optional[str] = None) -> List[Tuple[int, int]]:
    busy = []
    rows = conn.execute(
        f"SELECT id, appointment_time, duration, status, created_at FROM appointments "
        f"WHERE doctor_id = ? AND appointment_date = ? AND (status IN ({','.join('?' * len(BUSY_STATUSES))}) "
        f"OR (status = 'pending_payment' AND created_at > ?))",
        (doctor_id, date_str, *BUSY_STATUSES, hold_cutoff())
    ).fetchall()
    for r in rows:
        if r["id"] != exclude_id:
            start = to_minutes(r["appointment_time"])
            busy.append((start, start + r["duration"]))
    for b in conn.execute("SELECT start_time, end_time FROM blocked_times WHERE doctor_id = ? AND block_date = ?",
                          (doctor_id, date_str)):
        busy.append((to_minutes(b["start_time"]) if b["start_time"] else 0,
                     to_minutes(b["end_time"]) if b["end_time"] else 24 * 60))
    return busy


def candidate_starts(windows, slot_minutes: int, duration: int) -> List[int]:
    starts = []
    for w_start, w_end in windows:
        t = w_start
        while t + duration <= w_end:
            starts.append(t)
            t += slot_minutes
    return sorted(set(starts))


def bookable_range() -> Tuple[date, date]:
    today = settings.now().date()
    return today, today + timedelta(days=settings.MAX_ADVANCE_DAYS)


def day_availability(conn, doctor, d: date, duration: int, exclude_id: Optional[str] = None) -> dict:
    """{'open': bool, 'all_slots': [...], 'slots': [...free...], 'message'?: str}"""
    first, last = bookable_range()
    windows = working_windows(conn, doctor["user_id"], d)
    if not windows:
        return {"open": False, "all_slots": [], "slots": [], "message": "Not available on this day"}
    starts = candidate_starts(windows, doctor["slot_minutes"], duration)
    all_slots = [to_hhmm(s) for s in starts]
    if not first <= d <= last:
        return {"open": True, "all_slots": all_slots, "slots": [], "message": "This date can't be booked online"}

    busy = busy_intervals(conn, doctor["user_id"], d.isoformat(), exclude_id)
    now = settings.now()
    free = []
    for s in starts:
        if d == now.date() and s <= now.hour * 60 + now.minute:
            continue
        if any(s < b_end and s + duration > b_start for b_start, b_end in busy):
            continue
        free.append(to_hhmm(s))
    return {"open": True, "all_slots": all_slots, "slots": free}


def check_slot(conn, doctor, date_str: str, time_str: str, duration: int, exclude_id: Optional[str] = None) -> None:
    """Raise unless the doctor is free for the whole appointment."""
    d = parse_date(date_str)
    first, last = bookable_range()
    if d < first:
        raise HTTPException(status_code=400, detail="Cannot book in the past")
    if d > last:
        raise HTTPException(status_code=400, detail=f"Bookings open up to {settings.MAX_ADVANCE_DAYS} days ahead")
    day = day_availability(conn, doctor, d, duration, exclude_id)
    if not day["open"]:
        raise HTTPException(status_code=400, detail="The doctor isn't available on this day")
    if time_str not in day["all_slots"]:
        raise HTTPException(status_code=400, detail="Invalid time slot")
    if time_str not in day["slots"]:
        raise HTTPException(status_code=409, detail="This time slot is no longer available. Please choose another.")

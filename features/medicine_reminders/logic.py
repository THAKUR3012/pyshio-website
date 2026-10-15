"""Medicine reminders: today's doses from prescriptions and own medicines, taken / skipped log and adherence.

Where doses come from (for one patient, including family members they booked for):
  - courses: medicines bought through a Pyshio pharmacy, from purchase until the supply runs out
  - prescription items not bought here, for their prescribed duration (30 days when none is given)
  - "My other medicines" the patient added themself, while marked active
The dosage text decides the times: "1-0-1" = morning + night, "1-1-1" = morning, afternoon, night,
four parts add an evening dose, words like OD / BD / TDS / HS work too. "SOS" or unreadable text = as needed.

This file: logic and queries.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Optional

from shared import settings

SLOTS = ["morning", "afternoon", "evening", "night"]
SLOT_LABELS = {"morning": "Morning", "afternoon": "Afternoon", "evening": "Evening", "night": "Night"}
DEFAULT_DAYS = 30
SHAPES = {2: ["morning", "night"], 3: ["morning", "afternoon", "night"], 4: ["morning", "afternoon", "evening", "night"]}
WORDS = [  # (pattern, slots) checked in order
    (r"\b(qid|four times)\b", ["morning", "afternoon", "evening", "night"]),
    (r"\b(tds|tid|thrice|three times)\b", ["morning", "afternoon", "night"]),
    (r"\b(bd|bid|twice|two times)\b", ["morning", "night"]),
    (r"\b(hs|bedtime|at night|night)\b", ["night"]),
    (r"\b(od|qd|once|daily|morning|every day|a day)\b", ["morning"]),
]
NOT_DAILY = re.compile(r"\b(sos|prn|as needed|if needed|when needed|week|weekly|month|monthly|alternate)\b")
PART = re.compile(r"^\s*(\d+(?:\.\d+)?|\d+/\d+|½|¼|¾)\s*$")


def slots_for(dosage: Optional[str]) -> list:
    """[(slot, amount)] for a dosage string; [] means not a daily schedule (as needed / weekly / unreadable)."""
    text = (dosage or "").strip().lower()
    if not text or NOT_DAILY.search(text):
        return []
    parts = re.split(r"\s*-\s*", text)
    if 2 <= len(parts) <= 4 and all(PART.match(p) for p in parts):
        return [(slot, part.strip()) for slot, part in zip(SHAPES[len(parts)], parts) if part.strip() not in ("0", "0.0")]
    for pattern, slots in WORDS:
        if re.search(pattern, text):
            amount = re.match(r"^\s*(\d+(?:\.\d+)?|½|¼|¾)", text)
            return [(s, amount.group(1) if amount else "1") for s in slots]
    return []


def times_for(user) -> dict:
    """Dose times for this patient (their own choice, else the platform defaults)."""
    times = dict(settings.DOSE_TIMES)
    try:
        times.update(json.loads(user["remind_times"] or "{}"))
    except (ValueError, TypeError):
        pass
    return times


# ---------- What the patient is taking ----------
def medicines(conn, patient_id: int, start: date, end: date) -> list:
    """Every medicine active at some point between start and end, with the days it's active."""
    out = []
    for r in conn.execute(
            "SELECT c.id, c.name, c.strength, c.started_on, c.ends_on, i.dosage, i.instructions, fm.name AS member_name "
            "FROM courses c JOIN prescription_items i ON i.id = c.prescription_item_id "
            "JOIN prescriptions r ON r.id = c.prescription_id LEFT JOIN family_members fm ON fm.id = r.family_member_id "
            "WHERE c.patient_id = ? AND c.started_on <= ? AND (c.ends_on IS NULL OR c.ends_on >= ?)",
            (patient_id, end.isoformat(), start.isoformat())):
        out.append({"source": "course", "source_id": str(r["id"]), "name": r["name"], "strength": r["strength"],
                    "dosage": r["dosage"], "instructions": r["instructions"], "for_name": r["member_name"],
                    "from": r["started_on"], "until": r["ends_on"] or end.isoformat()})
    bought = {r[0] for r in conn.execute("SELECT prescription_item_id FROM courses WHERE patient_id = ?", (patient_id,))}
    for r in conn.execute(
            "SELECT i.id, i.name, i.strength, i.dosage, i.instructions, i.duration_days, substr(r.created_at, 1, 10) AS day, "
            "fm.name AS member_name FROM prescription_items i JOIN prescriptions r ON r.id = i.prescription_id "
            "LEFT JOIN family_members fm ON fm.id = r.family_member_id "
            "WHERE r.patient_id = ? AND r.status = 'active' AND substr(r.created_at, 1, 10) <= ?",
            (patient_id, end.isoformat())):
        if r["id"] in bought:
            continue
        first = date.fromisoformat(r["day"])
        last = first + timedelta(days=(r["duration_days"] or DEFAULT_DAYS) - 1)
        if last < start:
            continue
        out.append({"source": "rx", "source_id": str(r["id"]), "name": r["name"], "strength": r["strength"],
                    "dosage": r["dosage"], "instructions": r["instructions"], "for_name": r["member_name"],
                    "from": first.isoformat(), "until": last.isoformat()})
    for r in conn.execute("SELECT * FROM patient_medicines WHERE patient_id = ? AND active = 1", (patient_id,)):
        out.append({"source": "own", "source_id": str(r["id"]), "name": r["name"], "strength": r["strength"],
                    "dosage": r["dosage"], "instructions": r["notes"], "for_name": None,
                    "from": (r["since"] or r["created_at"][:10]), "until": end.isoformat()})
    for m in out:
        m["slots"] = slots_for(m["dosage"])
    return out


def active_on(med: dict, day: date) -> bool:
    return med["from"] <= day.isoformat() <= med["until"]


def logs(conn, patient_id: int, start: date, end: date) -> dict:
    return {(r["source"], r["source_id"], r["dose_date"], r["slot"]): r["status"] for r in conn.execute(
        "SELECT source, source_id, dose_date, slot, status FROM dose_logs WHERE patient_id = ? AND dose_date BETWEEN ? AND ?",
        (patient_id, start.isoformat(), end.isoformat()))}


def day_schedule(conn, user, day: date) -> dict:
    """Doses for one day grouped by time of day, each with its taken / skipped / pending status."""
    meds = [m for m in medicines(conn, user["id"], day, day) if active_on(m, day)]
    logged = logs(conn, user["id"], day, day)
    times = times_for(user)
    now = settings.now()
    slots = {s: [] for s in SLOTS}
    as_needed = []
    for m in meds:
        base = {k: m[k] for k in ("source", "source_id", "name", "strength", "dosage", "instructions", "for_name")}
        if not m["slots"]:
            as_needed.append(base)
            continue
        for slot, amount in m["slots"]:
            status = logged.get((m["source"], m["source_id"], day.isoformat(), slot), "pending")
            slots[slot].append(base | {"slot": slot, "amount": amount, "status": status})
    due = sum(len(v) for v in slots.values())
    summary = {"due": due, "taken": sum(1 for v in slots.values() for d in v if d["status"] == "taken"),
               "skipped": sum(1 for v in slots.values() for d in v if d["status"] == "skipped")}
    groups = [{"slot": s, "label": SLOT_LABELS[s], "time": times[s], "doses": slots[s],
               "past": day < now.date() or (day == now.date() and now.strftime("%H:%M") >= times[s])}
              for s in SLOTS if slots[s]]
    return {"date": day.isoformat(), "times": times, "groups": groups, "as_needed": as_needed, "summary": summary}


def scheduled_doses(conn, patient_id: int, start: date, end: date, times: dict) -> list:
    """[(med, day, slot)] for every dose that was due in the range (today's only once its time has passed)."""
    now = settings.now()
    out = []
    for m in medicines(conn, patient_id, start, end):
        day = max(start, date.fromisoformat(m["from"]))
        last = min(end, date.fromisoformat(m["until"]))
        while day <= last:
            for slot, _ in m["slots"]:
                if day < now.date() or (day == now.date() and now.strftime("%H:%M") >= times[slot]):
                    out.append((m, day, slot))
            day += timedelta(days=1)
    return out


def adherence(conn, user, days: int = 30) -> dict:
    """Share of due doses marked taken: overall, per medicine and per day (last 7 days)."""
    end = settings.now().date()
    start = end - timedelta(days=days - 1)
    doses = scheduled_doses(conn, user["id"], start, end, times_for(user))
    logged = logs(conn, user["id"], start, end)
    per_med, per_day = {}, {}
    taken = 0
    for m, day, slot in doses:
        ok = logged.get((m["source"], m["source_id"], day.isoformat(), slot)) == "taken"
        taken += ok
        key = (m["source"], m["source_id"])
        rec = per_med.setdefault(key, {"name": m["name"], "strength": m["strength"], "for_name": m["for_name"],
                                       "due": 0, "taken": 0})
        rec["due"] += 1
        rec["taken"] += ok
        d = per_day.setdefault(day.isoformat(), [0, 0])
        d[0] += 1
        d[1] += ok

    def pct(t, d):
        return round(100 * t / d) if d else None

    week = [(end - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    return {"days": days, "due": len(doses), "taken": taken, "percent": pct(taken, len(doses)),
            "medicines": sorted(({**r, "percent": pct(r["taken"], r["due"])} for r in per_med.values()),
                                key=lambda r: (r["percent"] if r["percent"] is not None else 101, r["name"])),
            "week": [{"date": d, "due": per_day.get(d, [0, 0])[0], "taken": per_day.get(d, [0, 0])[1],
                      "percent": pct(per_day.get(d, [0, 0])[1], per_day.get(d, [0, 0])[0])} for d in week]}


def is_scheduled(conn, user, source: str, source_id: str, day: date, slot: str) -> bool:
    for m in medicines(conn, user["id"], day, day):
        if m["source"] == source and m["source_id"] == source_id and active_on(m, day):
            return slot in [s for s, _ in m["slots"]]
    return False

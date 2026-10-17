"""Risk nudges: a no-show score for upcoming visits with one "Still coming?" nudge, and gentle alerts when medicine-taking drops.

This file: logic. Transparent points, no model: every factor can be explained to a doctor. The score is only ever
shown to the doctor as a band ("may need a reminder"), never to the patient, and never blocks a booking.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from shared import geo, settings

HIGH, MEDIUM = 60, 35


def no_show_score(conn, appt) -> dict:
    """0-100 with the reasons that added to it."""
    reasons, points = [], 10
    past = conn.execute("SELECT SUM(status = 'no_show') AS missed, SUM(status IN ('completed', 'no_show')) AS seen "
                        "FROM appointments WHERE patient_id = ? AND id != ?", (appt["patient_id"], appt["id"])).fetchone()
    missed, seen = past["missed"] or 0, past["seen"] or 0
    history = round(60 * (missed + 0.5) / (seen + 2))  # smoothed: little history counts for little
    points += history
    if missed:
        reasons.append(f"missed {missed} of {seen} past visits")
    created = datetime.fromisoformat(appt["created_at"][:19])
    start = datetime.strptime(f"{appt['appointment_date']} {appt['appointment_time']}", "%Y-%m-%d %H:%M")
    lead = (start - created).days
    if lead > 21:
        points += 15
        reasons.append("booked over 3 weeks ahead")
    elif lead > 7:
        points += 10
        reasons.append("booked over a week ahead")
    if appt["payment_status"] == "paid":
        points -= 10
    elif appt["payment_method"] == "clinic":
        points += 15
        reasons.append("pays at the clinic")
    first = not conn.execute("SELECT 1 FROM appointments WHERE patient_id = ? AND doctor_id = ? AND status = 'completed' "
                             "AND id != ? LIMIT 1", (appt["patient_id"], appt["doctor_id"], appt["id"])).fetchone()
    if first:
        points += 10
        reasons.append("first visit with this doctor")
    if appt["appointment_time"] < "09:30":
        points += 5
        reasons.append("early-morning slot")
    if appt["visit_type"] == "clinic":
        home = conn.execute("SELECT home_lat, home_lng FROM users WHERE id = ?", (appt["patient_id"],)).fetchone()
        clinic = conn.execute("SELECT lat, lng FROM doctors WHERE user_id = ?", (appt["doctor_id"],)).fetchone()
        if home and clinic and None not in (home["home_lat"], clinic["lat"]) \
                and geo.haversine_km(home["home_lat"], home["home_lng"], clinic["lat"], clinic["lng"]) > 10:
            points += 10
            reasons.append("lives over 10 km away")
    score = max(0, min(100, points))
    return {"score": score, "band": band(score), "reasons": reasons}


def band(score: Optional[int]) -> Optional[str]:
    if score is None:
        return None
    return "high" if score >= HIGH else "medium" if score >= MEDIUM else "low"


def score_upcoming(conn, days: int = 3) -> int:
    """(Re)score confirmed visits in the next few days."""
    today = settings.now().date()
    rows = conn.execute("SELECT * FROM appointments WHERE status = 'confirmed' AND appointment_date BETWEEN ? AND date(?, ?)",
                        (today.isoformat(), today.isoformat(), f"+{days} days")).fetchall()
    for r in rows:
        conn.execute("UPDATE appointments SET no_show_risk = ? WHERE id = ?", (no_show_score(conn, r)["score"], r["id"]))
    return len(rows)


def adherence_drop(conn, patient) -> Optional[dict]:
    """Last 7 days below 60% and at least 25 points under the 3 weeks before (with enough doses to judge)."""
    recent = medicine_reminders.adherence(conn, patient, 7)
    month = medicine_reminders.adherence(conn, patient, 28)
    if not recent["due"] or recent["due"] < 5 or recent["percent"] is None:
        return None
    before_due, before_taken = month["due"] - recent["due"], month["taken"] - recent["taken"]
    if before_due < 10:
        return None
    before = round(100 * before_taken / before_due)
    if recent["percent"] < 60 and before - recent["percent"] >= 25:
        return {"recent": recent["percent"], "before": before,
                "worst": next((m["name"] for m in recent["medicines"] if m["percent"] is not None), None)}
    return None


# Imported last, so slices can depend on each other in any order
from features.medicine_reminders import logic as medicine_reminders  # noqa: E402

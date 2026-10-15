"""Prescriptions: doctors write them (medicines, tests, diagnoses, refills); patients and pharmacies read them.

This file: logic and queries.
"""
from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timedelta
from fastapi import HTTPException
from typing import Optional

from shared import settings
from shared.common import hold_cutoff, new_reference, stamp


DOSE_WORDS = {"od": 1, "hs": 1, "qd": 1, "once daily": 1, "bd": 2, "bid": 2, "twice daily": 2,
              "tds": 3, "tid": 3, "thrice daily": 3, "qid": 4}


def daily_units_from(dosage: Optional[str]) -> Optional[float]:
    """'1-0-1' -> 2, '1/2-0-1/2' -> 1, 'BD' -> 2. None when it can't be read (e.g. 'SOS')."""
    text = (dosage or "").strip().lower().replace("½", "0.5")
    if not text:
        return None
    if text in DOSE_WORDS:
        return DOSE_WORDS[text]
    parts = re.split(r"\s*-\s*", text)
    if len(parts) in (2, 3, 4):
        total = 0.0
        for part in parts:
            if re.fullmatch(r"\d+(\.\d+)?", part):
                total += float(part)
            elif re.fullmatch(r"(\d+)/(\d+)", part):
                a, b = part.split("/")
                total += int(a) / int(b) if int(b) else 0
            else:
                return None
        return total or None
    return None


def days_from(duration: Optional[str]) -> Optional[int]:
    """'5 days' -> 5, '2 weeks' -> 14, '3 months' -> 90."""
    m = re.fullmatch(r"\s*(\d+)\s*(d|day|days|w|wk|week|weeks|m|month|months)?\s*", (duration or "").lower())
    if not m:
        return None
    n = int(m.group(1))
    unit = (m.group(2) or "d")[0]
    days = n * {"d": 1, "w": 7, "m": 30}[unit]
    return days if 1 <= days <= 365 else None


# ---------- Prescriptions ----------
RX_SELECT = """
    SELECT r.*, du.name AS doctor_name, du.email AS doctor_email, d.qualification, d.registration_no,
           d.registration_council, d.category, d.clinic_name, d.address AS clinic_address, d.city AS clinic_city,
           d.lat AS clinic_lat, d.lng AS clinic_lng,
           pu.name AS patient_name, pu.email AS patient_email, pu.phone AS patient_phone,
           pu.home_lat, pu.home_lng, pu.home_address, pu.share_consent_at,
           fm.name AS member_name, fm.relation AS member_relation
    FROM prescriptions r
    LEFT JOIN family_members fm ON fm.id = r.family_member_id
    JOIN users du ON du.id = r.doctor_id
    JOIN doctors d ON d.user_id = r.doctor_id
    JOIN users pu ON pu.id = r.patient_id
"""


def rx_items(conn, rx_id: str) -> list:
    return [dict(r) for r in conn.execute("SELECT * FROM prescription_items WHERE prescription_id = ? ORDER BY id",
                                          (rx_id,))]


def rx_is_live(row) -> bool:
    return row["status"] == "active" and row["valid_until"] > stamp()


# Unpaid online orders stop counting once their payment window has passed (cleanup may not have run yet)
LIVE_ORDER = "NOT (o.status = 'pending_payment' AND o.created_at <= ?)"


def ordered_item_ids(conn, rx_id: str, exclude_order_id: str = None) -> set:
    rows = conn.execute(
        f"SELECT oi.prescription_item_id FROM order_items oi JOIN medicine_orders o ON o.id = oi.order_id "
        f"WHERE o.prescription_id = ? AND o.status IN ({','.join('?' * len(ACTIVE_ORDER_STATUSES))}) "
        f"AND {LIVE_ORDER} AND o.id != ?",
        (rx_id, *ACTIVE_ORDER_STATUSES, hold_cutoff(), exclude_order_id or ""))
    return {r[0] for r in rows}


def rx_view(conn, row, level: str) -> dict:
    """level: 'full' (patient/doctor/admin), 'pharmacy' (request only: no patient identity), 'pharmacy_order'."""
    view = {k: row[k] for k in ("id", "reference", "status", "created_at", "valid_until", "radius_km")}
    if level in ("full", "pharmacy_order"):
        # Clinical details stay private until the patient chooses this pharmacy
        view.update(diagnosis=row["diagnosis"], advice=row["advice"])
    view["doctor"] = {"name": row["doctor_name"], "qualification": row["qualification"],
                      "registration_no": row["registration_no"], "registration_council": row["registration_council"],
                      "category": settings.DOCTOR_CATEGORIES.get(row["category"], {}).get("label", row["category"]),
                      "clinic": f"{row['clinic_name']}, {row['clinic_address']}, {row['clinic_city']}"}
    items = rx_items(conn, row["id"])
    view["items"] = [{k: i[k] for k in ("id", "medicine_id", "name", "generic_name", "strength", "form", "dosage",
                                         "duration", "quantity", "instructions", "duration_days", "daily_units")}
                     | {"allow_substitute": bool(i["allow_substitute"]), "is_long_term": bool(i["is_long_term"])}
                     for i in items]
    view["live"] = rx_is_live(row)
    view["refill_number"] = row["refill_number"]
    if row["parent_id"]:
        parent = conn.execute("SELECT reference FROM prescriptions WHERE id = ?", (row["parent_id"],)).fetchone()
        view["refill_of"] = parent["reference"] if parent else None
    if level in ("full", "pharmacy_order"):
        view["patient"] = {"name": row["member_name"] or row["patient_name"], "age": row["patient_age"],
                           "sex": row["patient_sex"], "relation": row["member_relation"],
                           "booked_by": row["patient_name"] if row["member_name"] else None}
        view["conditions"] = rx_conditions(conn, row["id"])
    if level == "full":
        view["refills"] = refill_status(conn, row)
        tests = labs.rx_tests(conn, row["id"])
        booked = labs.booked_test_ids(conn, row["id"])
        view["tests"] = [dict(t, booked=t["id"] in booked) for t in tests]
        view["remaining_test_ids"] = [t["id"] for t in tests if t["id"] not in booked]
        view["lab_bookings"] = labs.booking_summaries(conn, row["id"])
        view["appointment_id"] = row["appointment_id"]
        view["follow_up_on"] = row["follow_up_on"]
        view["family_member_id"] = row["family_member_id"]
        view["has_location"] = row["lat"] is not None
        view["location_source"] = row["location_source"]
        ordered = ordered_item_ids(conn, row["id"])
        view["remaining_item_ids"] = [i["id"] for i in items if i["id"] not in ordered]
        view["orders"] = [order_summary(r) for r in conn.execute(
            ORDER_SELECT + f" WHERE o.prescription_id = ? AND o.status != 'expired' AND {LIVE_ORDER} ORDER BY o.created_at",
            (row["id"], hold_cutoff()))]
    return view


def rx_for(conn, rx_id: str, user: dict):
    """Load a prescription and decide how much of it this user may see; 404 if nothing."""
    row = conn.execute(RX_SELECT + " WHERE r.id = ?", (rx_id,)).fetchone()
    if row:
        if user["role"] == "admin" or (user["role"] == "patient" and row["patient_id"] == user["id"]) \
                or (user["role"] == "doctor" and row["doctor_id"] == user["id"]):
            return row, "full"
        if user["role"] == "pharmacy":
            if conn.execute("SELECT 1 FROM medicine_orders WHERE prescription_id = ? AND pharmacy_id = ? "
                            "AND status != 'expired'", (rx_id, user["id"])).fetchone():
                return row, "pharmacy_order"
            if row["status"] == "active" and conn.execute(
                    "SELECT 1 FROM rx_requests WHERE prescription_id = ? AND pharmacy_id = ? AND status != 'declined'",
                    (rx_id, user["id"])).fetchone():
                return row, "pharmacy"
    raise HTTPException(status_code=404, detail="Prescription not found")


def create_prescription(conn, appt_id, doctor_id: int, patient_id: int, data: PrescriptionIn, lat, lng, source,
                        parent=None) -> str:
    """Insert a prescription with its medicines and diagnoses (inside the caller's transaction)."""
    known = {r["id"] for r in conn.execute(
        f"SELECT id FROM conditions WHERE id IN ({','.join('?' * len(data.condition_ids))})", data.condition_ids)} \
        if data.condition_ids else set()
    unknown = [c for c in data.condition_ids if c not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown condition id {unknown[0]}")
    rx_id = str(uuid.uuid4())
    now = settings.now()
    # A visit booked for a family member makes the prescription theirs too (refills inherit it)
    if parent:
        member_id = parent["family_member_id"]
    else:
        appt = conn.execute("SELECT family_member_id FROM appointments WHERE id = ?", (appt_id,)).fetchone() if appt_id else None
        member_id = appt["family_member_id"] if appt else None
    conn.execute(
        "INSERT INTO prescriptions (id, reference, appointment_id, doctor_id, patient_id, patient_age, patient_sex, "
        "diagnosis, advice, lat, lng, location_source, radius_km, status, created_at, valid_until, parent_id, "
        "refill_number, refills_allowed, refill_until, family_member_id, follow_up_on) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?)",
        (rx_id, new_reference(conn, "prescriptions", "RX"), appt_id, doctor_id, patient_id, data.patient_age,
         data.patient_sex, data.diagnosis, data.advice, lat, lng, source, settings.NEARBY_RADIUS_KM, stamp(now),
         stamp(now + timedelta(days=settings.RX_VALID_DAYS)),
         parent["id"] if parent else None, parent["refills_used"] + 1 if parent else 0,
         0 if parent else data.refills_allowed,
         stamp(now + timedelta(days=settings.LONG_TERM_RX_DAYS)) if data.refills_allowed and not parent else None,
         member_id, None if parent else getattr(data, "follow_up_on", None)))
    for item in data.items:
        med = conn.execute("SELECT * FROM medicines WHERE id = ?", (item.medicine_id,)).fetchone() \
            if item.medicine_id else None
        if item.medicine_id and not med:
            raise HTTPException(status_code=400, detail=f"Unknown medicine id {item.medicine_id}")
        conn.execute(
            "INSERT INTO prescription_items (prescription_id, medicine_id, name, generic_name, strength, form, "
            "dosage, duration, quantity, instructions, allow_substitute, daily_units, duration_days, is_long_term) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rx_id, item.medicine_id, med["name"] if med else item.name,
             med["generic_name"] if med else item.generic_name, med["strength"] if med else item.strength,
             med["form"] if med else item.form, item.dosage, item.duration, item.quantity,
             item.instructions, int(item.allow_substitute), item.daily_units, item.duration_days, int(item.is_long_term)))
    conn.executemany("INSERT INTO prescription_conditions (prescription_id, condition_id) VALUES (?, ?)",
                     [(rx_id, c) for c in data.condition_ids])
    for t in data.tests:
        test = conn.execute("SELECT * FROM lab_tests WHERE id = ?", (t.test_id,)).fetchone() if t.test_id else None
        if t.test_id and not test:
            raise HTTPException(status_code=400, detail=f"Unknown test id {t.test_id}")
        conn.execute("INSERT INTO prescription_tests (prescription_id, test_id, name, instructions) VALUES (?, ?, ?, ?)",
                     (rx_id, t.test_id, test["name"] if test else t.name, t.instructions))
    return rx_id


def rx_conditions(conn, rx_id: str) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT c.id, c.code, c.name, c.care_area FROM prescription_conditions pc JOIN conditions c "
        "ON c.id = pc.condition_id WHERE pc.prescription_id = ? ORDER BY c.name", (rx_id,))]


def course_days(item) -> int:
    """How many days a prescribed quantity lasts (for refills); 30 when the doctor left it open."""
    if item["daily_units"]:
        return max(1, math.ceil(item["quantity"] / item["daily_units"]))
    return item["duration_days"] or 30


def refill_status(conn, row) -> dict:
    """Can the patient ask for a refill of this prescription (or the one it refills) now?"""
    root = row if not row["parent_id"] else conn.execute("SELECT * FROM prescriptions WHERE id = ?",
                                                         (row["parent_id"],)).fetchone()
    left = root["refills_allowed"] - root["refills_used"]
    info = {"root_id": root["id"], "allowed": root["refills_allowed"], "used": root["refills_used"], "left": max(left, 0),
            "until": root["refill_until"], "can_refill": False, "available_from": None, "reason": None}
    if not root["refills_allowed"]:
        return info
    if root["status"] != "active":
        info["reason"] = "The prescription was cancelled"
    elif left <= 0:
        info["reason"] = "All refills used. Book a follow-up with your doctor."
    elif not root["refill_until"] or root["refill_until"] <= stamp():
        info["reason"] = "Refills have expired. Book a follow-up with your doctor."
    else:
        long_term = conn.execute("SELECT * FROM prescription_items WHERE prescription_id = ? AND is_long_term = 1",
                                 (root["id"],)).fetchall()
        latest = conn.execute("SELECT MAX(created_at) FROM prescriptions WHERE id = ? OR parent_id = ?",
                              (root["id"], root["id"])).fetchone()[0]
        # Open a week before the last supply runs out, so nobody stocks up early
        days = max(course_days(i) for i in long_term) if long_term else 30
        available = datetime.fromisoformat(latest) + timedelta(days=max(days - 7, 0))
        info["available_from"] = stamp(available)
        if available > settings.now():
            info["reason"] = f"Your next refill opens on {available:%d %b %Y}"
        else:
            info["can_refill"] = True
    return info


# Imported last, so slices can depend on each other in any order
from features.medicine_orders.logic import ACTIVE_ORDER_STATUSES, ORDER_SELECT, order_summary  # noqa: E402
from features.prescriptions.api import PrescriptionIn  # noqa: E402
from features.labs import logic as labs  # noqa: E402

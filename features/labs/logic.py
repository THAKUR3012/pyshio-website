"""Labs: registration, test catalogue and prices, tests sent from prescriptions, bookings and reports.

This file: logic and queries.
"""
from __future__ import annotations

import re
from fastapi import BackgroundTasks, HTTPException

from shared import geo
from shared.common import paise, stamp
from shared.db import get_db, write_txn


TEST_CATEGORIES = ["Blood", "Urine", "Imaging", "Cardiac", "Other"]
ACTIVE_BOOKING = ("booked", "accepted", "sample_collected", "report_ready", "completed")
NEXT_STATUS = {"booked": {"accepted", "rejected"}, "accepted": {"sample_collected", "rejected"},
               "sample_collected": set(), "report_ready": {"completed"}}


# ---------- Labs ----------
LAB_SELECT = """SELECT l.*, u.name, u.email, u.phone, u.is_active FROM labs l JOIN users u ON u.id = l.user_id"""


def lab_public(row, distance_km: float = None) -> dict:
    view = {k: row[k] for k in ("slug", "lab_name", "nabl_no", "address", "city", "state", "pincode", "lat", "lng",
                                "opening_hours", "collection_fee_paise", "collection_radius_km", "flat_discount_percent",
                                "report_hours")}
    view.update(id=row["user_id"], phone=row["phone"], home_collection=bool(row["home_collection"]))
    if distance_km is not None:
        view["distance_km"] = distance_km
    return view


def lab_private(row) -> dict:
    return {**lab_public(row), "name": row["name"], "email": row["email"], "registration_no": row["registration_no"],
            "pathologist_name": row["pathologist_name"], "pathologist_reg_no": row["pathologist_reg_no"],
            "gstin": row["gstin"], "status": row["status"], "status_reason": row["status_reason"],
            "created_at": row["created_at"], "approved_at": row["approved_at"], "is_active": bool(row["is_active"])}


def get_lab(conn, lab_id: int):
    row = conn.execute(LAB_SELECT + " WHERE l.user_id = ?", (lab_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Lab not found")
    return row


def nearby_labs(conn, lat: float, lng: float, radius_km: float):
    min_lat, max_lat, min_lng, max_lng = geo.bounding_box(lat, lng, radius_km)
    rows = conn.execute(LAB_SELECT + " WHERE l.status = 'approved' AND u.is_active = 1 AND l.lat BETWEEN ? AND ? "
                        "AND l.lng BETWEEN ? AND ?", (min_lat, max_lat, min_lng, max_lng)).fetchall()
    return geo.within(rows, lat, lng, radius_km)


def make_slug(conn, name: str, city: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", f"{name} {city}".lower()).strip("-")[:60] or "lab"
    slug, n = base, 2
    while conn.execute("SELECT 1 FROM labs WHERE slug = ?", (slug,)).fetchone():
        slug, n = f"{base}-{n}", n + 1
    return slug


def admin_emails(conn) -> list:
    return [r["email"] for r in conn.execute("SELECT email FROM users WHERE role = 'admin' AND is_active = 1")]


# ---------- Test catalogue & price lists ----------
def test_view(row) -> dict:
    return {k: row[k] for k in ("id", "name", "code", "category", "sample_type", "mrp_paise", "unit", "ref_low", "ref_high")} \
        | {"fasting": bool(row["fasting"])}


def lab_trusted(conn, user) -> bool:
    if user["role"] == "admin":
        return True
    table = {"lab": "labs", "doctor": "doctors"}.get(user["role"])
    row = conn.execute(f"SELECT status FROM {table} WHERE user_id = ?", (user["id"],)).fetchone() if table else None
    return bool(row and row["status"] == "approved")


def find_or_create_test(conn, data: TestIn, user: dict) -> int:
    row = conn.execute("SELECT id FROM lab_tests WHERE lower(name) = lower(?)", (data.name,)).fetchone()
    if row:
        return row["id"]
    if not lab_trusted(conn, user):
        raise HTTPException(status_code=403, detail="Your account must be approved before adding tests to the catalogue")
    cur = conn.execute("INSERT INTO lab_tests (name, category, sample_type, fasting, mrp_paise, unit, ref_low, ref_high, "
                       "created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (data.name, data.category, data.sample_type, int(data.fasting), paise(data.mrp) if data.mrp else None,
                        data.unit, data.ref_low, data.ref_high, user["id"], stamp()))
    return cur.lastrowid


PRICE_SELECT = """SELECT p.*, t.name, t.code, t.category, t.sample_type, t.fasting FROM lab_prices p
                  JOIN lab_tests t ON t.id = p.test_id"""


def price_view(row) -> dict:
    return {"test_id": row["test_id"], "name": row["name"], "code": row["code"], "category": row["category"],
            "mrp_paise": row["mrp_paise"], "price_paise": row["price_paise"], "available": bool(row["available"]),
            "report_hours": row["report_hours"], "updated_at": row["updated_at"]}


# ---------- Tests on prescriptions ----------
def rx_tests(conn, rx_id: str) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT pt.id, pt.test_id, pt.name, pt.instructions, t.category, t.sample_type, t.fasting, t.mrp_paise "
        "FROM prescription_tests pt LEFT JOIN lab_tests t ON t.id = pt.test_id WHERE pt.prescription_id = ? ORDER BY pt.id",
        (rx_id,))]


def booked_test_ids(conn, rx_id: str, exclude_booking: str = None) -> set:
    rows = conn.execute(f"SELECT bi.prescription_test_id FROM lab_booking_items bi JOIN lab_bookings b ON b.id = bi.booking_id "
                        f"WHERE b.prescription_id = ? AND b.status IN ({','.join('?' * len(ACTIVE_BOOKING))}) AND b.id != ?",
                        (rx_id, *ACTIVE_BOOKING, exclude_booking or ""))
    return {r[0] for r in rows}


def booking_summaries(conn, rx_id: str) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT b.id, b.reference, b.status, b.total_paise, b.collection, l.lab_name, "
        "(SELECT id FROM health_records h WHERE h.lab_booking_id = b.id AND h.deleted_at IS NULL LIMIT 1) AS record_id "
        "FROM lab_bookings b JOIN labs l ON l.user_id = b.lab_id WHERE b.prescription_id = ? AND b.status != 'cancelled' "
        "ORDER BY b.created_at", (rx_id,))]


def distribute_tests(conn, rx_id: str, lat, lng, radius_km: float) -> list:
    """Send the prescription's tests to every nearby lab that hasn't had them yet. Returns [(distance, lab)]."""
    if lat is None:
        return []
    added = []
    for distance, lab in nearby_labs(conn, lat, lng, radius_km):
        cur = conn.execute("INSERT OR IGNORE INTO lab_requests (prescription_id, lab_id, distance_km, status, created_at, "
                           "updated_at) VALUES (?, ?, ?, 'open', ?, ?)", (rx_id, lab["user_id"], distance, stamp(), stamp()))
        if cur.rowcount:
            added.append((distance, dict(lab)))
    return added


def email_lab_requests(background: BackgroundTasks, conn, rx_id: str, added: list) -> None:
    if not added:
        return
    rx = conn.execute("SELECT reference FROM prescriptions WHERE id = ?", (rx_id,)).fetchone()
    tests = rx_tests(conn, rx_id)
    for distance, lab in added:
        background.add_task(labs_emails.send_lab_request, lab, dict(rx), tests, distance)


# ---------- Comparing labs ----------
def estimate(conn, lab, tests: list) -> dict:
    """Price each test from the lab's price list, or its flat % off the catalogue MRP."""
    prices = {r["test_id"]: r for r in conn.execute("SELECT * FROM lab_prices WHERE lab_id = ?", (lab["user_id"],))}
    lines = []
    for t in tests:
        p = prices.get(t["test_id"]) if t["test_id"] else None
        if p and p["available"]:
            lines.append({"prescription_test_id": t["id"], "name": t["name"], "available": True, "price_paise": p["price_paise"],
                          "mrp_paise": p["mrp_paise"], "report_hours": p["report_hours"] or lab["report_hours"]})
        elif not p and lab["flat_discount_percent"] is not None and t.get("mrp_paise"):
            price = int(round(t["mrp_paise"] * (1 - lab["flat_discount_percent"] / 100)))
            lines.append({"prescription_test_id": t["id"], "name": t["name"], "available": True, "price_paise": price,
                          "mrp_paise": t["mrp_paise"], "report_hours": lab["report_hours"]})
        else:
            lines.append({"prescription_test_id": t["id"], "name": t["name"], "available": False, "price_paise": 0,
                          "mrp_paise": None, "report_hours": None})
    return {"lines": lines, "report_hours": max([l["report_hours"] for l in lines if l["available"]] or [None]) if any(
        l["available"] for l in lines) else None, "source": "estimate"}


def quote_offer(conn, quote, tests: list) -> dict:
    items = {r["prescription_test_id"]: r for r in conn.execute("SELECT * FROM lab_quote_items WHERE quote_id = ?", (quote["id"],))}
    lines = []
    for t in tests:
        q = items.get(t["id"])
        lines.append({"prescription_test_id": t["id"], "name": t["name"], "available": bool(q and q["available"]),
                      "price_paise": q["price_paise"] if q and q["available"] else 0,
                      "mrp_paise": q["mrp_paise"] if q else None, "report_hours": quote["report_hours"]})
    return {"lines": lines, "report_hours": quote["report_hours"], "source": "quote", "quote_id": quote["id"],
            "note": quote["note"], "quote_home": bool(quote["home_collection"]),
            "quote_fee": quote["collection_fee_paise"]}


def summarise(lines: list) -> dict:
    available = [l for l in lines if l["available"]]
    subtotal = sum(l["price_paise"] for l in available)
    mrp = sum(l["mrp_paise"] or l["price_paise"] for l in available)
    return {"available": len(available), "tests": len(lines), "complete": len(available) == len(lines),
            "subtotal_paise": subtotal, "mrp_total_paise": mrp, "discount_paise": max(mrp - subtotal, 0),
            "discount_percent": round(100 * (mrp - subtotal) / mrp, 1) if mrp and mrp > subtotal else 0}


def lab_options(conn, rx_row, tests: list, radius_km: float = None, sort: str = "cheapest") -> list:
    if rx_row["lat"] is None or not tests:
        return []
    radius = radius_km or rx_row["radius_km"]
    quotes = {q["lab_id"]: q for q in conn.execute("SELECT * FROM lab_quotes WHERE prescription_id = ? AND status = 'active' "
                                                   "AND valid_until > ?", (rx_row["id"], stamp()))}
    options = []
    for distance, lab in nearby_labs(conn, rx_row["lat"], rx_row["lng"], radius):
        offer = quote_offer(conn, quotes[lab["user_id"]], tests) if lab["user_id"] in quotes else estimate(conn, lab, tests)
        summary = summarise(offer["lines"])
        if not summary["available"]:
            continue
        home = offer.get("quote_home", bool(lab["home_collection"])) and distance <= lab["collection_radius_km"]
        fee = offer.get("quote_fee", lab["collection_fee_paise"]) if home else 0
        options.append({"lab_id": lab["user_id"], "lab_name": lab["lab_name"], "nabl_no": lab["nabl_no"],
                        "address": lab["address"], "city": lab["city"], "phone": lab["phone"], "distance_km": distance,
                        "home_collection": home, "collection_fee_paise": fee, "report_hours": offer["report_hours"],
                        "source": offer["source"], "quote_id": offer.get("quote_id"), "note": offer.get("note"),
                        "lines": offer["lines"], **summary})
    return sort_options(options, sort)


SORTS = ("cheapest", "fastest", "nearest")


def sort_options(options: list, sort: str = "cheapest") -> list:
    keys = {"cheapest": lambda o: (-o["available"], o["subtotal_paise"], o["distance_km"]),
            "fastest": lambda o: (-o["available"], o["report_hours"] or 9999, o["subtotal_paise"]),
            "nearest": lambda o: (o["distance_km"], -o["available"], o["subtotal_paise"])}
    return sorted(options, key=keys[sort])


def highlights(options: list) -> dict:
    if not options:
        return {"cheapest": None, "fastest": None, "home_collection": None}
    most = max(o["available"] for o in options)
    full = [o for o in options if o["available"] == most]

    def pick(o):
        return None if not o else {k: o[k] for k in ("lab_id", "lab_name", "subtotal_paise", "report_hours", "distance_km",
                                                     "collection_fee_paise", "available", "tests", "discount_percent")}
    home = [o for o in full if o["home_collection"]]
    return {"cheapest": pick(min(full, key=lambda o: (o["subtotal_paise"], o["distance_km"]))),
            "fastest": pick(min(full, key=lambda o: (o["report_hours"] or 9999, o["subtotal_paise"]))),
            "home_collection": pick(min(home, key=lambda o: (o["subtotal_paise"] + o["collection_fee_paise"]))) if home else None}


def options_payload(conn, row, sort: str, radius: float = None) -> dict:
    tests = rx_tests(conn, row["id"])
    remaining = [t for t in tests if t["id"] not in booked_test_ids(conn, row["id"])]
    opts = lab_options(conn, row, remaining, radius, sort)
    shared = bool(conn.execute("SELECT 1 FROM lab_requests WHERE prescription_id = ?", (row["id"],)).fetchone())
    home_differs = row["home_lat"] is not None and (row["lat"], row["lng"]) != (row["home_lat"], row["home_lng"])
    return {"options": opts, "highlights": highlights(opts), "remaining_test_ids": [t["id"] for t in remaining],
            "radius_km": radius or row["radius_km"], "shared": shared, "has_location": row["lat"] is not None,
            "home_differs": home_differs, "sorts": list(SORTS)}


def request_for(conn, req_id: int, lab_id: int):
    row = conn.execute("SELECT * FROM lab_requests WHERE id = ? AND lab_id = ?", (req_id, lab_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")
    return row


# ---------- Bookings ----------
BOOKING_SELECT = """
    SELECT b.*, l.lab_name, l.address AS lab_address, l.city AS lab_city, l.nabl_no, l.pathologist_name,
           lu.email AS lab_email, lu.phone AS lab_phone, pu.name AS patient_name, pu.email AS patient_email,
           pu.phone AS patient_phone, r.reference AS rx_reference, r.doctor_id, r.patient_age, r.patient_sex,
           du.name AS doctor_name, du.email AS doctor_email
    FROM lab_bookings b JOIN labs l ON l.user_id = b.lab_id JOIN users lu ON lu.id = b.lab_id
    JOIN users pu ON pu.id = b.patient_id JOIN prescriptions r ON r.id = b.prescription_id
    JOIN users du ON du.id = r.doctor_id
"""


def booking_items(conn, booking_id: str) -> list:
    return [dict(r) for r in conn.execute("SELECT * FROM lab_booking_items WHERE booking_id = ? ORDER BY id", (booking_id,))]


def booking_view(conn, row, role: str) -> dict:
    view = {k: row[k] for k in ("id", "reference", "prescription_id", "rx_reference", "lab_id", "lab_name", "lab_address",
                                "lab_city", "lab_phone", "nabl_no", "collection", "preferred_date", "preferred_time",
                                "distance_km", "subtotal_paise", "collection_fee_paise", "total_paise", "payment_method",
                                "payment_status", "status", "status_reason", "created_at", "updated_at", "doctor_name")}
    view["items"] = booking_items(conn, row["id"])
    rec = conn.execute("SELECT id FROM health_records WHERE lab_booking_id = ? AND deleted_at IS NULL", (row["id"],)).fetchone()
    view["record_id"] = rec["id"] if rec else None
    if role in ("lab", "admin"):
        view.update(patient_name=row["patient_name"], patient_phone=row["patient_phone"], patient_age=row["patient_age"],
                    patient_sex=row["patient_sex"], address=row["address"])
    if role in ("patient", "admin"):
        view["address"] = row["address"]
    return view


def full_booking(conn, booking_id: str) -> dict:
    row = conn.execute(BOOKING_SELECT + " WHERE b.id = ?", (booking_id,)).fetchone()
    return {**dict(row), "items": booking_items(conn, booking_id)}


def booking_for(conn, booking_id: str, user: dict):
    row = conn.execute(BOOKING_SELECT + " WHERE b.id = ?", (booking_id,)).fetchone()
    if row and (user["role"] == "admin" or (user["role"] == "patient" and row["patient_id"] == user["id"])
                or (user["role"] == "lab" and row["lab_id"] == user["id"])):
        return row
    raise HTTPException(status_code=404, detail="Booking not found")


def notify(background: BackgroundTasks, conn, booking_id: str, recipients: list, note: str = None) -> None:
    data = full_booking(conn, booking_id)
    for who in recipients:
        background.add_task(labs_emails.send_lab_booking, data, who, note)


def reopen_lab_requests(conn, rx_id: str) -> None:
    """Tests are free again: labs that were told to stop can quote again."""
    conn.execute("UPDATE lab_requests SET status = 'open', outcome = NULL, updated_at = ? WHERE prescription_id = ? AND status = 'closed'",
                 (stamp(), rx_id))


def wind_down_lab(lab_id: int, reason: str, background: BackgroundTasks) -> int:
    """Cancel bookings that haven't reached the lab yet and tell the patients."""
    with get_db() as conn:
        with write_txn(conn):
            rows = conn.execute("SELECT id, prescription_id FROM lab_bookings WHERE lab_id = ? AND status IN ('booked', 'accepted')",
                                (lab_id,)).fetchall()
            for r in rows:
                conn.execute("UPDATE lab_bookings SET status = 'rejected', status_reason = ?, updated_at = ? WHERE id = ?",
                             (reason, stamp(), r["id"]))
                reopen_lab_requests(conn, r["prescription_id"])
            conn.execute("UPDATE lab_requests SET status = 'closed', updated_at = ? WHERE lab_id = ? AND status IN ('open', 'quoted')",
                         (stamp(), lab_id))
        for r in rows:
            notify(background, conn, r["id"], ["patient"], "Please book the tests at another lab nearby.")
    return len(rows)


def wind_down_patient_bookings(patient_id: int, reason: str, background: BackgroundTasks) -> None:
    with get_db() as conn:
        rows = conn.execute("SELECT id FROM lab_bookings WHERE patient_id = ? AND status IN ('booked', 'accepted')",
                            (patient_id,)).fetchall()
        conn.execute("UPDATE lab_bookings SET status = 'cancelled', status_reason = ?, updated_at = ? WHERE patient_id = ? "
                     "AND status IN ('booked', 'accepted')", (reason, stamp(), patient_id))
        for r in rows:
            notify(background, conn, r["id"], ["lab"])


# Imported last, so slices can depend on each other in any order
from features.labs.api import TestIn  # noqa: E402
from features.labs import emails as labs_emails  # noqa: E402

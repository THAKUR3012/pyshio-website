"""Prescription uploads: a patient uploads any prescription, nearby pharmacies and labs quote.

This file: logic and queries.
"""
from __future__ import annotations

from fastapi import BackgroundTasks, HTTPException

from shared.common import stamp
from shared.db import get_db


ACTIVE_ORDER = ("placed", "accepted", "ready", "sample_collected", "report_ready", "completed")
NEXT = {
    "pharmacy": {"placed": {"accepted", "rejected"}, "accepted": {"ready", "rejected"}, "ready": {"completed"}},
    "lab": {"placed": {"accepted", "rejected"}, "accepted": {"sample_collected", "rejected"}, "report_ready": {"completed"}},
}
KIND = {"pharmacy": "medicines", "lab": "tests"}


# ---------- Helpers ----------
def upload_for(conn, upload_id: str, user: dict):
    row = conn.execute("SELECT * FROM rx_uploads WHERE id = ? AND patient_id = ?", (upload_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Upload not found")
    return row


def is_live(row) -> bool:
    return row["status"] == "open" and row["valid_until"] > stamp()


def partner_rows(conn, role: str, lat: float, lng: float, radius: float):
    return pharmacies_logic.nearby_pharmacies(conn, lat, lng, radius) if role == "pharmacy" else labs.nearby_labs(conn, lat, lng, radius)


def partner_name(row, role: str) -> str:
    return row["store_name"] if role == "pharmacy" else row["lab_name"]


def distribute(conn, upload) -> list:
    """Send the upload to every nearby pharmacy / lab that hasn't had it. Returns [(role, distance, partner row)]."""
    added = []
    for role, wanted in (("pharmacy", upload["want_medicines"]), ("lab", upload["want_tests"])):
        if not wanted:
            continue
        for distance, row in partner_rows(conn, role, upload["lat"], upload["lng"], upload["radius_km"]):
            cur = conn.execute("INSERT OR IGNORE INTO upload_requests (upload_id, partner_role, partner_id, distance_km, status, "
                               "created_at, updated_at) VALUES (?, ?, ?, ?, 'open', ?, ?)",
                               (upload["id"], role, row["user_id"], distance, stamp(), stamp()))
            if cur.rowcount:
                added.append((role, distance, dict(row)))
    return added


def email_requests(background: BackgroundTasks, upload, added: list) -> None:
    for role, distance, partner in added:
        background.add_task(prescription_uploads_emails.send_upload_request, partner["email"], partner_name(partner, role), upload["reference"],
                            KIND[role], distance, upload["note"])


QUOTE_SELECT = """SELECT q.*, r.distance_km, COALESCE(ph.store_name, l.lab_name) AS partner_name,
                         COALESCE(ph.address, l.address) AS partner_address, u.phone AS partner_phone, l.nabl_no
                  FROM upload_quotes q JOIN upload_requests r ON r.upload_id = q.upload_id AND r.partner_id = q.partner_id
                  JOIN users u ON u.id = q.partner_id
                  LEFT JOIN pharmacies ph ON ph.user_id = q.partner_id AND q.partner_role = 'pharmacy'
                  LEFT JOIN labs l ON l.user_id = q.partner_id AND q.partner_role = 'lab'"""


def quote_view(conn, q) -> dict:
    lines = [{"name": l["name"], "quantity": l["quantity"], "available": bool(l["available"]),
              "unit_price_paise": l["unit_price_paise"], "line_paise": l["unit_price_paise"] * l["quantity"] if l["available"] else 0,
              "mrp_paise": l["mrp_paise"]}
             for l in conn.execute("SELECT * FROM upload_quote_lines WHERE quote_id = ? ORDER BY id", (q["id"],))]
    mrp, sub = q["mrp_total_paise"], q["subtotal_paise"]
    speed = q["delivery_minutes"] if q["partner_role"] == "pharmacy" and q["delivery_available"] and q["delivery_minutes"] else q["ready_minutes"]
    return {"id": q["id"], "partner_role": q["partner_role"], "partner_id": q["partner_id"], "partner_name": q["partner_name"],
            "partner_address": q["partner_address"], "partner_phone": q["partner_phone"], "nabl": bool(q["nabl_no"]),
            "distance_km": q["distance_km"], "subtotal_paise": sub, "mrp_total_paise": mrp,
            "discount_percent": round(100 * (mrp - sub) / mrp, 1) if mrp > sub else 0,
            "lines_total": q["lines_total"], "lines_available": q["lines_available"],
            "complete": q["lines_available"] == q["lines_total"],
            "delivery_available": bool(q["delivery_available"]), "delivery_fee_paise": q["delivery_fee_paise"],
            "ready_minutes": q["ready_minutes"], "delivery_minutes": q["delivery_minutes"], "speed_minutes": speed,
            "note": q["note"], "status": q["status"], "valid_until": q["valid_until"], "lines": lines}


SORTS = {
    "cheapest": lambda o: (-o["lines_available"], o["subtotal_paise"], o["distance_km"]),
    "discount": lambda o: (-o["discount_percent"], o["subtotal_paise"]),
    "fastest": lambda o: (o["speed_minutes"] if o["speed_minutes"] is not None else 99999, o["subtotal_paise"]),
    "nearest": lambda o: (o["distance_km"], o["subtotal_paise"]),
}


def highlights(options: list) -> dict:
    if not options:
        return {"cheapest": None, "best_discount": None, "fastest": None}
    most = max(o["lines_available"] for o in options)
    full = [o for o in options if o["lines_available"] == most]
    pick = lambda o: {k: o[k] for k in ("id", "partner_name", "subtotal_paise", "discount_percent", "speed_minutes", "distance_km")} if o else None
    with_discount = [o for o in full if o["discount_percent"]]
    timed = [o for o in full if o["speed_minutes"] is not None]
    return {"cheapest": pick(min(full, key=SORTS["cheapest"])),
            "best_discount": pick(max(with_discount, key=lambda o: o["discount_percent"])) if with_discount else None,
            "fastest": pick(min(timed, key=SORTS["fastest"])) if timed else None}


ORDER_SELECT = """SELECT o.*, x.reference AS upload_reference, x.record_id, pu.name AS patient_name, pu.phone AS patient_phone,
                         pu.email AS patient_email, pa.email AS partner_email, pa.phone AS partner_phone,
                         COALESCE(ph.store_name, l.lab_name) AS partner_name, COALESCE(ph.address, l.address) AS partner_address
                  FROM upload_orders o JOIN rx_uploads x ON x.id = o.upload_id JOIN users pu ON pu.id = o.patient_id
                  JOIN users pa ON pa.id = o.partner_id
                  LEFT JOIN pharmacies ph ON ph.user_id = o.partner_id AND o.partner_role = 'pharmacy'
                  LEFT JOIN labs l ON l.user_id = o.partner_id AND o.partner_role = 'lab'"""


def order_view(conn, row, viewer: str) -> dict:
    view = {k: row[k] for k in ("id", "reference", "upload_id", "upload_reference", "quote_id", "partner_role", "partner_id",
                                "partner_name", "partner_address", "partner_phone", "fulfilment", "preferred_date",
                                "preferred_time", "subtotal_paise", "fee_paise", "total_paise", "payment_status", "status",
                                "status_reason", "created_at", "updated_at")}
    view["lines"] = [dict(l) for l in conn.execute("SELECT name, quantity, unit_price_paise FROM upload_quote_lines WHERE quote_id = ? "
                                                   "AND available = 1 ORDER BY id", (row["quote_id"],))]
    rec = conn.execute("SELECT id FROM health_records WHERE upload_order_id = ? AND deleted_at IS NULL", (row["id"],)).fetchone()
    view["report_record_id"] = rec["id"] if rec else None
    view["prescription_file"] = f"/api/records/{row['record_id']}/file"
    if viewer == "patient":
        view["handover_code"] = row["handover_code"]
        view["address"] = row["address"]
    else:
        view.update(patient_name=row["patient_name"], patient_phone=row["patient_phone"], address=row["address"])
    return view


def notify(background: BackgroundTasks, conn, order_id: str, recipient: str, note: str = None) -> None:
    row = conn.execute(ORDER_SELECT + " WHERE o.id = ?", (order_id,)).fetchone()
    data = {**dict(row), "lines": [dict(l) for l in conn.execute("SELECT name, quantity FROM upload_quote_lines WHERE quote_id = ? "
                                                                 "AND available = 1", (row["quote_id"],))]}
    background.add_task(prescription_uploads_emails.send_upload_order, data, recipient, note)


def detail(conn, upload, sort: str) -> dict:
    quotes = [quote_view(conn, q) for q in conn.execute(
        QUOTE_SELECT + " WHERE q.upload_id = ? AND q.status IN ('active', 'accepted') ORDER BY q.created_at", (upload["id"],))]
    live = [q for q in quotes if q["status"] == "active" and q["valid_until"] > stamp()]
    out = {k: upload[k] for k in ("id", "reference", "note", "status", "created_at", "valid_until", "radius_km")}
    out.update(want_medicines=bool(upload["want_medicines"]), want_tests=bool(upload["want_tests"]), live=is_live(upload),
               file_url=f"/api/records/{upload['record_id']}/file", sort=sort)
    sent = {r["partner_role"]: r["n"] for r in conn.execute("SELECT partner_role, COUNT(*) AS n FROM upload_requests WHERE upload_id = ? "
                                                            "GROUP BY partner_role", (upload["id"],))}
    for role, kind in KIND.items():
        opts = sorted([q for q in live if q["partner_role"] == role], key=SORTS[sort])
        out[kind] = {"wanted": bool(upload[f"want_{kind}"]), "sent_to": sent.get(role, 0), "options": opts,
                     "highlights": highlights(opts)}
    out["orders"] = [order_view(conn, o, "patient") for o in conn.execute(
        ORDER_SELECT + " WHERE o.upload_id = ? ORDER BY o.created_at DESC", (upload["id"],))]
    return out


def reopen(conn, order) -> None:
    """A cancelled / rejected booking frees the prescription for other partners of that kind."""
    conn.execute("UPDATE upload_requests SET status = 'open', outcome = NULL, updated_at = ? WHERE upload_id = ? AND partner_role = ? "
                 "AND status = 'closed'", (stamp(), order["upload_id"], order["partner_role"]))


# ---------- Pharmacy / lab ----------
def approved_partner(conn, user: dict):
    table = "pharmacies" if user["role"] == "pharmacy" else "labs"
    row = conn.execute(f"SELECT status FROM {table} WHERE user_id = ?", (user["id"],)).fetchone()
    if not row or row["status"] != "approved":
        raise HTTPException(status_code=403, detail="Your account must be approved first")


def partner_order(conn, order_id: str, user: dict):
    row = conn.execute(ORDER_SELECT + " WHERE o.id = ? AND o.partner_id = ?", (order_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    return row


def wind_down_patient(patient_id: int, background: BackgroundTasks) -> None:
    """Account closure: uploads close and open orders are cancelled."""
    with get_db() as conn:
        conn.execute("UPDATE rx_uploads SET status = 'cancelled' WHERE patient_id = ? AND status = 'open'", (patient_id,))
        rows = conn.execute("SELECT id FROM upload_orders WHERE patient_id = ? AND status IN ('placed', 'accepted')", (patient_id,)).fetchall()
        conn.execute("UPDATE upload_orders SET status = 'cancelled', status_reason = 'The patient closed their account', updated_at = ? "
                     "WHERE patient_id = ? AND status IN ('placed', 'accepted')", (stamp(), patient_id))
        for r in rows:
            notify(background, conn, r["id"], "partner")


# Imported last, so slices can depend on each other in any order
from features.labs import logic as labs  # noqa: E402
from features.pharmacies import logic as pharmacies_logic  # noqa: E402
from features.prescription_uploads import emails as prescription_uploads_emails  # noqa: E402

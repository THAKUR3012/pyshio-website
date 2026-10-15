"""Medicine orders: the patient orders from a pharmacy, pays, and the pharmacy fulfils it.

This file: logic and queries.
"""
from __future__ import annotations

import logging
from fastapi import BackgroundTasks, HTTPException

from shared import mailer, payments, settings
from shared.common import hold_cutoff, stamp
from shared.db import get_db, write_txn


log = logging.getLogger("pyshio.medicine_orders")


ACTIVE_ORDER_STATUSES = ("pending_payment", "placed", "accepted", "ready", "out_for_delivery", "completed")
OPEN_ORDER_STATUSES = ("pending_payment", "placed", "accepted", "ready", "out_for_delivery")


# ---------- Medicine orders ----------
ORDER_SELECT = """
    SELECT o.*, p.store_name, p.address AS pharmacy_address, p.city AS pharmacy_city, p.lat AS pharmacy_lat,
           p.lng AS pharmacy_lng, pu_ph.phone AS pharmacy_phone, pu_ph.email AS pharmacy_email,
           pu.name AS patient_name, pu.email AS patient_email, pu.phone AS patient_phone, r.reference AS rx_reference,
           p.gstin, p.drug_licence_no, p.pharmacist_name, pu_ph.name AS pharmacy_contact,
           (SELECT rating FROM reviews rv WHERE rv.order_id = o.id) AS my_rating
    FROM medicine_orders o
    JOIN pharmacies p ON p.user_id = o.pharmacy_id
    JOIN users pu_ph ON pu_ph.id = o.pharmacy_id
    JOIN users pu ON pu.id = o.patient_id
    JOIN prescriptions r ON r.id = o.prescription_id
"""


def order_summary(row) -> dict:
    return {k: row[k] for k in ("id", "reference", "status", "total_paise", "fulfilment", "payment_method",
                                "payment_status", "created_at", "store_name")}


def order_items(conn, order_id: str) -> list:
    return [dict(r) for r in conn.execute("SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order_id,))]


def order_view(conn, row, role: str) -> dict:
    view = {k: row[k] for k in ("id", "reference", "prescription_id", "rx_reference", "status", "status_reason",
                                "source", "subtotal_paise", "delivery_fee_paise", "total_paise", "fulfilment",
                                "delivery_address", "distance_km", "payment_method", "payment_status", "created_at",
                                "updated_at", "store_name", "pharmacy_address", "pharmacy_city", "pharmacy_phone",
                                "pharmacy_lat", "pharmacy_lng", "pharmacy_id")}
    view["items"] = [{k: i[k] for k in ("prescription_item_id", "name", "quantity", "unit_price_paise", "line_paise")}
                     for i in order_items(conn, row["id"])]
    if role == "patient":
        view.update(delivery_otp=row["delivery_otp"] if row["status"] in ("ready", "out_for_delivery") else None,
                    my_rating=row["my_rating"])
    if role in ("pharmacy", "admin"):
        view.update(patient_name=row["patient_name"], patient_phone=row["patient_phone"],
                    patient_email=row["patient_email"], needs_otp=bool(row["delivery_otp"]))
    if role == "admin":
        view.update(razorpay_order_id=row["razorpay_order_id"], razorpay_payment_id=row["razorpay_payment_id"])
    return view


def email_view(conn, order_id: str) -> dict:
    row = conn.execute(ORDER_SELECT + " WHERE o.id = ?", (order_id,)).fetchone()
    return {**dict(row), "items": order_items(conn, order_id)}


def order_for(conn, order_id: str, user: dict):
    row = conn.execute(ORDER_SELECT + " WHERE o.id = ?", (order_id,)).fetchone()
    if row and (user["role"] == "admin" or (user["role"] == "patient" and row["patient_id"] == user["id"])
                or (user["role"] == "pharmacy" and row["pharmacy_id"] == user["id"])):
        return row
    raise HTTPException(status_code=404, detail="Order not found")


def expire_unpaid_orders(conn) -> None:
    conn.execute("UPDATE medicine_orders SET status = 'expired', updated_at = ? WHERE status = 'pending_payment' "
                 "AND created_at <= ?", (stamp(), hold_cutoff()))


def after_order_placed(conn, order) -> list:
    """Mark the quote used, open the patient-pharmacy chat, and stop asking pharmacies once every medicine is ordered.
    Returns the pharmacies that had quoted and lost, as [(email, store name, prescription reference)], to tell them."""
    if order["quote_id"]:
        conn.execute("UPDATE quotes SET status = 'accepted' WHERE id = ?", (order["quote_id"],))
    rx_id = order["prescription_id"]
    rx = conn.execute("SELECT reference FROM prescriptions WHERE id = ?", (rx_id,)).fetchone()
    store = conn.execute("SELECT store_name FROM pharmacies WHERE user_id = ?", (order["pharmacy_id"],)).fetchone()["store_name"]
    chats.open_conversation(conn, order["patient_id"], "pharmacy", order["pharmacy_id"], "medicine_order", order["id"],
                            f"{order['reference']} · {store}",
                            f"Order {order['reference']} placed with {store}. Chat here about availability, substitutes or delivery.")
    conn.execute("UPDATE rx_requests SET outcome = 'chosen' WHERE prescription_id = ? AND pharmacy_id = ?", (rx_id, order["pharmacy_id"]))
    losers = []
    remaining = {i["id"] for i in rx_items(conn, rx_id)} - ordered_item_ids(conn, rx_id)
    if not remaining:
        losers = [(r["email"], r["store_name"], rx["reference"]) for r in conn.execute(
            "SELECT u.email, p.store_name FROM rx_requests q JOIN users u ON u.id = q.pharmacy_id JOIN pharmacies p ON p.user_id = q.pharmacy_id "
            "WHERE q.prescription_id = ? AND q.pharmacy_id != ? AND q.status = 'quoted'", (rx_id, order["pharmacy_id"]))]
        conn.execute("UPDATE rx_requests SET outcome = CASE WHEN pharmacy_id = ? THEN 'chosen' ELSE 'chosen_other' END "
                     "WHERE prescription_id = ? AND status IN ('open', 'quoted')", (order["pharmacy_id"], rx_id))
        conn.execute("UPDATE rx_requests SET status = 'closed', updated_at = ? WHERE prescription_id = ? "
                     "AND status IN ('open', 'quoted')", (stamp(), rx_id))
        conn.execute("UPDATE quotes SET status = 'lost' WHERE prescription_id = ? AND status = 'active'", (rx_id,))
    return losers


def tell_losers(background: BackgroundTasks, losers: list, kind: str = "medicines") -> None:
    for email, name, reference in losers:
        background.add_task(prescription_uploads_emails.send_patient_chose_other, email, name, reference, kind)


def reopen_requests(conn, rx_id: str) -> None:
    """After a cancelled/rejected order: let pharmacies quote again and revive still-valid quotes."""
    rx = conn.execute("SELECT * FROM prescriptions WHERE id = ?", (rx_id,)).fetchone()
    if not rx or not rx_is_live(rx):
        return
    remaining = {i["id"] for i in rx_items(conn, rx_id)} - ordered_item_ids(conn, rx_id)
    if not remaining:
        return
    conn.execute(
        f"UPDATE quotes SET status = 'active' WHERE prescription_id = ? AND status IN ('lost', 'accepted') "
        f"AND valid_until > ? AND id NOT IN (SELECT o.quote_id FROM medicine_orders o WHERE o.quote_id IS NOT NULL "
        f"AND o.status IN ({','.join('?' * len(ACTIVE_ORDER_STATUSES))}) AND {LIVE_ORDER})",
        (rx_id, stamp(), *ACTIVE_ORDER_STATUSES, hold_cutoff()))
    conn.execute("UPDATE rx_requests SET status = CASE WHEN EXISTS (SELECT 1 FROM quotes q WHERE "
                 "q.prescription_id = rx_requests.prescription_id AND q.pharmacy_id = rx_requests.pharmacy_id "
                 "AND q.status = 'active') THEN 'quoted' ELSE 'open' END, outcome = NULL, updated_at = ? "
                 "WHERE prescription_id = ? AND status = 'closed'", (stamp(), rx_id))


def checkout(row, user: dict) -> dict:
    return {"key_id": settings.RAZORPAY_KEY_ID, "order_id": row["razorpay_order_id"], "amount": row["total_paise"],
            "currency": "INR", "name": settings.PLATFORM_NAME, "description": f"Medicines from {row['store_name']}",
            "prefill": {"name": user["name"], "email": user["email"], "contact": user["phone"] or ""},
            "hold_minutes": settings.PAYMENT_HOLD_MINUTES}


def notify_order(background: BackgroundTasks, conn, order_id: str, recipients: list, note: str = None) -> None:
    view = email_view(conn, order_id)
    for r in recipients:
        background.add_task(medicine_orders_emails.send_order_update, view, r, note)


def refund_order(order_id: str) -> str:
    with get_db() as conn:
        # Claim the refund first so two callers (e.g. patient cancel + admin retry) can't both refund
        claimed = conn.execute("UPDATE medicine_orders SET refund_id = 'pending', updated_at = ? WHERE id = ? "
                               "AND refund_id IS NULL AND razorpay_payment_id IS NOT NULL "
                               "AND payment_status IN ('paid', 'refund_failed')", (stamp(), order_id)).rowcount
        row = conn.execute("SELECT * FROM medicine_orders WHERE id = ?", (order_id,)).fetchone()
        if not claimed:
            return "A refund for this order is already being processed." if row["payment_status"] != "refunded" \
                else f"Your refund of {mailer.money(row['total_paise'])} has already been issued."
        try:
            result = payments.refund_paise(row["razorpay_payment_id"], row["total_paise"])
        except payments.PaymentError:
            conn.execute("UPDATE medicine_orders SET payment_status = 'refund_failed', refund_id = NULL, updated_at = ? "
                         "WHERE id = ?", (stamp(), order_id))
            return f"Your refund of {mailer.money(row['total_paise'])} could not be processed automatically; our team will refund it."
        conn.execute("UPDATE medicine_orders SET payment_status = 'refunded', refund_id = ?, updated_at = ? WHERE id = ?",
                     (result.get("id"), stamp(), order_id))
    return f"A full refund of {mailer.money(row['total_paise'])} has been issued (5–7 working days)."


def mark_order_paid(order_id: str, payment_id: str, background: BackgroundTasks) -> str:
    with get_db() as conn:
        with write_txn(conn):
            row = conn.execute("SELECT * FROM medicine_orders WHERE id = ?", (order_id,)).fetchone()
            if row["payment_status"] in ("paid", "refunded", "refund_failed"):
                return row["status"]
            conn.execute("UPDATE medicine_orders SET payment_status = 'paid', razorpay_payment_id = ?, updated_at = ? "
                         "WHERE id = ?", (payment_id, stamp(), order_id))
            placed, losers = False, []  # cancelled meanwhile, or can't be revived -> refund below
            if row["status"] in ("pending_payment", "expired"):
                rx = conn.execute("SELECT * FROM prescriptions WHERE id = ?", (row["prescription_id"],)).fetchone()
                pharmacy = get_pharmacy(conn, row["pharmacy_id"])
                mine = {r[0] for r in conn.execute("SELECT prescription_item_id FROM order_items WHERE order_id = ?",
                                                   (order_id,))}
                taken = mine & ordered_item_ids(conn, row["prescription_id"], exclude_order_id=order_id)
                if rx_is_live(rx) and pharmacy["status"] == "approved" and pharmacy["is_active"] and not taken:
                    conn.execute("UPDATE medicine_orders SET status = 'placed' WHERE id = ?", (order_id,))
                    losers = after_order_placed(conn, row)
                    placed = True
                else:
                    conn.execute("UPDATE medicine_orders SET status = 'cancelled', status_reason = ? WHERE id = ?",
                                 ("Payment arrived after the order could no longer be fulfilled", order_id))
        if placed:
            notify_order(background, conn, order_id, ["pharmacy", "patient"])
            tell_losers(background, losers)
            return "placed"
    note = refund_order(order_id)
    with get_db() as conn:
        notify_order(background, conn, order_id, ["patient"], note)
    return "refunded"


def handle_razorpay_payment(razorpay_order_id: str, payment_id: str, background: BackgroundTasks,
                            amount: int = None, currency: str = "INR") -> bool:
    """Webhook hook: returns True if the Razorpay order belongs to a medicine order."""
    with get_db() as conn:
        row = conn.execute("SELECT id, total_paise FROM medicine_orders WHERE razorpay_order_id = ?",
                           (razorpay_order_id,)).fetchone()
    if not row:
        return False
    if amount is not None and (int(amount) != row["total_paise"] or currency != "INR"):
        log.error("Payment %s amount %s %s doesn't match order %s (%s paise); not marking paid",
                  payment_id, amount, currency, row["id"], row["total_paise"])
        return True
    mark_order_paid(row["id"], payment_id, background)
    return True


def handle_refund_event(payment_id: str, refund_id: str, ok: bool) -> bool:
    """Webhook hook for refund.processed / refund.failed on medicine orders."""
    with get_db() as conn:
        cur = conn.execute("UPDATE medicine_orders SET payment_status = ?, refund_id = ?, updated_at = ? "
                           "WHERE razorpay_payment_id = ?",
                           ("refunded" if ok else "refund_failed", refund_id if ok else None, stamp(), payment_id))
    return bool(cur.rowcount)


NEXT_STATUS = {
    "placed": {"accepted", "rejected"},
    "accepted": {"ready", "out_for_delivery", "rejected"},
    "ready": {"completed"},
    "out_for_delivery": {"completed"},
}


def wind_down_orders(where: str, params: tuple, reason: str, background: BackgroundTasks, notify: list) -> int:
    """Cancel open orders matching `where`, refund online payments and tell the other side."""
    with get_db() as conn:
        with write_txn(conn):
            rows = conn.execute(f"SELECT id, status, payment_status, prescription_id FROM medicine_orders WHERE {where} "
                                f"AND status IN ({','.join('?' * len(OPEN_ORDER_STATUSES))})",
                                (*params, *OPEN_ORDER_STATUSES)).fetchall()
            for r in rows:
                conn.execute("UPDATE medicine_orders SET status = 'cancelled', status_reason = ?, updated_at = ? "
                             "WHERE id = ?", (reason, stamp(), r["id"]))
            for rx_id in {r["prescription_id"] for r in rows}:
                reopen_requests(conn, rx_id)
    for r in rows:
        note = refund_order(r["id"]) if r["payment_status"] == "paid" else None
        if r["status"] != "pending_payment":
            with get_db() as conn:
                notify_order(background, conn, r["id"], notify, note)
    return len(rows)


def wind_down_patient_orders(patient_id: int, reason: str, background: BackgroundTasks) -> int:
    return wind_down_orders("patient_id = ?", (patient_id,), reason, background, ["pharmacy"])


def admin_order_query(status=None, pharmacy_id=None, date_from=None, date_to=None, q=None):
    sql, params = ORDER_SELECT + f" WHERE o.status != 'expired' AND {LIVE_ORDER}", [hold_cutoff()]
    if status:
        sql += " AND o.status = ?"
        params.append(status)
    if pharmacy_id:
        sql += " AND o.pharmacy_id = ?"
        params.append(pharmacy_id)
    if date_from:
        sql += " AND o.created_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND o.created_at < date(?, '+1 day')"
        params.append(date_to)
    if q:
        like = f"%{q.strip().lower()}%"
        sql += " AND (lower(o.reference) LIKE ? OR lower(pu.name) LIKE ? OR pu.phone LIKE ? OR lower(p.store_name) LIKE ?)"
        params += [like] * 4
    return sql + " ORDER BY o.created_at DESC LIMIT 1000", params


# Imported last, so slices can depend on each other in any order
from features.pharmacies.logic import get_pharmacy  # noqa: E402
from features.prescriptions.logic import LIVE_ORDER, ordered_item_ids, rx_is_live, rx_items  # noqa: E402
from features.chats import logic as chats  # noqa: E402
from features.medicine_orders import emails as medicine_orders_emails  # noqa: E402
from features.prescription_uploads import emails as prescription_uploads_emails  # noqa: E402

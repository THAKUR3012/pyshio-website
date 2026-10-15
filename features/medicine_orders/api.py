"""Medicine orders: the patient orders from a pharmacy, pays, and the pharmacy fulfils it.

This file: HTTP endpoints and request models.
"""
import csv
import io
import secrets
import uuid
from datetime import timedelta
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional

from shared import auth, payments, settings, validators as v
from shared.auth import admin_user, patient_user, pharmacy_user
from shared.common import audit, csv_safe, hold_cutoff, new_reference, stamp
from shared.db import get_db, write_txn


router = APIRouter()


class OrderIn(BaseModel):
    pharmacy_id: int
    fulfilment: Literal["pickup", "delivery"] = "pickup"
    payment_method: Literal["pharmacy", "online"] = "pharmacy"
    delivery_address: Optional[str] = Field(default=None, max_length=300)

    _address = field_validator("delivery_address")(v.optional_text)


class OrderStatusIn(BaseModel):
    status: Literal["accepted", "rejected", "ready", "out_for_delivery", "completed"]
    reason: Optional[str] = Field(default=None, max_length=300)
    otp: Optional[str] = Field(default=None, max_length=8)  # patient's handover code, needed to complete

    _reason = field_validator("reason")(v.optional_text)


class OrderCancelIn(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=300)

    _reason = field_validator("reason")(v.optional_text)


class PaymentVerify(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@router.post("/api/prescriptions/{rx_id}/orders")
def place_order(rx_id: str, data: OrderIn, background: BackgroundTasks, user: dict = Depends(patient_user)):
    with get_db() as conn:
        with write_txn(conn):
            expire_unpaid_orders(conn)
            rx, _ = rx_for(conn, rx_id, user)
            if not rx_is_live(rx):
                raise HTTPException(status_code=400, detail="This prescription has expired or was cancelled")
            req = conn.execute("SELECT * FROM rx_requests WHERE prescription_id = ? AND pharmacy_id = ?",
                               (rx_id, data.pharmacy_id)).fetchone()
            pharmacy = get_pharmacy(conn, data.pharmacy_id)
            if not req or req["status"] == "declined" or pharmacy["status"] != "approved" or not pharmacy["is_active"]:
                raise HTTPException(status_code=400, detail="This pharmacy isn't available for this prescription")
            option = next((o for o in price_options(conn, rx, max([rx["radius_km"]] + settings.WIDER_RADII_KM))
                           if o["pharmacy"]["id"] == data.pharmacy_id), None)
            if not option or not option["best"] or not option["best"]["available"]:
                raise HTTPException(status_code=400, detail="This pharmacy hasn't priced any of the remaining medicines yet")
            offer = option["best"]
            lines = [l for l in offer["lines"] if l["available"]]

            delivery_fee = 0
            address = None
            if data.fulfilment == "delivery":
                if not option["delivery_available"]:
                    raise HTTPException(status_code=400, detail="This pharmacy doesn't deliver to your location")
                delivery_fee = option["delivery_fee_paise"] or 0
                address = data.delivery_address or rx["home_address"]
                if not address:
                    raise HTTPException(status_code=400, detail="Enter a delivery address")
            online = data.payment_method == "online"
            if online and not option["pharmacy"]["accepts_online_payment"]:
                raise HTTPException(status_code=400, detail="This pharmacy doesn't accept online payment")

            order_id = str(uuid.uuid4())
            subtotal = sum(l["line_paise"] for l in lines)
            conn.execute(
                "INSERT INTO medicine_orders (id, reference, prescription_id, pharmacy_id, patient_id, quote_id, source, "
                "subtotal_paise, delivery_fee_paise, total_paise, fulfilment, delivery_address, distance_km, payment_method, "
                "payment_status, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (order_id, new_reference(conn, "medicine_orders", "MO"), rx_id, data.pharmacy_id, user["id"],
                 offer.get("quote_id"), offer["source"], subtotal, delivery_fee, subtotal + delivery_fee, data.fulfilment,
                 address, option["pharmacy"]["distance_km"], data.payment_method, "pending" if online else "unpaid",
                 "pending_payment" if online else "placed", stamp(), stamp()))
            conn.executemany("INSERT INTO order_items (order_id, prescription_item_id, medicine_id, name, quantity, "
                             "unit_price_paise, line_paise) VALUES (?, ?, ?, ?, ?, ?, ?)",
                             [(order_id, l["prescription_item_id"], l["medicine_id"], l["name"], l["quantity"],
                               l["unit_price_paise"], l["line_paise"]) for l in lines])
            losers = []
            if not online:
                losers = after_order_placed(conn, conn.execute("SELECT * FROM medicine_orders WHERE id = ?", (order_id,)).fetchone())

        if not online:
            notify_order(background, conn, order_id, ["pharmacy", "patient"])
            tell_losers(background, losers)
            return {"order": order_view(conn, conn.execute(ORDER_SELECT + " WHERE o.id = ?", (order_id,)).fetchone(),
                                        "patient"), "payment": None, "chat_id": chats.chat_for_order(conn, "medicine_order", order_id)}
        try:
            rzp = payments.create_order_paise(subtotal + delivery_fee, order_id, {"medicine_order_id": order_id})
        except payments.PaymentError as e:
            conn.execute("UPDATE medicine_orders SET status = 'expired', payment_status = 'unpaid', updated_at = ? "
                         "WHERE id = ?", (stamp(), order_id))
            raise HTTPException(status_code=502, detail=f"Couldn't start online payment ({e}). Try again or pay at the pharmacy.")
        conn.execute("UPDATE medicine_orders SET razorpay_order_id = ? WHERE id = ?", (rzp["id"], order_id))
        row = conn.execute(ORDER_SELECT + " WHERE o.id = ?", (order_id,)).fetchone()
        return {"order": order_view(conn, row, "patient"), "payment": checkout(row, user)}


@router.post("/api/orders/{order_id}/verify-payment")
def verify_order_payment(order_id: str, data: PaymentVerify, background: BackgroundTasks,
                         user: dict = Depends(patient_user)):
    with get_db() as conn:
        row = order_for(conn, order_id, user)
    if row["razorpay_order_id"] != data.razorpay_order_id or not payments.verify_payment_signature(
            data.razorpay_order_id, data.razorpay_payment_id, data.razorpay_signature):
        raise HTTPException(status_code=400, detail="Payment could not be verified")
    outcome = mark_order_paid(order_id, data.razorpay_payment_id, background)
    with get_db() as conn:
        return {"outcome": outcome, "order": order_view(conn, conn.execute(ORDER_SELECT + " WHERE o.id = ?",
                                                                            (order_id,)).fetchone(), "patient")}


@router.get("/api/orders/{order_id}/payment")
def resume_order_payment(order_id: str, user: dict = Depends(patient_user)):
    with get_db() as conn:
        row = order_for(conn, order_id, user)
        cutoff = stamp(settings.now() - timedelta(minutes=settings.PAYMENT_HOLD_MINUTES))
        if row["status"] != "pending_payment" or row["created_at"] <= cutoff:
            raise HTTPException(status_code=400, detail="This payment window has closed. Please order again.")
        return checkout(row, user)


@router.get("/api/my/orders")
def my_orders(user: dict = Depends(patient_user)):
    with get_db() as conn:
        rows = conn.execute(ORDER_SELECT + f" WHERE o.patient_id = ? AND o.status != 'expired' AND {LIVE_ORDER} "
                            "ORDER BY o.created_at DESC", (user["id"], hold_cutoff())).fetchall()
        return [order_view(conn, r, "patient") for r in rows]


@router.get("/api/orders/{order_id}")
def get_order(order_id: str, user: dict = Depends(auth.require("patient", "pharmacy", "admin"))):
    with get_db() as conn:
        return order_view(conn, order_for(conn, order_id, user), user["role"])


@router.post("/api/orders/{order_id}/cancel")
def cancel_order(order_id: str, data: OrderCancelIn, background: BackgroundTasks, user: dict = Depends(patient_user)):
    with get_db() as conn:
        with write_txn(conn):
            row = order_for(conn, order_id, user)
            if row["status"] not in ("pending_payment", "placed", "accepted"):
                raise HTTPException(status_code=400, detail="This order can no longer be cancelled; please call the pharmacy")
            conn.execute("UPDATE medicine_orders SET status = 'cancelled', status_reason = ?, updated_at = ? WHERE id = ?",
                         (data.reason or "Cancelled by patient", stamp(), order_id))
            reopen_requests(conn, row["prescription_id"])
            chats.note_on_order(conn, "medicine_order", order_id, chats.order_note("cancelled", "pharmacy"))
    note = refund_order(order_id) if row["payment_status"] == "paid" else None
    if row["status"] != "pending_payment":
        with get_db() as conn:
            notify_order(background, conn, order_id, ["pharmacy", "patient"], note)
    with get_db() as conn:
        return {"order": order_view(conn, conn.execute(ORDER_SELECT + " WHERE o.id = ?", (order_id,)).fetchone(),
                                    "patient"), "refund_note": note}


@router.get("/api/pharmacy/orders")
def pharmacy_orders(status: Optional[str] = None, user: dict = Depends(pharmacy_user)):
    sql, params = ORDER_SELECT + " WHERE o.pharmacy_id = ? AND o.status NOT IN ('pending_payment', 'expired')", [user["id"]]
    if status == "open":
        sql += " AND o.status IN ('placed', 'accepted', 'ready', 'out_for_delivery')"
    elif status:
        sql += " AND o.status = ?"
        params.append(status)
    with get_db() as conn:
        return [order_view(conn, r, "pharmacy") for r in conn.execute(sql + " ORDER BY o.created_at DESC", params)]


@router.post("/api/pharmacy/orders/{order_id}/status")
def update_order_status(order_id: str, data: OrderStatusIn, background: BackgroundTasks,
                        user: dict = Depends(pharmacy_user)):
    with get_db() as conn:
        with write_txn(conn):
            row = order_for(conn, order_id, user)
            if data.status not in NEXT_STATUS.get(row["status"], set()):
                raise HTTPException(status_code=400, detail=f"Can't change an order from {row['status']} to {data.status}")
            if data.status == "ready" and row["fulfilment"] != "pickup":
                raise HTTPException(status_code=400, detail="Use 'out for delivery' for delivery orders")
            if data.status == "out_for_delivery" and row["fulfilment"] != "delivery":
                raise HTTPException(status_code=400, detail="Use 'ready' for pickup orders")
            if data.status == "rejected" and not data.reason:
                raise HTTPException(status_code=400, detail="Please tell the patient why")
            if data.status == "completed" and row["delivery_otp"] and (data.otp or "").strip() != row["delivery_otp"]:
                raise HTTPException(status_code=400, detail="Enter the 4-digit handover code from the patient")
            payment_status = row["payment_status"]
            if data.status == "completed" and row["payment_method"] == "pharmacy":
                payment_status = "paid"  # collected at the counter / on delivery
            otp = row["delivery_otp"]
            if data.status in ("ready", "out_for_delivery") and not otp:
                otp = f"{secrets.randbelow(10000):04d}"  # proves the right person received the medicines
            conn.execute("UPDATE medicine_orders SET status = ?, status_reason = ?, payment_status = ?, updated_at = ?, "
                         "delivery_otp = ? WHERE id = ?",
                         (data.status, data.reason, payment_status, stamp(), otp, order_id))
            if data.status == "rejected":
                reopen_requests(conn, row["prescription_id"])
            if data.status == "completed":
                record_courses(conn, order_id)
            chats.note_on_order(conn, "medicine_order", order_id, chats.order_note(data.status, "pharmacy", data.reason))
    note = refund_order(order_id) if data.status == "rejected" and row["payment_status"] == "paid" else None
    if data.status == "rejected":
        note = (note + " " if note else "") + "You can order the same medicines from another nearby pharmacy."
    with get_db() as conn:
        notify_order(background, conn, order_id, ["patient"], note)
        return order_view(conn, conn.execute(ORDER_SELECT + " WHERE o.id = ?", (order_id,)).fetchone(), "pharmacy")


# ---------- Invoices ----------
@router.get("/api/orders/{order_id}/invoice")
def order_invoice(order_id: str, user: dict = Depends(auth.require("patient", "pharmacy", "admin"))):
    with get_db() as conn:
        row = order_for(conn, order_id, user)
        items = order_items(conn, order_id)
        mrps = {r["prescription_item_id"]: r["mrp_paise"] for r in conn.execute(
            "SELECT qi.prescription_item_id, qi.mrp_paise FROM quote_items qi WHERE qi.quote_id = ?", (row["quote_id"],))} \
            if row["quote_id"] else {}
        for it in items:
            if it["medicine_id"] and it["prescription_item_id"] not in mrps:
                med = conn.execute("SELECT mrp_paise, pack_size FROM medicines WHERE id = ?", (it["medicine_id"],)).fetchone()
                if med and med["mrp_paise"]:
                    mrps[it["prescription_item_id"]] = unit_price(med["mrp_paise"], med["pack_size"])
    if row["status"] != "completed" and row["payment_status"] != "paid":
        raise HTTPException(status_code=400, detail="An invoice is available once the order is paid or completed")
    return {
        "kind": "Tax invoice" if row["gstin"] else "Invoice", "reference": row["reference"], "date": row["updated_at"][:10],
        "seller": {"name": row["store_name"], "detail": f"Drug licence {row['drug_licence_no']} · Pharmacist {row['pharmacist_name']}",
                   "address": f"{row['pharmacy_address']}, {row['pharmacy_city']}", "gstin": row["gstin"]},
        "buyer": {"name": row["patient_name"], "phone": row["patient_phone"], "address": row["delivery_address"]},
        "prescription": row["rx_reference"],
        "lines": [{"description": i["name"], "quantity": i["quantity"], "unit_paise": i["unit_price_paise"],
                   "mrp_paise": mrps.get(i["prescription_item_id"]), "amount_paise": i["line_paise"]} for i in items],
        "delivery_fee_paise": row["delivery_fee_paise"], "total_paise": row["total_paise"],
        "payment": {"method": "Online" if row["payment_method"] == "online" else "At pharmacy",
                    "status": row["payment_status"], "reference": row["razorpay_payment_id"]},
    }


@router.get("/api/admin/orders")
def admin_orders(status: Optional[str] = None, pharmacy_id: Optional[int] = None, date_from: Optional[str] = None,
                 date_to: Optional[str] = None, q: Optional[str] = None, user: dict = Depends(admin_user)):
    sql, params = admin_order_query(status, pharmacy_id, date_from, date_to, q)
    with get_db() as conn:
        return [order_view(conn, r, "admin") for r in conn.execute(sql, params)]


@router.get("/api/admin/orders.csv")
def admin_orders_csv(status: Optional[str] = None, pharmacy_id: Optional[int] = None, date_from: Optional[str] = None,
                     date_to: Optional[str] = None, q: Optional[str] = None, user: dict = Depends(admin_user)):
    sql, params = admin_order_query(status, pharmacy_id, date_from, date_to, q)
    fields = ["reference", "created_at", "patient_name", "patient_phone", "store_name", "fulfilment", "distance_km",
              "subtotal_paise", "delivery_fee_paise", "total_paise", "payment_method", "payment_status", "status"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Sr No"] + fields)
    with get_db() as conn:
        for i, r in enumerate(conn.execute(sql, params), 1):
            writer.writerow([i] + [csv_safe(r[f]) for f in fields])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="medicine-orders-{settings.now().date()}.csv"'})


@router.post("/api/admin/orders/{order_id}/retry-refund")
def admin_retry_order_refund(order_id: str, user: dict = Depends(admin_user)):
    with get_db() as conn:
        row = order_for(conn, order_id, user)
    if row["payment_status"] != "refund_failed":
        raise HTTPException(status_code=400, detail="No failed refund on this order")
    note = refund_order(order_id)
    with get_db() as conn:
        audit(conn, user["id"], "order.retry_refund", f"order:{order_id}", note)
        refunded = conn.execute("SELECT payment_status FROM medicine_orders WHERE id = ?", (order_id,)).fetchone()
    if refunded["payment_status"] == "refunded":
        appointments_emails.send_refund_done(row["patient_name"], row["patient_email"], row["reference"], note)
    return {"note": note}


# Imported last, so slices can depend on each other in any order
from features.medicine_courses.logic import record_courses  # noqa: E402
from features.medicine_orders.logic import admin_order_query, after_order_placed, checkout, expire_unpaid_orders, mark_order_paid, NEXT_STATUS, notify_order, order_for, order_items, ORDER_SELECT, order_view, refund_order, reopen_requests, tell_losers  # noqa: E402
from features.pharmacies.logic import get_pharmacy  # noqa: E402
from features.pharmacy_offers.logic import price_options, unit_price  # noqa: E402
from features.prescriptions.logic import LIVE_ORDER, rx_for, rx_is_live  # noqa: E402
from features.appointments import emails as appointments_emails  # noqa: E402
from features.chats import logic as chats  # noqa: E402

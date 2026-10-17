"""Online payments: the Razorpay webhook for appointments and medicine orders.

This file: HTTP endpoints and request models.
"""
import json
import logging
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from shared import payments
from shared.common import stamp
from shared.db import get_db


router = APIRouter()
log = logging.getLogger("pyshio.online_payments")


@router.post("/api/payments/webhook", include_in_schema=False)
async def razorpay_webhook(request: Request, background: BackgroundTasks):
    """Backup for patients who close the browser right after paying."""
    body = await request.body()
    if not payments.verify_webhook_signature(body, request.headers.get("X-Razorpay-Signature", "")):
        raise HTTPException(status_code=400, detail="Bad signature")
    try:
        event = json.loads(body)
        name = event.get("event")
        payload = event.get("payload") or {}
        payment = (payload.get("payment") or {}).get("entity") or {}
        refund = (payload.get("refund") or {}).get("entity") or {}
    except (ValueError, AttributeError):
        log.error("Unreadable Razorpay webhook body")
        return {"ok": True}
    if name in ("payment.captured", "order.paid") and payment.get("id") and payment.get("order_id"):
        amount, currency = payment.get("amount"), payment.get("currency", "INR")
        with get_db() as conn:
            row = conn.execute("SELECT id, fee FROM appointments WHERE razorpay_order_id = ?",
                               (payment["order_id"],)).fetchone()
        if row:
            if amount is not None and (int(amount) != row["fee"] * 100 or currency != "INR"):
                log.error("Payment %s amount %s %s doesn't match appointment %s; not marking paid",
                          payment["id"], amount, currency, row["id"])
            else:
                mark_paid(row["id"], payment["id"], background)
        else:
            medicine_orders.handle_razorpay_payment(payment["order_id"], payment["id"], background, amount, currency)
    elif name == "payment.failed":
        # Razorpay lets the patient retry inside the same checkout, so the hold simply runs out if they give up
        log.info("Payment attempt failed for order %s: %s", payment.get("order_id"),
                 (payment.get("error_description") or "no reason given"))
    elif name in ("refund.processed", "refund.failed") and refund.get("payment_id"):
        ok = name == "refund.processed"
        with get_db() as conn:
            cur = conn.execute("UPDATE appointments SET payment_status = ?, refund_id = ?, updated_at = ? "
                               "WHERE razorpay_payment_id = ?",
                               ("refunded" if ok else "refund_failed", refund.get("id") if ok else None, stamp(),
                                refund["payment_id"]))
        if not cur.rowcount:
            medicine_orders.handle_refund_event(refund["payment_id"], refund.get("id"), ok)
    return {"ok": True}


# Imported last, so slices can depend on each other in any order
from features.appointments.logic import mark_paid  # noqa: E402
from features.medicine_orders import logic as medicine_orders  # noqa: E402

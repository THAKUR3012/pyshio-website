"""Razorpay: create orders, verify payments and webhooks, issue refunds.

Uses Razorpay's REST API directly (no SDK) so nothing extra needs installing on shared hosting.
Docs: https://razorpay.com/docs/api/
"""
import base64
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request

from shared import settings


log = logging.getLogger("pyshio.payments")
API_BASE = "https://api.razorpay.com/v1"


class PaymentError(Exception):
    pass


def _request(method: str, path: str, payload: dict = None) -> dict:
    credentials = base64.b64encode(f"{settings.RAZORPAY_KEY_ID}:{settings.RAZORPAY_KEY_SECRET}".encode()).decode()
    req = urllib.request.Request(
        API_BASE + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        log.error("Razorpay %s %s failed: %s %s", method, path, e.code, detail)
        try:
            message = json.loads(detail)["error"]["description"]
        except (ValueError, KeyError, TypeError):
            message = f"HTTP {e.code}"
        raise PaymentError(message)
    except (urllib.error.URLError, TimeoutError) as e:
        log.error("Razorpay %s %s unreachable: %s", method, path, e)
        raise PaymentError("Payment service unreachable")


def create_order_paise(amount_paise: int, receipt: str, notes: dict) -> dict:
    return _request("POST", "/orders", {
        "amount": int(amount_paise),
        "currency": "INR",
        "receipt": receipt[:40],
        "notes": notes,
    })


def create_order(amount_rupees: int, receipt: str, notes: dict) -> dict:
    return create_order_paise(amount_rupees * 100, receipt, notes)


def refund_paise(payment_id: str, amount_paise: int) -> dict:
    return _request("POST", f"/payments/{payment_id}/refund", {"amount": int(amount_paise)})


def refund(payment_id: str, amount_rupees: int) -> dict:
    return refund_paise(payment_id, amount_rupees * 100)


def verify_payment_signature(order_id: str, payment_id: str, signature: str) -> bool:
    expected = hmac.new(settings.RAZORPAY_KEY_SECRET.encode(), f"{order_id}|{payment_id}".encode(),
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    if not settings.RAZORPAY_WEBHOOK_SECRET:
        return False
    expected = hmac.new(settings.RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")

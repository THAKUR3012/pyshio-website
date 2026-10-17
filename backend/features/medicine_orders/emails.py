"""Medicine orders: the patient orders from a pharmacy, pays, and the pharmacy fulfils it.

This file: emails.
"""
from __future__ import annotations

from shared import mailer
from shared.mailer import link, money, render


ORDER_HEADLINES = {
    "placed": "New medicine order", "accepted": "Your order was accepted", "ready": "Your medicines are ready for pickup",
    "out_for_delivery": "Your medicines are on the way", "completed": "Order completed",
    "rejected": "The pharmacy couldn't fulfil your order", "cancelled": "Order cancelled",
}


def send_order_update(order: dict, recipient: str, note: str = None) -> bool:
    """recipient: 'patient' or 'pharmacy'."""
    status = order["status"]
    headline = ORDER_HEADLINES.get(status, "Order update")
    if recipient == "patient" and status == "placed":
        headline = "Your order has been placed"
    details = [("Order", order["reference"]), ("Pharmacy", f"{order['store_name']}, {order['pharmacy_address']}"),
               ("Items", ", ".join(f"{i['name']} × {i['quantity']}" for i in order["items"])),
               ("Total", f"{money(order['total_paise'])} · {'Paid online' if order['payment_status'] == 'paid' else 'Pay at pharmacy'}"),
               ("Fulfilment", "Home delivery" if order["fulfilment"] == "delivery" else "Pickup")]
    if recipient == "pharmacy":
        details += [("Patient", order["patient_name"]), ("Phone", order["patient_phone"] or "—")]
        if order["fulfilment"] == "delivery":
            details.append(("Deliver to", order["delivery_address"] or "—"))
    if order.get("status_reason"):
        details.append(("Reason", order["status_reason"]))
    if recipient == "patient" and order.get("delivery_otp") and status in ("ready", "out_for_delivery"):
        details.append(("Handover code", f"{order['delivery_otp']} (give this to the pharmacy when you receive your medicines)"))
    intro = note or ""
    to = order["pharmacy_email"] if recipient == "pharmacy" else order["patient_email"]
    button = ("Open orders", link("/pharmacy")) if recipient == "pharmacy" else ("View my orders", link("/account#orders"))
    colour = "#b91c1c" if status in ("rejected", "cancelled") else "#0284c7"
    text, html = render(headline, intro, details, "Questions? Call the pharmacy directly.", colour, button)
    return mailer.send_email(to, f"{headline} ({order['reference']})", text, html,
                      reply_to=order["patient_email"] if recipient == "pharmacy" else None)


def send_order_reminder(order: dict, minutes: int) -> bool:
    text, html = render("Order waiting for you",
                        f"Order {order['reference']} has been waiting {minutes} minutes. Please accept or reject it; "
                        "unaccepted orders are cancelled automatically.",
                        [("Items", ", ".join(f"{i['name']} × {i['quantity']}" for i in order["items"])),
                         ("Total", money(order["total_paise"]))], "", "#b45309", button=("Open orders", link("/pharmacy")))
    return mailer.send_email(order["pharmacy_email"], f"Action needed: order {order['reference']}", text, html)

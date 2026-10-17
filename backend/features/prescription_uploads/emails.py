"""Prescription uploads: a patient uploads any prescription, nearby pharmacies and labs quote.

This file: emails.
"""
from __future__ import annotations

from shared import mailer
from shared.mailer import link, money, render


# ---------- Uploaded prescriptions ----------
def send_upload_request(email: str, partner_name: str, reference: str, kind: str, distance_km: float, note: str = None) -> bool:
    details = [("Request", reference), ("Needs", kind.title()), ("Distance", f"{distance_km:.1f} km")]
    if note:
        details.append(("Patient's note", note))
    text, html = render("A patient nearby uploaded a prescription", f"Hi {partner_name}, open it, read the {kind} and send your quotation.",
                        details, "The patient sees every quotation sorted by price and speed and books the one they like.",
                        "#0891b2", button=("Send a quotation", link("/lab" if kind == "tests" else "/pharmacy")))
    return mailer.send_email(email, f"Uploaded prescription {reference}: quote for {kind}", text, html)


def send_upload_quote(email: str, name: str, reference: str, partner_name: str, subtotal_paise: int, available: int, total: int) -> bool:
    text, html = render("You have a new quotation", f"Hi {name}, {partner_name} replied to your uploaded prescription.",
                        [("Prescription", reference), ("Price", money(subtotal_paise)), ("Items available", f"{available} of {total}")],
                        "Compare all quotations, sorted cheapest and fastest first, then book.", "#059669",
                        button=("Compare quotations", link("/account#uploads")))
    return mailer.send_email(email, f"New quotation for {reference}: {money(subtotal_paise)}", text, html)


def send_upload_order(order: dict, recipient: str, note: str = None) -> bool:
    labels = {"placed": "New booking from an uploaded prescription", "accepted": "Your booking is confirmed",
              "ready": "Your medicines are ready", "sample_collected": "Sample collected", "report_ready": "Your report is ready",
              "completed": "Order completed", "rejected": "Your booking couldn't be taken", "cancelled": "Booking cancelled"}
    headline = labels.get(order["status"], "Booking update")
    how = {"pickup": "Pick up from the pharmacy", "delivery": "Home delivery", "visit": "Visit the lab", "home": "Home sample collection"}
    details = [("Booking", order["reference"]), ("With", order["partner_name"]),
               ("Items", ", ".join(f"{l['name']} × {l['quantity']}" for l in order["lines"])),
               ("Total", money(order["total_paise"])), ("How", how[order["fulfilment"]])]
    if order.get("preferred_date"):
        details.append(("Preferred", f"{order['preferred_date']} {order.get('preferred_time') or ''}".strip()))
    if recipient == "partner":
        details += [("Patient", order["patient_name"]), ("Phone", order.get("patient_phone") or "-")]
        if order.get("address"):
            details.append(("Address", order["address"]))
    elif order["status"] == "ready" and order.get("handover_code"):
        details.append(("Handover code", order["handover_code"]))
    if note:
        details.append(("Note", note))
    to = order["partner_email"] if recipient == "partner" else order["patient_email"]
    page = ("/lab" if order["partner_role"] == "lab" else "/pharmacy") if recipient == "partner" else "/account#uploads"
    text, html = render(headline, "", details, "Pay at the counter or on delivery / collection.", "#0891b2",
                        button=("Open dashboard", link(page)))
    return mailer.send_email(to, f"{headline} ({order['reference']})", text, html)


def send_patient_chose_other(email: str, partner_name: str, reference: str, kind: str) -> bool:
    """A pharmacy / lab that quoted is told the patient went with someone else, so they can stop preparing."""
    text, html = render("The patient chose another " + ("lab" if kind == "tests" else "pharmacy"),
                        f"Hi {partner_name}, thanks for your quotation. The patient has booked elsewhere, so this request is now closed.",
                        [("Request", reference)], "No action needed. New requests near you keep coming in on your dashboard.", "#475569")
    return mailer.send_email(email, f"Request {reference} closed: patient chose another " + ("lab" if kind == "tests" else "pharmacy"), text, html)

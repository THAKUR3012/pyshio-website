"""Pharmacies: registration, profile, nearby search, stats and admin approval.

This file: emails.
"""
from __future__ import annotations

from shared import mailer
from shared.mailer import link, render


def send_pharmacy_application(pharmacy: dict, admin_emails: list) -> int:
    text, html = render(
        "Thanks for registering your pharmacy", f"Hi {pharmacy['name']}, {pharmacy['store_name']} has been submitted for verification.",
        [("Drug licence no.", pharmacy["drug_licence_no"]), ("Pharmacist", pharmacy["pharmacist_name"])],
        "We'll email you once it's approved. Meanwhile you can upload your price list.",
        button=("Open pharmacy dashboard", link("/pharmacy")))
    sent = mailer.send_email(pharmacy["email"], f"{pharmacy['store_name']} is under review", text, html)
    text, html = render(
        "New pharmacy awaiting approval", "",
        [("Store", pharmacy["store_name"]), ("Drug licence no.", pharmacy["drug_licence_no"]),
         ("Pharmacist", f"{pharmacy['pharmacist_name']} ({pharmacy['pharmacist_reg_no']})"),
         ("GSTIN", pharmacy.get("gstin") or "—"), ("Address", f"{pharmacy['address']}, {pharmacy['city']}"),
         ("Contact", f"{pharmacy['name']} · {pharmacy['email']} · {pharmacy.get('phone') or ''}")],
        "Verify the drug licence before approving.", button=("Review in admin", link("/admin")))
    return sent + sum(mailer.send_email(a, f"Pharmacy approval needed: {pharmacy['store_name']}", text, html)
                      for a in admin_emails)

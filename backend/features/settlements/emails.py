"""Settlements: online payments owed to doctors and pharmacies, and payouts.

This file: emails.
"""
from __future__ import annotations

from shared import mailer, settings
from shared.mailer import money, render


def send_payout_recorded(name: str, email: str, amount_paise: int, reference: str = None) -> bool:
    details = [("Amount", money(amount_paise))] + ([("Transaction ref.", reference)] if reference else [])
    text, html = render("Payout sent", f"Hi {name}, we've transferred your online earnings.", details,
                        "It may take 1–2 working days to show in your bank.", "#059669")
    return mailer.send_email(email, f"{settings.PLATFORM_NAME} payout: {money(amount_paise)}", text, html)

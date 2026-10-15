"""Accounts: sign-up, login, password reset, profile, home location, consent, data export and account closure.

This file: emails.
"""
from __future__ import annotations

from shared import mailer, settings
from shared.mailer import link, render


# ---------- Account emails ----------
def send_password_reset(to: str, name: str, token: str) -> bool:
    url = link(f"/login#reset={token}") or f"/login#reset={token}"
    text, html = render(
        "Reset your password", f"Hi {name}, we received a request to reset your password.",
        [("Link valid for", f"{settings.PASSWORD_RESET_HOURS} hours")],
        "If you didn't ask for this, ignore this email; your password won't change.",
        button=("Choose a new password", url),
    )
    return mailer.send_email(to, f"Reset your {settings.PLATFORM_NAME} password", text, html, notify=False)


def send_account_disabled(name: str, email: str) -> bool:
    text, html = render("Your account has been disabled", f"Hi {name},", [],
                        "Any upcoming bookings or orders were cancelled and online payments refunded. "
                        "Reply to this email if you think this is a mistake.", "#b91c1c")
    return mailer.send_email(email, f"{settings.PLATFORM_NAME}: your account has been disabled", text, html)


def send_welcome(name: str, email: str) -> bool:
    text, html = render(f"Welcome to {settings.PLATFORM_NAME}", f"Hi {name},",
                        [("Next step", "Pin your home on the map so we can find doctors and pharmacies near you")],
                        "Questions? Just reply to this email.", button=("Set my home location", link("/account#profile")))
    return mailer.send_email(email, f"Welcome to {settings.PLATFORM_NAME}", text, html)


def send_invite(name: str, email: str, doctor_name: str, token: str) -> bool:
    url = link(f"/login#reset={token}") or f"/login#reset={token}"
    text, html = render(f"{doctor_name} booked an appointment for you",
                        f"Hi {name}, set a password to see your appointment, prescriptions and medicine prices nearby.",
                        [("Link valid for", f"{settings.PASSWORD_RESET_HOURS} hours (you can request a new one any time)")],
                        "You can also ignore this email; your appointment stays booked.", button=("Set my password", url))
    return mailer.send_email(email, f"Your appointment with {doctor_name} on {settings.PLATFORM_NAME}", text, html, notify=False)


def send_account_closed(name: str, email: str) -> bool:
    text, html = render("Your account has been closed", f"Hi {name},", [],
                        "Your personal details have been removed. Records we must keep by law are retained without "
                        "your name or contact details.", "#475569")
    return mailer.send_email(email, f"{settings.PLATFORM_NAME}: account closed", text, html)

"""Follow-ups: the doctor sets a follow-up date on a prescription; the patient is reminded and books in one tap.

This file: emails.
"""
from __future__ import annotations

from datetime import date

from shared import mailer
from shared.mailer import link, render


def send_follow_up_due(email: str, name: str, doctor_name: str, follow_up_on: str, path: str,
                       for_label: str = None) -> bool:
    day = date.fromisoformat(follow_up_on).strftime("%A, %d %B %Y").replace(" 0", " ")
    who = f" for {for_label}" if for_label else ""
    text, html = render(f"Follow-up with {doctor_name} due",
                        f"Hi {name}, {doctor_name} asked to see you again{who} around {day}.",
                        [("Doctor", doctor_name), ("Follow-up on", day)],
                        "Booking takes a minute; pick any free time that suits you.",
                        "#0f766e", button=("Book the follow-up", link(path)))
    return mailer.send_email(email, f"Follow-up with {doctor_name} on {day}", text, html)

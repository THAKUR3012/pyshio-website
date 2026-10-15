"""Medicine reminders: today's doses from prescriptions and own medicines, taken / skipped log and adherence.

This file: emails.
"""
from __future__ import annotations

from shared import mailer
from shared.mailer import link, render


def send_dose_digest(email: str, name: str, schedule: dict) -> bool:
    details = []
    for g in schedule["groups"]:
        meds = ", ".join(f"{d['name']}{' ×' + d['amount'] if d['amount'] not in ('1', '') else ''}"
                         f"{' (for ' + d['for_name'] + ')' if d.get('for_name') else ''}" for d in g["doses"])
        details.append((f"{g['label']} · {g['time']}", meds))
    text, html = render("Today's medicines", f"Good morning {name}, here's what's due today.", details,
                        "Tick each dose on Pyshio as you take it to keep your record (and your doctor) up to date.",
                        "#0d9488", button=("Open today's doses", link("/account#today")))
    return mailer.send_email(email, f"Today's medicines: {schedule['summary']['due']} doses", text, html)

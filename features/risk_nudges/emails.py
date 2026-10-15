"""Risk nudges: a no-show score for upcoming visits with one "Still coming?" nudge, and gentle alerts when medicine-taking drops.

This file: emails.
"""
from __future__ import annotations

from datetime import date

from shared import mailer
from shared.mailer import link, render


def send_still_coming(row: dict) -> bool:
    day = date.fromisoformat(row["appointment_date"]).strftime("%A, %d %B").replace(" 0", " ")
    text, html = render(f"Still coming to {row['doctor_name']} today?",
                        f"Hi {row['patient_name']}, your visit is at {row['appointment_time']} on {day}. "
                        "Please confirm, or reschedule so someone else can use the time.",
                        [("Doctor", row["doctor_name"]), ("Clinic", f"{row['clinic_name']}, {row['city']}"),
                         ("Time", f"{day}, {row['appointment_time']}"), ("Reference", row["reference"])],
                        "Plans changed? Rescheduling takes a minute.", "#0284c7",
                        button=("Confirm or reschedule", link("/account#appointments")))
    return mailer.send_email(row["patient_email"], f"Still coming at {row['appointment_time']}? ({row['reference']})", text, html)


def send_adherence_nudge(email: str, name: str, drop: dict) -> bool:
    worst = f" {drop['worst']} seems to be the one most often missed." if drop.get("worst") else ""
    text, html = render("A gentle reminder about your medicines",
                        f"Hi {name}, this week you marked {drop['recent']}% of your doses as taken, down from "
                        f"{drop['before']}% before.{worst}",
                        [("This week", f"{drop['recent']}%"), ("Earlier", f"{drop['before']}%")],
                        "If side effects, cost or the timing is the problem, tell your doctor: there is often an easier "
                        "option. You can change reminder times in Today's medicines.", "#0f766e",
                        button=("Open today's medicines", link("/account#today")))
    return mailer.send_email(email, "How are your medicines going?", text, html)

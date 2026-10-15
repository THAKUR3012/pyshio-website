"""Doctors: registration, the public directory and profiles, opening hours, services, leave, admin approval.

This file: emails.
"""
from __future__ import annotations

from shared import mailer, settings
from shared.mailer import link, render


def send_doctor_application_received(doctor: dict) -> bool:
    text, html = render(
        "Thanks for registering", f"Hi {doctor['name']}, your profile has been submitted for verification.",
        [("Registration no.", doctor["registration_no"]), ("Clinic", doctor["clinic_name"])],
        "We'll email you once it's approved. Meanwhile you can log in to set your hours and services.",
        button=("Open doctor dashboard", link("/doctor")),
    )
    return mailer.send_email(doctor["email"], f"Your {settings.PLATFORM_NAME} doctor profile is under review", text, html)


def send_new_doctor_to_admins(admin_emails: list, doctor: dict) -> int:
    text, html = render(
        "New doctor awaiting approval", "",
        [("Name", doctor["name"]), ("Qualification", doctor["qualification"]),
         ("Registration no.", doctor["registration_no"]),
         ("Council", doctor.get("registration_council") or "—"),
         ("Clinic", f"{doctor['clinic_name']}, {doctor['city']}"), ("Email", doctor["email"]),
         ("Phone", doctor.get("phone") or "—")],
        "Verify the registration number before approving.",
        button=("Review in admin", link("/admin")),
    )
    return sum(mailer.send_email(a, f"Doctor approval needed: {doctor['name']}", text, html) for a in admin_emails)


def send_doctor_status(doctor: dict, status: str, reason: str = None) -> bool:
    if status == "approved":
        headline, footer = "Your profile is live", "Patients can now find and book you."
    elif status == "rejected":
        headline, footer = "Your profile wasn't approved", "Reply to this email if you have questions."
    else:
        headline, footer = "Your profile has been suspended", "Reply to this email if you have questions."
    details = [("Reason", reason)] if reason else []
    text, html = render(headline, f"Hi {doctor['name']},", details, footer,
                        "#059669" if status == "approved" else "#b91c1c",
                        button=("Open doctor dashboard", link("/doctor")))
    return mailer.send_email(doctor["email"], f"{settings.PLATFORM_NAME}: {headline}", text, html)

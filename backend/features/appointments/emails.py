"""Appointments: booking, online payment, cancel / reschedule, messages, receipts, walk-ins, doctor and admin appointment lists, slot scheduling and reminders.

This file: emails.
"""
from __future__ import annotations

from shared import mailer, settings
from shared.mailer import ics_for, link, render, rupees, when


# ---------- Appointment emails ----------
COLOURS = {"cancelled": "#b91c1c", "rescheduled": "#b45309", "message": "#4f46e5"}
ACTORS = {"patient": "the patient", "doctor": "the doctor", "admin": f"{settings.PLATFORM_NAME} support",
          "system": f"{settings.PLATFORM_NAME}"}


def _appointment_details(appt: dict, for_doctor: bool, previous: dict = None) -> list:
    details = [("When", when(appt))]
    if previous and (previous["appointment_date"], previous["appointment_time"]) != \
            (appt["appointment_date"], appt["appointment_time"]):
        details.append(("Was", when(previous)))
    home = appt.get("visit_type") == "home"
    video = appt.get("visit_type") == "video"
    if for_doctor:
        details += [("Patient", person_label(appt)), ("Phone", appt["patient_phone"] or "—"),
                    ("Email", appt["patient_email"])]
    else:
        details += [("Doctor", appt["doctor_name"])]
        if appt.get("member_name"):
            details.append(("For", label(appt["member_name"], appt.get("member_relation"))))
        if not home and not video:
            details.append(("Clinic", f"{appt['clinic_name']}, {appt['address']}, {appt['city']}"))
    if video:
        details.append(("Video consultation", f"Join from {link(join_path(appt['id'])) or join_path(appt['id'])} "
                                              f"(opens {settings.VIDEO_JOIN_EARLY_MINUTES} min before)"))
    if home:
        details.append(("Home visit at", appt.get("visit_address") or "—"))
    payment = "Paid online" if appt["payment_status"] == "paid" else (
        ("Pay the doctor at the visit" if home else "Pay at clinic") if appt["payment_method"] == "clinic"
        else appt["payment_status"].replace("_", " ").title())
    travel = appt.get("travel_fee") or 0
    details += [
        ("Service", f"{appt['service_name']} ({appt['duration']} min)"),
        ("Fee", f"{rupees(appt['fee'] + travel)}" + (f" ({rupees(appt['fee'])} visit + {rupees(travel)} travel)" if travel else "")
         + f" · {payment}"),
        ("Reference", appt["reference"]),
    ]
    if home and appt.get("visit_note"):
        details.append(("Doctor's note", appt["visit_note"]))
    if for_doctor and appt.get("reason"):
        details.append(("Patient's reason", appt["reason"]))
    return details


def appointment_email(kind: str, appt: dict, recipient: str, previous: dict = None, by: str = "patient",
                      message: str = None, refund_note: str = None):
    """Build (subject, text, html, ics) for `recipient` in ('patient', 'doctor')."""
    for_doctor = recipient == "doctor"
    other = appt["patient_name"] if for_doctor else appt["doctor_name"]
    headline = {
        "confirmed": "New appointment booked" if for_doctor else "Your appointment is confirmed",
        "rescheduled": "Appointment rescheduled" if for_doctor else "Your appointment has been moved",
        "cancelled": "Appointment cancelled" if for_doctor else "Your appointment has been cancelled",
        "reminder": "Reminder: your appointment is coming up",
        "message": f"New message from {other}",
    }[kind]
    subject = f"{headline} – {when(appt)} ({appt['reference']})"
    if kind == "message":
        subject = f"{headline} about your appointment on {when(appt)} ({appt['reference']})"

    intro = ""
    if kind in ("rescheduled", "cancelled") and by in ACTORS:
        intro = f"{'Changed' if kind == 'rescheduled' else 'Cancelled'} by {ACTORS[by]}."
    if message:
        intro = (intro + "\n\n" if intro else "") + f"Message from {ACTORS.get(by, by)}:\n“{message}”"
    if refund_note and not for_doctor:
        intro = (intro + "\n\n" if intro else "") + refund_note

    details = _appointment_details(appt, for_doctor, previous)
    button = ("Open your doctor dashboard", link("/doctor")) if for_doctor else ("View my appointments", link("/account"))
    footer = ("Reply to this email to contact the patient." if for_doctor
              else "Need to change or cancel? Use My Appointments on our website.")
    text, html = render(headline, intro, details, footer, COLOURS.get(kind, "#0284c7"), button)
    ics = ics_for(appt) if kind in ("confirmed", "rescheduled") else None
    return subject, text, html, ics


def send_appointment_emails(kind: str, appt: dict, previous: dict = None, by: str = "patient",
                            message: str = None, refund_note: str = None) -> int:
    """Email the patient and/or doctor about an appointment event. Returns emails sent."""
    if kind == "reminder":
        recipients = ["patient"]
    elif kind == "message":
        recipients = ["doctor"] if by == "patient" else ["patient"]
    else:
        recipients = ["patient", "doctor"]
    sent = 0
    for r in recipients:
        to = appt["doctor_email"] if r == "doctor" else appt["patient_email"]
        reply_to = appt["patient_email"] if r == "doctor" else None
        subject, text, html, ics = appointment_email(kind, appt, r, previous, by, message, refund_note)
        sent += mailer.send_email(to, subject, text, html, ics, reply_to=reply_to)
    return sent


def send_refund_done(name: str, email: str, reference: str, note: str) -> bool:
    text, html = render("Your refund has been issued", f"Hi {name},", [("Reference", reference)], note, "#059669")
    return mailer.send_email(email, f"Refund issued ({reference})", text, html)


from features.family.logic import label, person_label  # noqa: E402
from features.video_consults.logic import join_path  # noqa: E402

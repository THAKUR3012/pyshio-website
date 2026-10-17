"""Home visits: the patient asks, the doctor sends charges and time, the patient accepts.

This file: emails.
"""
from __future__ import annotations

from shared import mailer
from shared.mailer import link, render, rupees, when


# ---------- Home visits ----------
def send_home_visit_request(appt: dict) -> bool:
    text, html = render("New home-visit request", f"{appt['patient_name']} would like you to visit them at home.",
                        [("Preferred", when(appt)), ("Address", appt.get("visit_address") or "-"), ("Service", appt["service_name"]),
                         ("Phone", appt["patient_phone"] or "-"), ("Reason", appt.get("reason") or "-"), ("Reference", appt["reference"])],
                        "Send your charges and the final time from your dashboard; the patient confirms before the visit is booked.",
                        "#b45309", button=("Send charges & time", link("/doctor#requests")))
    return mailer.send_email(appt["doctor_email"], f"Home-visit request from {appt['patient_name']} ({appt['reference']})", text, html,
                      reply_to=appt["patient_email"])


def send_home_visit_quote(appt: dict) -> bool:
    travel = appt.get("travel_fee") or 0
    details = [("Doctor", appt["doctor_name"]), ("When", when(appt)), ("Duration", f"{appt['duration']} min"),
               ("Visit fee", rupees(appt["fee"])), ("Travel charge", rupees(travel)), ("Total", rupees(appt["fee"] + travel)),
               ("Address", appt.get("visit_address") or "-")]
    if appt.get("visit_note"):
        details.append(("Doctor's note", appt["visit_note"]))
    text, html = render("The doctor sent charges for your home visit", f"Hi {appt['patient_name']},", details,
                        "Accept to confirm the visit, or decline if it doesn't suit you. You pay the doctor at the visit.",
                        "#0284c7", button=("Accept or decline", link("/account#appointments")))
    return mailer.send_email(appt["patient_email"], f"Home visit: {rupees(appt['fee'] + travel)} on {when(appt)} ({appt['reference']})", text, html)

"""Prescriptions: doctors write them (medicines, tests, diagnoses, refills); patients and pharmacies read them.

This file: emails.
"""
from __future__ import annotations

from shared import mailer
from shared.mailer import link, render


def send_rx_to_patient(patient: dict, rx: dict, nearby: int, tops: dict = None, labs: int = 0) -> bool:
    details = [("Doctor", rx["doctor_name"]), ("Prescription", rx["reference"])]
    if rx["items"]:
        details += [("Medicines", ", ".join(i["name"] for i in rx["items"])),
                    ("Pharmacies notified", f"{nearby} within {rx['radius_km']:g} km of you")] + offer_lines(tops)
    if rx.get("tests"):
        details += [("Tests", ", ".join(t["name"] for t in rx["tests"])),
                    ("Labs notified", f"{labs} within {rx['radius_km']:g} km of you")]
    text, html = render(
        "Your prescription is ready",
        "We've sent it to verified pharmacies and labs near you. Compare the cheapest price, the best discount and the "
        "fastest delivery or report, then order or book.",
        details, "Prices update as pharmacies and labs respond.", button=("Compare prices", link("/account#prescriptions")))
    return mailer.send_email(patient["email"], f"Prescription from {rx['doctor_name']} – compare prices nearby ({rx['reference']})",
                      text, html)


def send_rx_cancelled(email: str, reference: str, doctor_name: str, for_pharmacy: bool) -> bool:
    intro = (f"Prescription {reference} was cancelled by {doctor_name}. Please don't prepare or quote for it."
             if for_pharmacy else f"{doctor_name} cancelled prescription {reference}. Please don't buy these medicines; "
                                  "contact your doctor if you have questions.")
    text, html = render("Prescription cancelled", intro, [("Prescription", reference)], "", "#b91c1c")
    return mailer.send_email(email, f"Prescription {reference} cancelled", text, html)


def send_rx_expiring(email: str, name: str, reference: str, valid_until: str) -> bool:
    text, html = render("Your prescription expires soon", f"Hi {name}, some medicines on {reference} haven't been ordered yet.",
                        [("Valid until", valid_until[:10])], "Order now to get today's best prices nearby.",
                        button=("Compare prices", link("/account#prescriptions")))
    return mailer.send_email(email, f"Prescription {reference} expires soon", text, html)


def send_refill_due(email: str, name: str, reference: str, ends_on: str, refills_left: int, medicines: list) -> bool:
    text, html = render("Time to refill your medicines", f"Hi {name}, your long-term medicines from {reference} run out soon.",
                        [("Runs out on", ends_on), ("Medicines", ", ".join(medicines)), ("Refills left", refills_left)],
                        "Your doctor pre-approved these refills. One tap sends them to pharmacies near you for prices.",
                        "#059669", button=("Refill now", link("/account#prescriptions")))
    return mailer.send_email(email, f"Refill due: {reference}", text, html)


def send_refill_issued(root: dict, rx: dict) -> int:
    """Confirm to the patient and let the doctor know (refills were pre-authorised by the doctor)."""
    items = ", ".join(i["name"] for i in rx["items"])
    sent = 0
    text, html = render(f"Refill {rx['refill_number']} sent to pharmacies", f"Hi {root['patient_name']},",
                        [("Prescription", rx["reference"]), ("Refill of", root["reference"]), ("Medicines", items)],
                        "Nearby pharmacies will send their prices shortly.", "#059669",
                        button=("Compare prices", link("/account#prescriptions")))
    sent += mailer.send_email(root["patient_email"], f"Refill {rx['reference']} created", text, html)
    text, html = render("A patient used a pre-approved refill", f"Hi {root['doctor_name']},",
                        [("Patient", root["patient_name"]), ("Original prescription", root["reference"]),
                         ("Refill", f"{rx['refill_number']} of {root['refills_allowed']} ({rx['reference']})"),
                         ("Medicines", items)],
                        "No action needed. You can cancel the refill from your portal if needed.", "#475569",
                        button=("Open portal", link("/doctor#prescriptions")))
    sent += mailer.send_email(root["doctor_email"], f"Refill used: {root['reference']}", text, html)
    return sent


# Imported last, so slices can depend on each other in any order
from features.pharmacy_offers.emails import offer_lines  # noqa: E402

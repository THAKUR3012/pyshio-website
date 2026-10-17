"""Labs: registration, test catalogue and prices, tests sent from prescriptions, bookings and reports.

This file: emails.
"""
from __future__ import annotations

from shared import mailer
from shared.mailer import link, money, render


# ---------- Labs ----------
def send_lab_application(lab: dict, admin_emails: list) -> int:
    text, html = render("Thanks for registering your lab", f"Hi {lab['name']}, {lab['lab_name']} has been submitted for verification.",
                        [("Registration no.", lab["registration_no"]), ("NABL", lab.get("nabl_no") or "-")],
                        "We'll email you once it's approved. Meanwhile you can add your test prices.",
                        button=("Open lab dashboard", link("/lab")))
    sent = mailer.send_email(lab["email"], f"{lab['lab_name']} is under review", text, html)
    text, html = render("New lab awaiting approval", "",
                        [("Lab", lab["lab_name"]), ("Registration no.", lab["registration_no"]), ("NABL", lab.get("nabl_no") or "-"),
                         ("Pathologist", f"{lab['pathologist_name']} ({lab['pathologist_reg_no']})"), ("City", lab["city"])],
                        "Verify the registration before approving.", button=("Review", link("/admin#labs")))
    for admin in admin_emails:
        sent += mailer.send_email(admin, f"New lab: {lab['lab_name']}", text, html)
    return sent


def send_lab_request(lab: dict, rx: dict, tests: list, distance_km: float) -> bool:
    text, html = render("New test request nearby", f"A patient {distance_km:.1f} km away needs these tests.",
                        [("Prescription", rx["reference"]), ("Tests", ", ".join(t["name"] for t in tests))],
                        "Send your price and report time; the patient sees the cheapest and fastest labs first.",
                        "#0891b2", button=("Send a quote", link("/lab")))
    return mailer.send_email(lab["email"], f"Test request {rx['reference']} – {len(tests)} test(s)", text, html)


def send_lab_booking(booking: dict, recipient: str, note: str = None) -> bool:
    labels = {"booked": "New test booking", "accepted": "Your lab booking is confirmed",
              "sample_collected": "Sample collected", "report_ready": "Your test report is ready",
              "completed": "Lab booking completed", "rejected": "The lab couldn't take your booking",
              "cancelled": "Lab booking cancelled"}
    headline = labels.get(booking["status"], "Lab booking update")
    if recipient == "lab":
        headline = {"booked": "New test booking", "cancelled": "Booking cancelled by the patient"}.get(booking["status"], headline)
    details = [("Booking", booking["reference"]), ("Lab", booking["lab_name"]),
               ("Tests", ", ".join(i["name"] for i in booking["items"])), ("Total", money(booking["total_paise"])),
               ("Collection", "Home collection" if booking["collection"] == "home" else "Visit the lab")]
    if booking.get("preferred_date"):
        details.append(("Preferred", f"{booking['preferred_date']} {booking.get('preferred_time') or ''}".strip()))
    if recipient == "lab":
        details += [("Patient", booking["patient_name"]), ("Phone", booking.get("patient_phone") or "-")]
        if booking["collection"] == "home":
            details.append(("Address", booking.get("address") or "-"))
    if note:
        details.append(("Note", note))
    text, html = render(headline, "", details, "Pay at the lab or to the collection agent.", "#0891b2",
                        button=("Open dashboard", link("/lab" if recipient == "lab" else "/account#labs")))
    to = booking["lab_email"] if recipient == "lab" else booking["patient_email"]
    return mailer.send_email(to, f"{headline} ({booking['reference']})", text, html)


def send_report_ready(email: str, name: str, booking_ref: str, lab_name: str, for_doctor: bool, patient_name: str = None) -> bool:
    intro = (f"{lab_name} uploaded the report for your patient {patient_name}." if for_doctor
             else f"Hi {name}, {lab_name} has uploaded your report.")
    text, html = render("Test report ready", intro, [("Booking", booking_ref)],
                        "Reports stay in Health records with your key results and trends.", "#059669",
                        button=("View report", link("/doctor#patients" if for_doctor else "/account#records")))
    return mailer.send_email(email, f"Test report ready ({booking_ref})", text, html)


def send_lab_reminder(booking: dict, minutes: int) -> bool:
    text, html = render("A test booking is waiting for you", f"Booking {booking['reference']} has waited over {minutes} minutes.",
                        [("Tests", ", ".join(i["name"] for i in booking["items"]))], "Please accept or reject it.",
                        "#b45309", button=("Open lab dashboard", link("/lab")))
    return mailer.send_email(booking["lab_email"], f"Reminder: booking {booking['reference']} needs a response", text, html)

"""Pharma insights: companies, divisions, MRs, brands, MR meetings and sales / demand insights.

This file: emails.
"""
from __future__ import annotations

from shared import mailer, settings
from shared.mailer import link, render


def send_company_application(name: str, email: str, company: dict, admin_emails: list) -> int:
    text, html = render("Thanks for registering your company", f"Hi {name}, {company['name']} has been submitted for verification.",
                        [("Company", company["name"]), ("CIN", company.get("cin") or "-")],
                        "We'll email you once it's approved. Meanwhile you can set up your divisions.",
                        button=("Open company dashboard", link("/company")))
    sent = mailer.send_email(email, f"{company['name']} is under review", text, html)
    text, html = render("New pharma company awaiting approval", "",
                        [("Company", company["name"]), ("CIN", company.get("cin") or "-"),
                         ("GSTIN", company.get("gstin") or "-"), ("City", company["city"]), ("Contact", f"{name} <{email}>")],
                        "Check the company's registration before approving.", button=("Review", link("/admin#companies")))
    for admin in admin_emails:
        sent += mailer.send_email(admin, f"New company: {company['name']}", text, html)
    return sent


def send_company_status(name: str, email: str, company_name: str, status: str, reason: str = None) -> bool:
    headline = {"approved": f"{company_name} is approved", "rejected": f"{company_name} wasn't approved",
                "suspended": f"{company_name} has been suspended"}[status]
    footer = ("You can now invite your medical representatives." if status == "approved"
              else "Reply to this email if you have questions.")
    text, html = render(headline, f"Hi {name},", [("Reason", reason)] if reason else [], footer,
                        "#059669" if status == "approved" else "#b91c1c", button=("Open dashboard", link("/company")))
    return mailer.send_email(email, f"{settings.PLATFORM_NAME}: {headline}", text, html)


def send_mr_invite(name: str, email: str, company_name: str, token: str) -> bool:
    url = link(f"/login#reset={token}") or f"/login#reset={token}"
    text, html = render(f"{company_name} added you on {settings.PLATFORM_NAME}",
                        f"Hi {name}, set a password to see doctors in your territory and request meetings.",
                        [("Link valid for", f"{settings.PASSWORD_RESET_HOURS} hours (use 'Forgot password' for a new one)")],
                        "If you weren't expecting this, ignore this email.", button=("Set my password", url))
    return mailer.send_email(email, f"Your {company_name} account on {settings.PLATFORM_NAME}", text, html, notify=False)


def send_mr_request_to_doctor(req: dict) -> bool:
    preferred = " ".join(x for x in (req.get("preferred_date"), req.get("preferred_time")) if x) or "Any time"
    text, html = render("New meeting request from a medical representative", f"Hi {req['doctor_name']},",
                        [("Representative", req["mr_name"]), ("Company", req["company_name"]),
                         ("Division", req.get("division_name") or "-"), ("Purpose", req["purpose"]),
                         ("Products", req.get("products") or "-"), ("Preferred", preferred)],
                        "Accept with a time that suits you, or decline. You can stop these requests in your profile.",
                        "#4f46e5", button=("Review request", link("/doctor#requests")))
    return mailer.send_email(req["doctor_email"], f"Meeting request: {req['company_name']}", text, html)


def send_mr_request_update(req: dict) -> bool:
    accepted = req["status"] == "accepted"
    details = [("Doctor", f"{req['doctor_name']}, {req['clinic_name']}"), ("Purpose", req["purpose"])]
    if accepted:
        details.append(("Meeting", req["scheduled_for"].replace("T", " ")))
    if req.get("doctor_note"):
        details.append(("Doctor's note", req["doctor_note"]))
    headline = "Meeting accepted" if accepted else "Meeting request declined"
    text, html = render(headline, f"Hi {req['mr_name']},", details, "Please be on time and follow your company's code "
                        "of conduct." if accepted else "You can send a new request later.",
                        "#059669" if accepted else "#b91c1c", button=("Open dashboard", link("/mr")))
    return mailer.send_email(req["mr_email"], f"{headline}: {req['doctor_name']}", text, html)

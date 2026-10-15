"""Patient emails: confirmation (with calendar invite), reschedule, cancellation, reminder.

SMTP is configured with environment variables. If SMTP_HOST is not set, emails are
skipped and logged, so the booking site keeps working without email.
"""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape
import logging
import os
import smtplib
import ssl

log = logging.getLogger("pyshio.mail")

CLINIC_NAME = os.environ.get("PYSHIO_CLINIC_NAME", "Pyshio Physiotherapy")
CLINIC_ADDRESS = os.environ.get("PYSHIO_CLINIC_ADDRESS", "123 Wellness Avenue, Suite 200, Downtown")
CLINIC_PHONE = os.environ.get("PYSHIO_CLINIC_PHONE", "(123) 456-7890")


def is_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST"))


def send_email(to: str, subject: str, text: str, html: str = None, ics: str = None,
               reply_to: str = None) -> bool:
    """Send one email. Returns False (never raises) so a mail problem can't break a booking."""
    if not is_configured():
        log.info("SMTP not configured, skipped email %r to %s", subject, to)
        return False

    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    security = os.environ.get("SMTP_SECURITY") or {465: "ssl", 587: "starttls"}.get(port, "none")
    sender = os.environ.get("SMTP_FROM") or user

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{CLINIC_NAME} <{sender}>"
    msg["To"] = to
    if reply_to or os.environ.get("SMTP_REPLY_TO"):
        msg["Reply-To"] = reply_to or os.environ["SMTP_REPLY_TO"]
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    if ics:
        msg.add_attachment(ics.encode(), maintype="text", subtype="calendar",
                           filename="appointment.ics", params={"method": "PUBLISH"})

    try:
        context = ssl.create_default_context()
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, context=context, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            if security == "starttls":
                server.starttls(context=context)
        with server:
            if user:
                server.login(user, password)
            server.send_message(msg)
        log.info("Sent email %r to %s", subject, to)
        return True
    except Exception:
        log.exception("Failed to send email %r to %s", subject, to)
        return False


# ---------- Templates ----------
def _when(appt: dict) -> str:
    start = datetime.strptime(f"{appt['appointment_date']} {appt['appointment_time']}", "%Y-%m-%d %H:%M")
    return start.strftime("%A, %d %B %Y at %I:%M %p").replace(" 0", " ")


def _ics(appt: dict) -> str:
    start = datetime.strptime(f"{appt['appointment_date']} {appt['appointment_time']}", "%Y-%m-%d %H:%M")
    end = start + timedelta(minutes=appt["duration"])
    fmt = "%Y%m%dT%H%M%S"

    def text(v):
        return str(v).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Pyshio//Booking//EN", "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{appt['id']}@pyshio",
        f"DTSTAMP:{datetime.now(timezone.utc).strftime(fmt)}Z",
        f"DTSTART:{start.strftime(fmt)}",  # clinic local time
        f"DTEND:{end.strftime(fmt)}",
        f"SUMMARY:{text(appt['service_name'] + ' – ' + CLINIC_NAME)}",
        f"LOCATION:{text(CLINIC_ADDRESS)}",
        f"DESCRIPTION:{text('With ' + appt['practitioner_name'] + '. Reference ' + appt['reference'])}",
        "END:VEVENT", "END:VCALENDAR", "",
    ])


HEADLINES = {
    "confirmed": ("Your appointment is confirmed", "Booking confirmed"),
    "rescheduled": ("Your appointment has been moved", "Appointment rescheduled"),
    "cancelled": ("Your appointment has been cancelled", "Appointment cancelled"),
    "reminder": ("Reminder: your appointment is tomorrow", "Appointment reminder"),
}


def build_appointment_email(kind: str, appt: dict, site_url: str = ""):
    """Return (subject, text, html, ics) for a patient email."""
    headline, subject_prefix = HEADLINES[kind]
    subject = f"{subject_prefix} – {_when(appt)} ({appt['reference']})"
    manage = f"{site_url.rstrip('/')}/#manage" if site_url else ""

    details = [
        ("When", _when(appt)),
        ("Service", f"{appt['service_name']} ({appt['duration']} min)"),
        ("Practitioner", appt["practitioner_name"]),
        ("Reference", appt["reference"]),
        ("Where", CLINIC_ADDRESS),
    ]
    if kind == "cancelled":
        footer = f"If this was a mistake, please book again or call us on {CLINIC_PHONE}."
    else:
        footer = (f"Need to change or cancel? Use your reference {appt['reference']} "
                  f"{'at ' + manage if manage else 'under “My Booking” on our website'}, "
                  f"or call {CLINIC_PHONE}.")

    text = "\n".join(
        [f"Hi {appt['patient_name']},", "", headline + ".", ""]
        + [f"{k}: {v}" for k, v in details]
        + ["", footer, "", CLINIC_NAME]
    )

    rows = "".join(
        f'<tr><td style="padding:4px 12px 4px 0;color:#64748b">{escape(k)}</td>'
        f'<td style="padding:4px 0;color:#0f172a;font-weight:600">{escape(v)}</td></tr>'
        for k, v in details
    )
    html = f"""<!doctype html><html><body style="margin:0;background:#f8fafc;font-family:Arial,sans-serif">
<div style="max-width:520px;margin:24px auto;background:#fff;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden">
  <div style="background:#0284c7;color:#fff;padding:16px 24px;font-size:18px;font-weight:bold">{escape(CLINIC_NAME)}</div>
  <div style="padding:24px">
    <p style="margin:0 0 8px;color:#0f172a">Hi {escape(appt['patient_name'])},</p>
    <p style="margin:0 0 16px;font-size:18px;font-weight:bold;color:#0f172a">{escape(headline)}</p>
    <table style="border-collapse:collapse;font-size:14px">{rows}</table>
    <p style="margin:20px 0 0;font-size:13px;color:#475569">{escape(footer)}</p>
  </div>
</div></body></html>"""

    ics = _ics(appt) if kind in ("confirmed", "rescheduled") else None
    return subject, text, html, ics


def send_appointment_email(kind: str, appt: dict) -> bool:
    subject, text, html, ics = build_appointment_email(kind, appt, os.environ.get("PYSHIO_SITE_URL", ""))
    return send_email(appt["email"], subject, text, html, ics)


# ---------- Staff (practitioner / front desk) notifications ----------
def practitioner_email(key: str) -> str:
    """e.g. dr-sarah -> PYSHIO_EMAIL_DR_SARAH"""
    return os.environ.get("PYSHIO_EMAIL_" + key.upper().replace("-", "_"), "").strip()


def staff_recipients(practitioners) -> list:
    """Practitioners involved in the change plus the front desk (PYSHIO_STAFF_EMAIL, comma-separated)."""
    found = [practitioner_email(p) for p in practitioners if p]
    found += os.environ.get("PYSHIO_STAFF_EMAIL", "").split(",")
    recipients = []
    for addr in (a.strip() for a in found):
        if addr and addr.lower() not in (r.lower() for r in recipients):
            recipients.append(addr)
    return recipients


STAFF_HEADLINES = {
    "confirmed": "New appointment booked",
    "rescheduled": "Appointment rescheduled",
    "cancelled": "Appointment cancelled",
    "note": "Appointment note updated",
}


def build_staff_email(kind: str, appt: dict, previous: dict = None, by: str = "patient",
                      message: str = None, site_url: str = ""):
    """Return (subject, text, html, ics) for a practitioner/front-desk email."""
    headline = STAFF_HEADLINES[kind]
    who = "the patient online" if by == "patient" else "clinic staff"
    subject = f"{headline}: {appt['patient_name']} – {_when(appt)} ({appt['reference']})"

    details = [("When", _when(appt))]
    if previous and (previous["appointment_date"], previous["appointment_time"]) != \
            (appt["appointment_date"], appt["appointment_time"]):
        details.append(("Was", _when(previous)))
    details.append(("Practitioner", appt["practitioner_name"]))
    if previous and previous["practitioner"] != appt["practitioner"]:
        details.append(("Previously with", previous["practitioner_name"]))
    details += [
        ("Service", f"{appt['service_name']} ({appt['duration']} min)"),
        ("Patient", appt["patient_name"]),
        ("Phone", appt["phone"]),
        ("Email", appt["email"]),
        ("Reference", appt["reference"]),
        ("Patient's reason", appt.get("reason") or "—"),
    ]
    if message:
        details.append(("Message from patient", message))
    if appt.get("notes"):
        details.append(("Clinic notes", appt["notes"]))
    admin_link = f"{site_url.rstrip('/')}/admin" if site_url else ""
    footer = f"{headline} by {who}." + (f" Open the dashboard: {admin_link}" if admin_link else "")
    footer += " Reply to this email to contact the patient."

    text = "\n".join([headline, ""] + [f"{k}: {v}" for k, v in details] + ["", footer])
    rows = "".join(
        f'<tr><td style="padding:4px 12px 4px 0;color:#64748b;vertical-align:top;white-space:nowrap">{escape(k)}</td>'
        f'<td style="padding:4px 0;color:#0f172a;font-weight:600;white-space:pre-wrap">{escape(str(v))}</td></tr>'
        for k, v in details
    )
    colour = {"cancelled": "#b91c1c", "rescheduled": "#b45309"}.get(kind, "#0284c7")
    html = f"""<!doctype html><html><body style="margin:0;background:#f8fafc;font-family:Arial,sans-serif">
<div style="max-width:560px;margin:24px auto;background:#fff;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden">
  <div style="background:{colour};color:#fff;padding:16px 24px;font-size:18px;font-weight:bold">{escape(headline)}</div>
  <div style="padding:24px">
    <table style="border-collapse:collapse;font-size:14px">{rows}</table>
    <p style="margin:20px 0 0;font-size:13px;color:#475569">{escape(footer)}</p>
  </div>
</div></body></html>"""
    ics = _ics(appt) if kind in ("confirmed", "rescheduled") else None
    return subject, text, html, ics


def send_staff_email(kind: str, appt: dict, previous: dict = None, by: str = "patient",
                     message: str = None) -> int:
    """Email the practitioner(s) and front desk. Returns how many emails were sent."""
    involved = [appt["practitioner"]] + ([previous["practitioner"]] if previous else [])
    recipients = staff_recipients(involved)
    if not recipients:
        log.info("No staff email configured for %s, skipped %s notice", appt["practitioner"], kind)
        return 0
    subject, text, html, ics = build_staff_email(kind, appt, previous, by, message,
                                                 os.environ.get("PYSHIO_SITE_URL", ""))
    return sum(send_email(to, subject, text, html, ics, reply_to=appt["email"]) for to in recipients)

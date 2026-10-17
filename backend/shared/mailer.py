"""All outgoing email: appointment notices to patients and doctors, and account emails.

SMTP is configured with environment variables. If SMTP_HOST is not set, emails are
skipped and logged, so the platform keeps working without email.
"""
import logging
import os
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape

from shared import settings



log = logging.getLogger("pyshio.mail")

# Called with (to, subject, text, html) for every email that may be mirrored in-app (see features/notifications)
LISTENERS = []


def is_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST"))


def send_email(to: str, subject: str, text: str, html: str = None, ics: str = None,
               reply_to: str = None, notify: bool = True) -> bool:
    """Send one email and mirror it into the recipient's in-app notifications (unless notify=False, used for emails
    carrying a secret link). Returns False (never raises) so a mail problem can't break a booking."""
    if notify:
        for listener in LISTENERS:
            listener(to, subject, text, html)
    return _deliver(to, subject, text, html, ics, reply_to)


def _deliver(to: str, subject: str, text: str, html: str = None, ics: str = None, reply_to: str = None) -> bool:
    """The SMTP part of send_email."""
    if not to or to.endswith(".invalid"):  # walk-in patients without email, deleted accounts
        return False
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
    msg["From"] = f"{settings.PLATFORM_NAME} <{sender}>"
    msg["To"] = to
    if reply_to:
        msg["Reply-To"] = reply_to
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


# ---------- Building blocks ----------
def rupees(amount) -> str:
    """₹ with Indian digit grouping, e.g. 125000 -> ₹1,25,000"""
    s = str(int(amount))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return "₹" + s


def when(appt: dict) -> str:
    start = datetime.strptime(f"{appt['appointment_date']} {appt['appointment_time']}", "%Y-%m-%d %H:%M")
    return start.strftime("%A, %d %B %Y at %I:%M %p").replace(" 0", " ")


class Link(str):
    """An absolute URL (empty when SITE_URL isn't set) that remembers its site path for in-app notifications."""
    def __new__(cls, url: str, path: str):
        obj = super().__new__(cls, url)
        obj.path = path
        return obj


def link(path: str) -> str:
    return Link(f"{settings.SITE_URL}{path}" if settings.SITE_URL else "", path)


def ics_for(appt: dict) -> str:
    start = datetime.strptime(f"{appt['appointment_date']} {appt['appointment_time']}", "%Y-%m-%d %H:%M")
    end = start + timedelta(minutes=appt["duration"])
    fmt = "%Y%m%dT%H%M%S"
    where = {"video": "Video consultation (join from your Pyshio appointments)",
             "home": appt.get("visit_address") or "Home visit"}.get(appt.get("visit_type"),
                                                                    f"{appt['clinic_name']}, {appt['address']}, {appt['city']}")

    def text(v):
        return str(v).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Pyshio//Booking//EN", "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{appt['id']}@pyshio",
        f"DTSTAMP:{datetime.now(timezone.utc).strftime(fmt)}Z",
        f"DTSTART:{start.strftime(fmt)}",  # platform local time
        f"DTEND:{end.strftime(fmt)}",
        f"SUMMARY:{text(appt['service_name'] + ' – ' + appt['doctor_name'])}",
        f"LOCATION:{text(where)}",
        f"DESCRIPTION:{text('Patient: ' + appt['patient_name'] + '. Reference ' + appt['reference'])}",
        "END:VEVENT", "END:VCALENDAR", "",
    ])


def render(headline: str, intro: str, details: list, footer: str, colour: str = "#0284c7",
           button: tuple = None):
    """Return (text, html) for a simple branded email."""
    text_lines = [headline, ""]
    if intro:
        text_lines += [intro, ""]
    text_lines += [f"{k}: {v}" for k, v in details]
    if button and button[1]:
        text_lines += ["", f"{button[0]}: {button[1]}"]
    text_lines += ["", footer, "", settings.PLATFORM_NAME]

    rows = "".join(
        f'<tr><td style="padding:4px 12px 4px 0;color:#64748b;vertical-align:top;white-space:nowrap">{escape(k)}</td>'
        f'<td style="padding:4px 0;color:#0f172a;font-weight:600;white-space:pre-wrap">{escape(str(v))}</td></tr>'
        for k, v in details
    )
    btn = ""
    if button and button[1]:
        btn = (f'<p style="margin:20px 0 0"><a href="{escape(button[1])}" style="background:{colour};color:#fff;'
               f'padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:bold">{escape(button[0])}</a></p>')
    path = getattr(button[1], "path", None) if button else None
    mark = f"<!--pyshio-link:{escape(path)}-->" if path and "reset=" not in path else ""
    html = f"""<!doctype html><html><body style="margin:0;background:#f8fafc;font-family:Arial,sans-serif">
<div style="max-width:560px;margin:24px auto;background:#fff;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden">
  <div style="background:{colour};color:#fff;padding:16px 24px;font-size:18px;font-weight:bold">{escape(headline)}</div>
  <div style="padding:24px">
    {f'<p style="margin:0 0 16px;color:#0f172a;white-space:pre-wrap">{escape(intro)}</p>' if intro else ''}
    <table style="border-collapse:collapse;font-size:14px">{rows}</table>
    {btn}{mark}
    <p style="margin:20px 0 0;font-size:13px;color:#475569">{escape(footer)}</p>
  </div>
  <div style="padding:12px 24px;background:#f1f5f9;color:#64748b;font-size:12px">{escape(settings.PLATFORM_NAME)}</div>
</div></body></html>"""
    return "\n".join(text_lines), html


# ---------- Medicine network emails ----------
def money(paise: int) -> str:
    """Paise -> ₹ with Indian grouping, e.g. 123450 -> ₹1,234.50 ; 80000 -> ₹800"""
    paise = int(paise or 0)
    whole = rupees(paise // 100)
    return whole if paise % 100 == 0 else f"{whole}.{paise % 100:02d}"


def send_partner_status(name: str, email: str, status: str, reason: str = None, dashboard: str = "/doctor",
                        label: str = "profile") -> bool:
    """Approval / rejection / suspension email for doctors and pharmacies."""
    if status == "approved":
        headline, footer = f"Your {label} is live", "Patients nearby can now find you."
    elif status == "rejected":
        headline, footer = f"Your {label} wasn't approved", "Reply to this email if you have questions."
    else:
        headline, footer = f"Your {label} has been suspended", "Reply to this email if you have questions."
    text, html = render(headline, f"Hi {name},", [("Reason", reason)] if reason else [], footer,
                        "#059669" if status == "approved" else "#b91c1c",
                        button=("Open dashboard", link(dashboard)))
    return send_email(email, f"{settings.PLATFORM_NAME}: {headline}", text, html)

"""MR next-best-action and company alerts: emails."""
from __future__ import annotations

from shared import mailer
from shared.mailer import link, render


def send_company_alerts(email: str, name: str, company: str, alerts: list) -> bool:
    text, html = render(f"{len(alerts)} unusual change{'s' if len(alerts) > 1 else ''} in your products' prescribing",
                        f"Hi {name}, this week's check of {company}'s products on Pyshio found:",
                        [(a["product"], a["message"]) for a in alerts[:10]],
                        "Counts are prescriptions on Pyshio only; small groups are never shown.", "#7c3aed",
                        button=("Open insights", link("/company#insights")))
    return mailer.send_email(email, f"Prescribing alerts for {company}", text, html)

"""Pyshio is organised as vertical slices: one folder per feature, each owning its whole stack.

    features/<slice>/
        api.py      HTTP endpoints + request models   (exposes `router`)
        logic.py    the slice's logic and queries      (what other slices may call)
        emails.py   the slice's emails                 (optional)
        jobs.py     scheduled work, run by jobs.py      (optional)
        static/     the slice's browser script(s), served at /features/<slice>/...   (optional)

Shared infrastructure (database, login, settings, email sending, validators) lives in shared/.
SLICES is the order routes are registered in.
"""
from importlib import import_module
from pathlib import Path

HERE = Path(__file__).resolve().parent

SLICES = [
    "pages", "pwa",
    "accounts", "health_profile", "family", "doctors", "triage", "appointments", "home_visits", "video_consults",
    "online_payments", "patient_history",
    "pharmacies", "medicines", "rx_safety", "ai_scribe", "prescriptions", "follow_ups", "pharmacy_offers", "medicine_orders",
    "medicine_courses", "medicine_reminders", "risk_nudges", "settlements", "reviews",
    "labs", "health_records", "lab_reader", "prescription_uploads", "chats",
    "pharma_insights", "pharma_nba", "admin_analytics", "admin",
    "notifications", "assistant",
]


def existing():
    return [s for s in SLICES if (HERE / s).is_dir()]


def load_all():
    """Import every slice's modules (so hooks such as notifications register themselves). Returns the api modules."""
    apis = []
    for s in existing():
        for part in ("logic", "emails", "jobs", "api"):
            if (HERE / s / f"{part}.py").exists():
                module = import_module(f"features.{s}.{part}")
                if part == "api":
                    apis.append((s, module))
    return apis


def static_dirs():
    return [(s, HERE / s / "static") for s in existing() if (HERE / s / "static").is_dir()]

"""Input cleaning shared by the request models."""
import re
from typing import Optional

from shared import reference_data


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[0-9+()\-.\s]+$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PINCODE_RE = re.compile(r"^\d{6}$")


def name(v: str) -> str:
    v = " ".join((v or "").split())
    if not 2 <= len(v) <= 100:
        raise ValueError("Please enter a full name (2-100 characters)")
    return v


def email(v: str) -> str:
    v = (v or "").strip().lower()
    if len(v) > 254 or not EMAIL_RE.match(v):
        raise ValueError("Please enter a valid email address")
    return v


def phone(v: str) -> str:
    v = (v or "").strip()
    digits = re.sub(r"\D", "", v)
    if not PHONE_RE.match(v) or not 10 <= len(digits) <= 13:
        raise ValueError("Please enter a valid mobile number")
    return v


def password(v: str) -> str:
    if len(v or "") < 8:
        raise ValueError("Password must be at least 8 characters")
    if len(v) > 128:
        raise ValueError("Password is too long")
    return v


def optional_text(v: Optional[str]) -> Optional[str]:
    v = (v or "").strip()
    return v or None


def required_text(label: str, max_len: int = 200):
    def check(v: str) -> str:
        v = " ".join((v or "").split())
        if not v:
            raise ValueError(f"{label} is required")
        if len(v) > max_len:
            raise ValueError(f"{label} is too long")
        return v
    return check


def hhmm(v: str) -> str:
    v = (v or "").strip()
    if not TIME_RE.match(v):
        raise ValueError("Times must be HH:MM (24-hour)")
    return v


def iso_date(v: str) -> str:
    v = (v or "").strip()
    if not DATE_RE.match(v):
        raise ValueError("Dates must be YYYY-MM-DD")
    return v


def pincode(v: Optional[str]) -> Optional[str]:
    v = (v or "").strip()
    if v and not PINCODE_RE.match(v):
        raise ValueError("PIN code must be 6 digits")
    return v or None


def state(v: Optional[str]) -> Optional[str]:
    """An Indian state / union territory, spelled the standard way."""
    v = (v or "").strip()
    if not v:
        return None
    found = reference_data.normalise_state(v)
    if not found:
        raise ValueError("Choose a state from the list")
    return found


def birth_date(v: Optional[str]) -> Optional[str]:
    """Optional date of birth: a real past date, at most 120 years ago."""
    from datetime import datetime
    from shared import settings
    v = optional_text(v)
    if v is None:
        return None
    try:
        d = datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("Enter a valid date of birth")
    today = settings.now().date()
    if d > today or d.year < today.year - 120:
        raise ValueError("Enter a valid date of birth")
    return v


def short_list_text(label: str, max_len: int = 500):
    """Free text such as 'Penicillin, peanuts' (optional, trimmed, length-checked)."""
    def check(v: Optional[str]) -> Optional[str]:
        v = " ".join((v or "").split())
        if len(v) > max_len:
            raise ValueError(f"{label} is too long")
        return v or None
    return check

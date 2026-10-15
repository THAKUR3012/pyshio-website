"""Platform settings, read once from environment variables / .env."""
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from shared.envfile import load_env


BASE_DIR = Path(__file__).resolve().parent.parent  # project root (this file is shared/settings.py)
WEB_DIR = BASE_DIR / "web"  # HTML pages and shared browser assets
load_env(os.environ.get("PYSHIO_ENV_FILE", BASE_DIR / ".env"))

PLATFORM_NAME = os.environ.get("PYSHIO_PLATFORM_NAME", "Pyshio")
SITE_URL = os.environ.get("PYSHIO_SITE_URL", "").rstrip("/")
DB_PATH = os.environ.get("PYSHIO_DB", str(BASE_DIR / "pyshio.db"))

# Hosting servers (e.g. GoDaddy) usually run on US time; all scheduling uses this zone.
TIMEZONE = ZoneInfo(os.environ.get("PYSHIO_TIMEZONE", "Asia/Kolkata"))

# First admin account, created at startup if it doesn't exist yet
ADMIN_EMAIL = os.environ.get("PYSHIO_ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.environ.get("PYSHIO_ADMIN_PASSWORD", "")

SESSION_DAYS = 30
MAX_ADVANCE_DAYS = 90
PASSWORD_RESET_HOURS = 2

# Online payments (Razorpay). Online payment is offered only when both keys are set.
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
PAYMENT_HOLD_MINUTES = 15          # slot is held this long while the patient pays
REFUND_CUTOFF_HOURS = int(os.environ.get("PYSHIO_REFUND_CUTOFF_HOURS", "24"))

DOCTOR_CATEGORIES = {
    "physiotherapist": {"label": "Physiotherapist", "specialities": [
        "Orthopaedic", "Sports injury", "Neurological", "Paediatric", "Women's health",
        "Geriatric", "Cardio-respiratory", "Post-surgical rehab", "Pain management"]},
    "general_physician": {"label": "General physician", "specialities": [
        "Family medicine", "Diabetes", "Hypertension", "Fever & infections"]},
    "specialist": {"label": "Specialist doctor", "specialities": [
        "Orthopaedics", "Cardiology", "Dermatology", "Paediatrics", "Gynaecology", "ENT",
        "Neurology", "Psychiatry", "Pulmonology", "Gastroenterology"]},
    "dentist": {"label": "Dentist", "specialities": [
        "General dentistry", "Orthodontics", "Root canal", "Oral surgery"]},
    "ayush": {"label": "AYUSH (Ayurveda, Homoeopathy, Unani)", "specialities": [
        "Ayurveda", "Homoeopathy", "Unani", "Siddha"]},
}
SPECIALITIES = [s for c in DOCTOR_CATEGORIES.values() for s in c["specialities"]]

# Which doctor categories may write medicine prescriptions. Default: all (business decision).
# Indian law limits allopathic prescribing to registered medical practitioners; restrict with e.g.
# PYSHIO_PRESCRIBING_CATEGORIES=general_physician,specialist,dentist
PRESCRIBING_CATEGORIES = [c.strip() for c in os.environ.get(
    "PYSHIO_PRESCRIBING_CATEGORIES", ",".join(DOCTOR_CATEGORIES)).split(",") if c.strip()]

# The "nearby" network: doctors, pharmacies and prescriptions are matched within this distance
NEARBY_RADIUS_KM = float(os.environ.get("PYSHIO_RADIUS_KM", "3"))
WIDER_RADII_KM = [5, 10]           # patients may widen the search themselves
RX_VALID_DAYS = 30
QUOTE_VALID_HOURS = 24
# Pharmacies must accept a placed order in time: reminded after REMIND minutes, auto-rejected after ACCEPT minutes
ORDER_REMIND_MINUTES = int(os.environ.get("PYSHIO_ORDER_REMIND_MINUTES", "10"))
ORDER_ACCEPT_MINUTES = int(os.environ.get("PYSHIO_ORDER_ACCEPT_MINUTES", "30"))
RX_EXPIRY_REMIND_DAYS = 3
SUPPORT_EMAIL = os.environ.get("PYSHIO_SUPPORT_EMAIL", "")
SUPPORT_PHONE = os.environ.get("PYSHIO_SUPPORT_PHONE", "")
# Platform fee kept from online payments before settling with doctors/pharmacies
COMMISSION_PERCENT = float(os.environ.get("PYSHIO_COMMISSION_PERCENT", "0"))

# Long-term medicines: a doctor may pre-authorise refills, usable for this many days after prescribing
LONG_TERM_RX_DAYS = 180
MAX_REFILLS = 5
REFILL_REMIND_DAYS = 3             # email the patient this many days before a long-term course runs out
# Insights & analytics never show a group smaller than this (shown as "<5"), so nobody can be singled out
MIN_GROUP_SIZE = 5
# An MR may send a doctor at most this many meeting requests in 30 days (and only one open at a time)
MR_REQUESTS_PER_MONTH = 3


def now() -> datetime:
    """Current wall-clock time on the platform (naive, matching stored dates/times)."""
    return datetime.now(TIMEZONE).replace(tzinfo=None)


def online_payments_enabled() -> bool:
    return bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)

# Labs & health records
UPLOAD_DIR = os.environ.get("PYSHIO_UPLOAD_DIR", str(BASE_DIR / "uploads"))  # private: served only through the API
MAX_UPLOAD_MB = 10
LAB_REMIND_MINUTES = 60            # remind a lab about a booking it hasn't accepted yet
# Anonymous doctors in pharma insights: a category in a city needs at least this many doctors to be listed
# one by one; otherwise they are pooled so nobody can be singled out
MIN_ANON_DOCTORS = 3

# Prescriptions patients upload themselves stay open for quotes this long
UPLOAD_VALID_DAYS = 7

# Sign-ups allowed per network (IP) per hour
REGISTRATIONS_PER_HOUR = int(os.environ.get("PYSHIO_REGISTRATIONS_PER_HOUR", "10"))

# Video consults open in a private Jitsi Meet room (only a random room name is shared with the video service)
VIDEO_BASE_URL = os.environ.get("PYSHIO_VIDEO_BASE_URL", "https://meet.jit.si").rstrip("/")
VIDEO_JOIN_EARLY_MINUTES = 10
VIDEO_JOIN_LATE_MINUTES = 30
# Medicine reminders: default dose times (patients can change theirs)
DOSE_TIMES = {"morning": "08:00", "afternoon": "14:00", "evening": "18:00", "night": "21:00"}
# Follow-up reminder this many days before the date the doctor set
FOLLOW_UP_REMIND_DAYS = 2
# Read notifications are deleted after this many days
NOTIFICATION_KEEP_DAYS = 90

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from pathlib import Path
import csv
import hashlib
import hmac
import io
import math
import os
import re
import secrets
import sqlite3
import time
import uuid
from zoneinfo import ZoneInfo

from envfile import load_env

load_env(os.environ.get("PYSHIO_ENV_FILE", Path(__file__).resolve().parent / ".env"))

import mailer  # noqa: E402  (reads settings loaded above)

app = FastAPI(title="Pyshio Appointment API")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("PYSHIO_DB", str(BASE_DIR / "pyshio.db"))
ADMIN_PASSWORD = os.environ.get("PYSHIO_ADMIN_PASSWORD", "admin123")
TOKEN_TTL_SECONDS = 12 * 60 * 60
# Hosting servers (e.g. GoDaddy) usually run on US time, so all "today" / "in the past"
# checks use the clinic's own timezone.
CLINIC_TZ = ZoneInfo(os.environ.get("PYSHIO_TIMEZONE", "Asia/Kolkata"))


def clinic_now() -> datetime:
    """Current wall-clock time at the clinic (naive, to match stored dates/times)."""
    return datetime.now(CLINIC_TZ).replace(tzinfo=None)


def load_token_secret() -> str:
    """Shared by every worker process; shared hosts run several, so a per-process random key breaks logins."""
    if os.environ.get("PYSHIO_SECRET"):
        return os.environ["PYSHIO_SECRET"]
    path = Path(DB_PATH).with_name(".pyshio_secret")
    try:
        with open(path, "x") as f:
            f.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    return path.read_text().strip()


TOKEN_SECRET = load_token_secret()

# ---------- Clinic configuration ----------
SERVICES = {
    "initial":  {"name": "Initial Assessment",      "duration": 60},
    "followup": {"name": "Follow-up Session",       "duration": 30},
    "sports":   {"name": "Sports Injury Treatment", "duration": 45},
    "manual":   {"name": "Manual Therapy",          "duration": 45},
    "rehab":    {"name": "Post-Surgery Rehab",      "duration": 45},
}

PRACTITIONERS = {
    "dr-sarah": "Dr. Sarah Chen",
    "dr-james": "James Patel, PT",
    "dr-emily": "Emily Rodriguez, PT",
}

SLOT_MINUTES = 30
TIME_SLOTS = [
    "08:00", "08:30", "09:00", "09:30", "10:00", "10:30",
    "11:00", "11:30", "13:00", "13:30", "14:00", "14:30",
    "15:00", "15:30", "16:00", "16:30", "17:00", "17:30"
]

# Weekday (Mon=0) -> time by which the last appointment must finish. Missing day = closed.
CLOSING_TIME = {0: "18:00", 1: "18:00", 2: "18:00", 3: "18:00", 4: "17:00", 5: "13:00"}
MAX_ADVANCE_DAYS = 90

STATUSES = ("confirmed", "completed", "no_show", "cancelled")
REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[0-9+()\-.\s]+$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")


# ---------- Database ----------
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_txn(conn):
    """Serialise writers so two requests can never book the same slot."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def new_reference(conn) -> str:
    while True:
        ref = "PY-" + "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(6))
        if not conn.execute("SELECT 1 FROM appointments WHERE reference = ?", (ref,)).fetchone():
            return ref


def service_key(value: Optional[str]) -> Optional[str]:
    """Accept a service key or a legacy display label like 'Initial Assessment (60 min)'."""
    if not value:
        return None
    value = value.strip()
    if value in SERVICES:
        return value
    for key, svc in SERVICES.items():
        if value.lower().startswith(svc["name"].lower()):
            return key
    return None


def practitioner_key(value: Optional[str]) -> Optional[str]:
    """Return a practitioner key, 'any', or None if unrecognised."""
    if value is None or value.strip().lower() in ("", "any", "any available"):
        return "any"
    value = value.strip()
    if value in PRACTITIONERS:
        return value
    for key, name in PRACTITIONERS.items():
        if value.lower() == name.lower():
            return key
    return None


def init_db():
    with get_db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS appointments (
                id TEXT PRIMARY KEY,
                patient_name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT NOT NULL,
                service TEXT NOT NULL,
                practitioner TEXT,
                reason TEXT,
                appointment_date TEXT NOT NULL,
                appointment_time TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT DEFAULT 'confirmed'
            )
        """)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(appointments)")}
        for col in ("reference", "notes", "updated_at", "reminder_sent_at"):
            if col not in cols:
                conn.execute(f"ALTER TABLE appointments ADD COLUMN {col} TEXT")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS blocked_times (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                block_date TEXT NOT NULL,
                start_time TEXT,
                end_time TEXT,
                practitioner TEXT,
                reason TEXT,
                created_at TEXT NOT NULL
            )
        """)

        # Normalise rows written by the original version (labels instead of keys,
        # "Any available" never assigned to a real practitioner, no reference).
        with write_txn(conn):
            rows = conn.execute("SELECT * FROM appointments ORDER BY created_at").fetchall()
            for r in rows:
                svc = service_key(r["service"]) or r["service"]
                prac = practitioner_key(r["practitioner"])
                if prac == "any" or prac is None:
                    free = free_practitioners_at(conn, r["appointment_date"], r["appointment_time"], svc, exclude_id=r["id"])
                    prac = free[0] if free else next(iter(PRACTITIONERS))
                ref = r["reference"] or new_reference(conn)
                if (svc, prac, ref) != (r["service"], r["practitioner"], r["reference"]):
                    conn.execute(
                        "UPDATE appointments SET service = ?, practitioner = ?, reference = ? WHERE id = ?",
                        (svc, prac, ref, r["id"])
                    )
            conn.execute("UPDATE appointments SET status = 'confirmed' WHERE status IS NULL")

        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_appt_reference ON appointments(reference)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_appt_date ON appointments(appointment_date, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_block_date ON blocked_times(block_date)")


# ---------- Scheduling helpers ----------
def to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def service_duration(key: str) -> int:
    return SERVICES.get(key, {}).get("duration", SLOT_MINUTES)


def slot_span(start: str, duration: int) -> Optional[List[str]]:
    """Consecutive grid slots an appointment occupies, or None if it would run past a break."""
    if start not in TIME_SLOTS:
        return None
    needed = max(1, math.ceil(duration / SLOT_MINUTES))
    idx = TIME_SLOTS.index(start)
    span = TIME_SLOTS[idx:idx + needed]
    if len(span) < needed:
        return None
    for a, b in zip(span, span[1:]):
        if to_minutes(b) - to_minutes(a) != SLOT_MINUTES:
            return None
    return span


def day_slots(d: date) -> List[str]:
    close = CLOSING_TIME.get(d.weekday())
    if close is None:
        return []
    return [t for t in TIME_SLOTS if to_minutes(t) + SLOT_MINUTES <= to_minutes(close)]


def occupied_by_practitioner(conn, date_str: str, exclude_id: Optional[str] = None) -> dict:
    occupied = {p: set() for p in PRACTITIONERS}
    rows = conn.execute(
        "SELECT id, service, practitioner, appointment_time FROM appointments "
        "WHERE appointment_date = ? AND status != 'cancelled'",
        (date_str,)
    ).fetchall()
    for r in rows:
        if r["id"] == exclude_id or r["practitioner"] not in occupied:
            continue
        span = slot_span(r["appointment_time"], service_duration(r["service"])) or [r["appointment_time"]]
        occupied[r["practitioner"]].update(span)

    blocks = conn.execute(
        "SELECT practitioner, start_time, end_time FROM blocked_times WHERE block_date = ?",
        (date_str,)
    ).fetchall()
    for b in blocks:
        targets = [b["practitioner"]] if b["practitioner"] in occupied else list(PRACTITIONERS)
        start = to_minutes(b["start_time"]) if b["start_time"] else 0
        end = to_minutes(b["end_time"]) if b["end_time"] else 24 * 60
        for t in TIME_SLOTS:
            if to_minutes(t) < end and to_minutes(t) + SLOT_MINUTES > start:
                for p in targets:
                    occupied[p].add(t)
    return occupied


def free_practitioners_at(conn, date_str: str, start: str, service: str,
                          practitioner: str = "any", exclude_id: Optional[str] = None,
                          occupied: Optional[dict] = None) -> List[str]:
    span = slot_span(start, service_duration(service))
    if not span:
        return []
    occupied = occupied if occupied is not None else occupied_by_practitioner(conn, date_str, exclude_id)
    candidates = list(PRACTITIONERS) if practitioner == "any" else [practitioner]
    return [p for p in candidates if not occupied[p] & set(span)]


def available_slots(conn, d: date, service: str, practitioner: str = "any",
                    exclude_id: Optional[str] = None) -> dict:
    """Map of start time -> practitioners free for the whole appointment."""
    open_slots = day_slots(d)
    if not open_slots:
        return {}
    date_str = d.isoformat()
    occupied = occupied_by_practitioner(conn, date_str, exclude_id)
    now = clinic_now()
    open_set = set(open_slots)
    result = {}
    for start in open_slots:
        if d == now.date() and to_minutes(start) <= now.hour * 60 + now.minute:
            continue
        span = slot_span(start, service_duration(service))
        if not span or not set(span) <= open_set:
            continue
        free = free_practitioners_at(conn, date_str, start, service, practitioner, occupied=occupied)
        if free:
            result[start] = free
    return result


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")


def check_bookable_date(d: date):
    today = clinic_now().date()
    if d < today:
        raise HTTPException(status_code=400, detail="Cannot book in the past")
    if d > today + timedelta(days=MAX_ADVANCE_DAYS):
        raise HTTPException(status_code=400, detail=f"Bookings open up to {MAX_ADVANCE_DAYS} days ahead")
    if not day_slots(d):
        raise HTTPException(status_code=400, detail="The clinic is closed on this day")


def assign_practitioner(conn, date_str: str, time_str: str, service: str,
                        practitioner: str, exclude_id: Optional[str] = None) -> str:
    """Validate a requested slot and return the practitioner who will take it."""
    d = parse_date(date_str)
    check_bookable_date(d)
    if time_str not in TIME_SLOTS:
        raise HTTPException(status_code=400, detail="Invalid time slot")
    avail = available_slots(conn, d, service, practitioner, exclude_id)
    if time_str not in avail:
        raise HTTPException(status_code=409, detail="This time slot is no longer available. Please choose another.")
    free = avail[time_str]
    if len(free) == 1:
        return free[0]
    # "Any available": give it to whoever has the lightest day
    occupied = occupied_by_practitioner(conn, date_str, exclude_id)
    return min(free, key=lambda p: len(occupied[p]))


def appointment_start(row) -> datetime:
    return datetime.strptime(f"{row['appointment_date']} {row['appointment_time']}", "%Y-%m-%d %H:%M")


# ---------- Models ----------
def _clean_name(v: str) -> str:
    v = " ".join((v or "").split())
    if not 2 <= len(v) <= 100:
        raise ValueError("Please enter your full name (2-100 characters)")
    return v


def _clean_email(v: str) -> str:
    v = (v or "").strip().lower()
    if len(v) > 254 or not EMAIL_RE.match(v):
        raise ValueError("Please enter a valid email address")
    return v


def _clean_phone(v: str) -> str:
    v = (v or "").strip()
    digits = re.sub(r"\D", "", v)
    if not PHONE_RE.match(v) or not 7 <= len(digits) <= 15:
        raise ValueError("Please enter a valid phone number")
    return v


def _clean_service(v: str) -> str:
    key = service_key(v)
    if not key:
        raise ValueError("Unknown service")
    return key


def _clean_practitioner(v: Optional[str]) -> str:
    key = practitioner_key(v)
    if not key:
        raise ValueError("Unknown practitioner")
    return key


def _clean_optional_text(v: Optional[str]) -> Optional[str]:
    v = (v or "").strip()
    return v or None


class BookingRequest(BaseModel):
    patient_name: str
    email: str
    phone: str
    service: str
    practitioner: Optional[str] = "any"
    reason: Optional[str] = Field(default=None, max_length=500)
    appointment_date: str   # YYYY-MM-DD
    appointment_time: str   # HH:MM

    _name = field_validator("patient_name")(_clean_name)
    _email = field_validator("email")(_clean_email)
    _phone = field_validator("phone")(_clean_phone)
    _service = field_validator("service")(_clean_service)
    _practitioner = field_validator("practitioner", mode="before")(_clean_practitioner)
    _reason = field_validator("reason")(_clean_optional_text)


class BookingLookup(BaseModel):
    reference: str
    email: str

    @field_validator("reference")
    @classmethod
    def _ref(cls, v: str) -> str:
        return (v or "").strip().upper()

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return (v or "").strip().lower()


class CancelRequest(BookingLookup):
    message: Optional[str] = Field(default=None, max_length=500)  # optional note for the physio

    _message = field_validator("message")(_clean_optional_text)


class RescheduleRequest(CancelRequest):
    appointment_date: str
    appointment_time: str


class AppointmentUpdate(BaseModel):
    status: Optional[Literal["confirmed", "completed", "no_show", "cancelled"]] = None
    notes: Optional[str] = Field(default=None, max_length=2000)
    appointment_date: Optional[str] = None
    appointment_time: Optional[str] = None
    practitioner: Optional[str] = None
    service: Optional[str] = None
    patient_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

    @field_validator("patient_name")
    @classmethod
    def _name(cls, v):
        return None if v is None else _clean_name(v)

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        return None if v is None else _clean_email(v)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v):
        return None if v is None else _clean_phone(v)

    @field_validator("service")
    @classmethod
    def _service(cls, v):
        return None if v is None else _clean_service(v)

    @field_validator("practitioner")
    @classmethod
    def _practitioner(cls, v):
        return None if v is None else _clean_practitioner(v)


class LoginRequest(BaseModel):
    password: str


class BlockRequest(BaseModel):
    block_date: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    practitioner: Optional[str] = None
    reason: Optional[str] = Field(default=None, max_length=200)

    @field_validator("start_time", "end_time")
    @classmethod
    def _time(cls, v):
        v = _clean_optional_text(v)
        if v is not None and not TIME_RE.match(v):
            raise ValueError("Times must be HH:MM")
        return v

    @field_validator("practitioner")
    @classmethod
    def _practitioner(cls, v):
        key = practitioner_key(v)
        if key is None:
            raise ValueError("Unknown practitioner")
        return None if key == "any" else key


# ---------- Serialisation ----------
def public_view(row) -> dict:
    return {
        "id": row["id"],
        "reference": row["reference"],
        "patient_name": row["patient_name"],
        "email": row["email"],
        "phone": row["phone"],
        "service": row["service"],
        "service_name": SERVICES.get(row["service"], {}).get("name", row["service"]),
        "duration": service_duration(row["service"]),
        "practitioner": row["practitioner"],
        "practitioner_name": PRACTITIONERS.get(row["practitioner"], row["practitioner"]),
        "reason": row["reason"],
        "appointment_date": row["appointment_date"],
        "appointment_time": row["appointment_time"],
        "created_at": row["created_at"],
        "status": row["status"],
    }


def admin_view(row) -> dict:
    return {**public_view(row), "notes": row["notes"], "updated_at": row["updated_at"]}


def get_row(conn, appt_id: str):
    row = conn.execute("SELECT * FROM appointments WHERE id = ?", (appt_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return row


def find_patient_booking(conn, lookup: BookingLookup):
    row = conn.execute(
        "SELECT * FROM appointments WHERE reference = ? AND lower(email) = ?",
        (lookup.reference, lookup.email)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No booking found for that reference and email")
    return row


def require_upcoming(row):
    if row["status"] != "confirmed":
        raise HTTPException(status_code=400, detail=f"This appointment is already {row['status'].replace('_', ' ')}")
    if appointment_start(row) <= clinic_now():
        raise HTTPException(status_code=400, detail="This appointment has already started. Please call the clinic.")


# ---------- Admin auth ----------
def _sign(payload: str) -> str:
    return hmac.new(TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def require_admin(authorization: Optional[str] = Header(default=None)):
    token = (authorization or "").removeprefix("Bearer ").strip()
    expiry, _, sig = token.partition(".")
    if not expiry.isdigit() or not hmac.compare_digest(sig, _sign(expiry)) or int(expiry) < time.time():
        raise HTTPException(status_code=401, detail="Admin login required")
    return True


# ---------- Errors ----------
@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    messages = []
    for err in exc.errors():
        msg = err.get("msg", "Invalid value").removeprefix("Value error, ")
        field = err["loc"][-1] if err.get("loc") else ""
        messages.append(msg if err.get("type") == "value_error" else f"{field}: {msg}")
    return JSONResponse(status_code=422, content={"detail": "; ".join(messages)})


# ---------- Public API ----------
@app.get("/api/config")
def get_config():
    return {
        "services": [{"key": k, **v} for k, v in SERVICES.items()],
        "practitioners": [{"key": k, "name": v} for k, v in PRACTITIONERS.items()],
        "time_slots": TIME_SLOTS,
        "statuses": list(STATUSES),
        "max_advance_days": MAX_ADVANCE_DAYS,
    }


@app.get("/api/slots")
def get_available_slots(
    date_str: str = Query(..., alias="date", description="YYYY-MM-DD"),
    service: str = Query("followup"),
    practitioner: str = Query("any"),
    exclude: Optional[str] = Query(None, description="Appointment id to ignore (rescheduling)"),
):
    """Return available start times for a date, service and practitioner"""
    target = parse_date(date_str)
    svc = service_key(service)
    prac = practitioner_key(practitioner)
    if not svc or not prac:
        raise HTTPException(status_code=400, detail="Unknown service or practitioner")

    all_slots = day_slots(target)
    today = clinic_now().date()
    if target < today or target > today + timedelta(days=MAX_ADVANCE_DAYS) or not all_slots:
        closed = not all_slots
        return {"date": date_str, "open": not closed, "all_slots": all_slots, "slots": [], "booked": all_slots,
                "message": "The clinic is closed on this day" if closed else "This date can't be booked online"}

    with get_db() as conn:
        avail = available_slots(conn, target, svc, prac, exclude)
    slots = [t for t in all_slots if t in avail]
    return {
        "date": date_str,
        "open": True,
        "all_slots": all_slots,
        "slots": slots,
        "booked": [t for t in all_slots if t not in avail],
    }


def notify(background: BackgroundTasks, kind: str, row, previous=None, by: str = "patient",
           message: Optional[str] = None, patient: bool = True):
    """Email the patient and the practitioner(s)/front desk after the response is sent,
    so a slow mail server never delays booking."""
    if patient:
        background.add_task(mailer.send_appointment_email, kind, public_view(row))
    background.add_task(mailer.send_staff_email, kind, admin_view(row),
                        admin_view(previous) if previous else None, by, message)


def add_patient_message(conn, row, action: str, message: Optional[str]):
    """Keep the patient's message in the appointment notes so staff can see it later."""
    if not message:
        return
    line = f"[{clinic_now():%d %b %Y %H:%M}] Patient ({action}): {message}"
    notes = f"{row['notes']}\n{line}" if row["notes"] else line
    conn.execute("UPDATE appointments SET notes = ? WHERE id = ?", (notes, row["id"]))


@app.post("/api/book")
def book_appointment(booking: BookingRequest, background: BackgroundTasks):
    """Create a new appointment"""
    appt_id = str(uuid.uuid4())
    now = clinic_now().isoformat(timespec="seconds")
    with get_db() as conn:
        with write_txn(conn):
            prac = assign_practitioner(conn, booking.appointment_date, booking.appointment_time,
                                       booking.service, booking.practitioner)
            conn.execute(
                """INSERT INTO appointments
                   (id, reference, patient_name, email, phone, service, practitioner, reason,
                    appointment_date, appointment_time, created_at, updated_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed')""",
                (appt_id, new_reference(conn), booking.patient_name, booking.email, booking.phone,
                 booking.service, prac, booking.reason, booking.appointment_date,
                 booking.appointment_time, now, now)
            )
        row = get_row(conn, appt_id)
    notify(background, "confirmed", row)
    return public_view(row)


@app.post("/api/my-booking")
def lookup_my_booking(lookup: BookingLookup):
    """Patient self-service: find a booking by reference + email"""
    with get_db() as conn:
        return public_view(find_patient_booking(conn, lookup))


@app.post("/api/my-booking/cancel")
def cancel_my_booking(req: CancelRequest, background: BackgroundTasks):
    with get_db() as conn:
        with write_txn(conn):
            row = find_patient_booking(conn, req)
            require_upcoming(row)
            conn.execute("UPDATE appointments SET status = 'cancelled', updated_at = ? WHERE id = ?",
                         (clinic_now().isoformat(timespec="seconds"), row["id"]))
            add_patient_message(conn, row, "cancelled", req.message)
        row = get_row(conn, row["id"])
    notify(background, "cancelled", row, message=req.message)
    return public_view(row)


@app.post("/api/my-booking/reschedule")
def reschedule_my_booking(req: RescheduleRequest, background: BackgroundTasks):
    with get_db() as conn:
        with write_txn(conn):
            previous = find_patient_booking(conn, req)
            require_upcoming(previous)
            # Keep the same practitioner if they're free, otherwise anyone available
            try:
                prac = assign_practitioner(conn, req.appointment_date, req.appointment_time,
                                           previous["service"], previous["practitioner"], exclude_id=previous["id"])
            except HTTPException as e:
                if e.status_code != 409:
                    raise
                prac = assign_practitioner(conn, req.appointment_date, req.appointment_time,
                                           previous["service"], "any", exclude_id=previous["id"])
            conn.execute(
                "UPDATE appointments SET appointment_date = ?, appointment_time = ?, practitioner = ?, "
                "updated_at = ?, reminder_sent_at = NULL WHERE id = ?",
                (req.appointment_date, req.appointment_time, prac,
                 clinic_now().isoformat(timespec="seconds"), previous["id"])
            )
            add_patient_message(conn, previous, "rescheduled", req.message)
        row = get_row(conn, previous["id"])
    notify(background, "rescheduled", row, previous=previous, message=req.message)
    return public_view(row)


# ---------- Admin API ----------
@app.post("/api/admin/login")
def admin_login(req: LoginRequest):
    if not hmac.compare_digest(req.password.encode(), ADMIN_PASSWORD.encode()):
        time.sleep(0.5)
        raise HTTPException(status_code=401, detail="Incorrect password")
    expiry = str(int(time.time()) + TOKEN_TTL_SECONDS)
    return {"token": f"{expiry}.{_sign(expiry)}", "expires_in": TOKEN_TTL_SECONDS}


def query_appointments(conn, date_str=None, date_from=None, date_to=None, status=None,
                       practitioner=None, q=None):
    sql = "SELECT * FROM appointments WHERE 1=1"
    params = []
    if date_str:
        sql += " AND appointment_date = ?"
        params.append(date_str)
    if date_from:
        sql += " AND appointment_date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND appointment_date <= ?"
        params.append(date_to)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if practitioner:
        sql += " AND practitioner = ?"
        params.append(practitioner)
    if q:
        like = f"%{q.strip().lower()}%"
        sql += (" AND (lower(patient_name) LIKE ? OR lower(email) LIKE ? OR phone LIKE ?"
                " OR lower(reference) LIKE ?)")
        params += [like, like, like, like]
    sql += " ORDER BY appointment_date, appointment_time"
    return conn.execute(sql, params).fetchall()


@app.get("/api/appointments", dependencies=[Depends(require_admin)])
def list_appointments(
    date_str: Optional[str] = Query(None, alias="date"),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    practitioner: Optional[str] = None,
    q: Optional[str] = None,
):
    """List appointments with optional filters (admin only)"""
    with get_db() as conn:
        rows = query_appointments(conn, date_str, date_from, date_to, status, practitioner, q)
    return [admin_view(r) for r in rows]


@app.get("/api/appointments/{appt_id}", dependencies=[Depends(require_admin)])
def get_appointment(appt_id: str):
    with get_db() as conn:
        return admin_view(get_row(conn, appt_id))


@app.patch("/api/appointments/{appt_id}", dependencies=[Depends(require_admin)])
def update_appointment(appt_id: str, update: AppointmentUpdate, background: BackgroundTasks):
    """Change status, notes, contact details or reschedule (admin only). Patients are emailed
    when their appointment is moved, cancelled or reinstated."""
    changes = update.model_dump(exclude_unset=True, exclude_none=True)
    if "notes" in update.model_fields_set:
        changes["notes"] = _clean_optional_text(update.notes)
    with get_db() as conn:
        with write_txn(conn):
            row = get_row(conn, appt_id)
            merged = {**dict(row), **changes}
            schedule_fields = ("appointment_date", "appointment_time", "practitioner", "service")
            rescheduled = any(f in changes and changes[f] != row[f] for f in schedule_fields)
            reactivated = row["status"] == "cancelled" and merged["status"] != "cancelled"
            if merged["status"] != "cancelled" and (rescheduled or reactivated):
                changes["practitioner"] = assign_practitioner(
                    conn, merged["appointment_date"], merged["appointment_time"],
                    merged["service"], merged["practitioner"], exclude_id=appt_id)
            elif changes.get("practitioner") == "any":
                changes.pop("practitioner")
            moved = merged["status"] != "cancelled" and (
                changes.get("appointment_date", row["appointment_date"]) != row["appointment_date"]
                or changes.get("appointment_time", row["appointment_time"]) != row["appointment_time"])
            if moved:
                changes["reminder_sent_at"] = None
            if changes:
                changes["updated_at"] = clinic_now().isoformat(timespec="seconds")
                cols = ", ".join(f"{k} = ?" for k in changes)
                conn.execute(f"UPDATE appointments SET {cols} WHERE id = ?", (*changes.values(), appt_id))
        updated = get_row(conn, appt_id)

    if appointment_start(updated) > clinic_now():
        reassigned = updated["status"] != "cancelled" and (
            updated["practitioner"] != row["practitioner"] or updated["service"] != row["service"])
        if reactivated:
            notify(background, "confirmed", updated, by="clinic")
        elif moved or reassigned:
            notify(background, "rescheduled", updated, previous=row, by="clinic")
        elif row["status"] == "confirmed" and updated["status"] == "cancelled":
            notify(background, "cancelled", updated, by="clinic")
        elif (updated["notes"] or None) != (row["notes"] or None) and updated["status"] == "confirmed":
            notify(background, "note", updated, by="clinic", patient=False)
    return admin_view(updated)


@app.delete("/api/appointments/{appt_id}", dependencies=[Depends(require_admin)])
def cancel_appointment(appt_id: str, background: BackgroundTasks):
    """Cancel an appointment (admin only)"""
    with get_db() as conn:
        with write_txn(conn):
            row = get_row(conn, appt_id)
            conn.execute("UPDATE appointments SET status = 'cancelled', updated_at = ? WHERE id = ?",
                         (clinic_now().isoformat(timespec="seconds"), appt_id))
        updated = get_row(conn, appt_id)
    if row["status"] == "confirmed" and appointment_start(row) > clinic_now():
        notify(background, "cancelled", updated, by="clinic")
    return {"message": "Appointment cancelled"}


@app.get("/api/admin/stats", dependencies=[Depends(require_admin)])
def admin_stats():
    today = clinic_now().date()
    week_end = today + timedelta(days=7)
    month_start = today.replace(day=1)
    with get_db() as conn:
        today_rows = conn.execute(
            "SELECT practitioner, COUNT(*) AS n FROM appointments "
            "WHERE appointment_date = ? AND status != 'cancelled' GROUP BY practitioner",
            (today.isoformat(),)
        ).fetchall()
        upcoming = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE status = 'confirmed' "
            "AND appointment_date >= ? AND appointment_date <= ?",
            (today.isoformat(), week_end.isoformat())
        ).fetchone()[0]
        month = conn.execute(
            "SELECT status, COUNT(*) AS n FROM appointments "
            "WHERE appointment_date >= ? AND appointment_date <= ? GROUP BY status",
            (month_start.isoformat(), today.isoformat())
        ).fetchall()
    by_status = {s: 0 for s in STATUSES}
    by_status.update({r["status"]: r["n"] for r in month})
    return {
        "today": sum(r["n"] for r in today_rows),
        "today_by_practitioner": {PRACTITIONERS.get(r["practitioner"], r["practitioner"]): r["n"] for r in today_rows},
        "upcoming_7_days": upcoming,
        "month_to_date": by_status,
    }


def _csv_safe(value) -> str:
    text = "" if value is None else str(value)
    # Stop spreadsheet apps from treating patient-entered text as a formula
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


@app.get("/api/admin/export.csv", dependencies=[Depends(require_admin)])
def export_csv(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    practitioner: Optional[str] = None,
    q: Optional[str] = None,
):
    with get_db() as conn:
        rows = query_appointments(conn, None, date_from, date_to, status, practitioner, q)
    fields = ["reference", "appointment_date", "appointment_time", "duration", "patient_name", "email",
              "phone", "service_name", "practitioner_name", "status", "reason", "notes", "created_at"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Sr No"] + fields)
    for i, r in enumerate(rows, 1):
        view = admin_view(r)
        writer.writerow([i] + [_csv_safe(view[f]) for f in fields])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="pyshio-appointments-{clinic_now().date()}.csv"'},
    )


@app.get("/api/admin/blocks", dependencies=[Depends(require_admin)])
def list_blocks(date_from: Optional[str] = None):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM blocked_times WHERE block_date >= ? ORDER BY block_date, start_time",
            (date_from or clinic_now().date().isoformat(),)
        ).fetchall()
    return [{**dict(r), "practitioner_name": PRACTITIONERS.get(r["practitioner"], "All practitioners")} for r in rows]


@app.post("/api/admin/blocks", dependencies=[Depends(require_admin)])
def create_block(block: BlockRequest):
    """Block time off (holiday, leave, training). Existing bookings are reported, not cancelled."""
    parse_date(block.block_date)
    if bool(block.start_time) != bool(block.end_time):
        raise HTTPException(status_code=400, detail="Give both a start and end time, or neither for a full day")
    if block.start_time and to_minutes(block.start_time) >= to_minutes(block.end_time):
        raise HTTPException(status_code=400, detail="End time must be after start time")
    with get_db() as conn:
        with write_txn(conn):
            cur = conn.execute(
                "INSERT INTO blocked_times (block_date, start_time, end_time, practitioner, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (block.block_date, block.start_time, block.end_time, block.practitioner,
                 _clean_optional_text(block.reason), clinic_now().isoformat(timespec="seconds"))
            )
        start = to_minutes(block.start_time) if block.start_time else 0
        end = to_minutes(block.end_time) if block.end_time else 24 * 60
        conflicts = []
        for r in query_appointments(conn, block.block_date, status="confirmed"):
            if block.practitioner and r["practitioner"] != block.practitioner:
                continue
            a_start = to_minutes(r["appointment_time"])
            if a_start < end and a_start + service_duration(r["service"]) > start:
                conflicts.append(admin_view(r))
    return {"id": cur.lastrowid, "conflicts": conflicts}


@app.delete("/api/admin/blocks/{block_id}", dependencies=[Depends(require_admin)])
def delete_block(block_id: int):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM blocked_times WHERE id = ?", (block_id,))
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Block not found")
    return {"message": "Block removed"}


# ---------- Serve Frontend ----------
@app.get("/")
def serve_index():
    return FileResponse(BASE_DIR / "index.html")


@app.get("/admin")
def serve_admin():
    return FileResponse(BASE_DIR / "admin.html")


init_db()

if ADMIN_PASSWORD == "admin123":
    print("WARNING: using default admin password. Set PYSHIO_ADMIN_PASSWORD before going live.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)

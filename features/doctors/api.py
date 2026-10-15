"""Doctors: registration, the public directory and profiles, opening hours, services, leave, admin approval.

This file: HTTP endpoints and request models.
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Literal, Optional

from shared import auth, geo, reference_data as ref, settings, validators as v
from shared.auth import admin_user, doctor_user
from shared.common import audit, stamp
from shared.db import get_db, write_txn

from features.accounts.api import PatientRegister

router = APIRouter()


class DoctorProfileFields(BaseModel):
    category: str = "physiotherapist"
    lat: Optional[float] = None
    lng: Optional[float] = None
    qualification: str
    registration_no: str
    registration_council: Optional[str] = None
    experience_years: int = Field(default=0, ge=0, le=70)
    specialities: List[str] = []
    bio: Optional[str] = Field(default=None, max_length=2000)
    clinic_name: str
    address: str
    city: str
    pincode: Optional[str] = None
    slot_minutes: int = 30
    accepts_clinic_payment: bool = True
    accepts_online_payment: bool = False
    state: Optional[str] = None
    offers_home_visits: bool = False
    home_visit_radius_km: float = Field(default=5, ge=1, le=25)
    home_visit_fee: Optional[int] = Field(default=None, ge=0, le=20000)   # usual travel / home-visit charge, shown as "from"

    _qualification = field_validator("qualification")(v.required_text("Qualification", 120))
    _state = field_validator("state")(v.state)
    _registration_no = field_validator("registration_no")(v.required_text("Registration number", 60))
    _council = field_validator("registration_council")(v.optional_text)
    _bio = field_validator("bio")(v.optional_text)
    _clinic = field_validator("clinic_name")(v.required_text("Clinic name", 120))
    _address = field_validator("address")(v.required_text("Address", 300))
    _pincode = field_validator("pincode")(v.pincode)

    @field_validator("city")
    @classmethod
    def _city(cls, value):
        return v.required_text("City", 60)(value).title()

    @field_validator("category")
    @classmethod
    def _category(cls, value):
        if value not in settings.DOCTOR_CATEGORIES:
            raise ValueError("Unknown doctor category")
        return value

    @model_validator(mode="after")
    def _location(self):
        if (self.lat is None) != (self.lng is None):
            raise ValueError("Pick the clinic location on the map")
        if self.lat is not None:
            geo.check_point(self.lat, self.lng)
        allowed = settings.DOCTOR_CATEGORIES[self.category]["specialities"]
        wrong = [x for x in self.specialities if x not in allowed]
        if wrong:
            label = settings.DOCTOR_CATEGORIES[self.category]["label"]
            raise ValueError(f"{', '.join(wrong)} is not a {label} speciality")
        return self

    @field_validator("specialities")
    @classmethod
    def _specialities(cls, value):
        unknown = [s for s in value if s not in settings.SPECIALITIES]
        if unknown:
            raise ValueError(f"Unknown speciality: {', '.join(unknown)}")
        return list(dict.fromkeys(value))

    @field_validator("slot_minutes")
    @classmethod
    def _slot(cls, value):
        if value not in (10, 15, 20, 30, 45, 60):
            raise ValueError("Slot length must be 10, 15, 20, 30, 45 or 60 minutes")
        return value

    @model_validator(mode="after")
    def _payment(self):
        if not (self.accepts_clinic_payment or self.accepts_online_payment):
            raise ValueError("Accept at least one payment method")
        self.state = self.state or ref.state_for_city(self.city)
        return self


class DoctorRegister(PatientRegister, DoctorProfileFields):
    consultation_fee: int = Field(ge=0, le=100000)


class DoctorProfileUpdate(DoctorProfileFields):
    name: str
    phone: str

    _name = field_validator("name")(v.name)
    _phone = field_validator("phone")(v.phone)


class HoursEntry(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start_time: str
    end_time: str

    _start = field_validator("start_time")(v.hhmm)
    _end = field_validator("end_time")(v.hhmm)

    @model_validator(mode="after")
    def _order(self):
        if self.start_time >= self.end_time:
            raise ValueError(f"{WEEKDAYS[self.weekday]}: end time must be after start time")
        return self


class ServiceIn(BaseModel):
    name: str
    duration: int = Field(ge=10, le=240)
    fee: int = Field(ge=0, le=100000)
    active: bool = True

    _name = field_validator("name")(v.required_text("Service name", 80))


class BlockIn(BaseModel):
    block_date: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    cancel_conflicts: bool = False      # cancel (and refund) bookings that fall inside the time off
    message: Optional[str] = Field(default=None, max_length=1000)
    reason: Optional[str] = Field(default=None, max_length=200)

    _date = field_validator("block_date")(v.iso_date)
    _reason = field_validator("reason")(v.optional_text)

    @field_validator("start_time", "end_time")
    @classmethod
    def _time(cls, value):
        value = v.optional_text(value)
        return v.hhmm(value) if value else None

    @model_validator(mode="after")
    def _pair(self):
        if bool(self.start_time) != bool(self.end_time):
            raise ValueError("Give both a start and end time, or neither for a full day")
        if self.start_time and self.start_time >= self.end_time:
            raise ValueError("End time must be after start time")
        return self


class DoctorStatusUpdate(BaseModel):
    status: Literal["approved", "rejected", "suspended"]
    reason: Optional[str] = Field(default=None, max_length=500)

    _reason = field_validator("reason")(v.optional_text)


@router.post("/api/auth/register-doctor")
def register_doctor(data: DoctorRegister, response: Response, background: BackgroundTasks, request: Request):
    registration_throttle(request)
    with get_db() as conn:
        with write_txn(conn):
            user_id = _create_user(conn, "doctor", data)
            conn.execute(
                """INSERT INTO doctors (user_id, slug, qualification, registration_no, registration_council,
                   experience_years, specialities, bio, clinic_name, address, city, pincode, slot_minutes,
                   accepts_clinic_payment, accepts_online_payment, status, created_at, updated_at, category, lat, lng,
                   state, offers_home_visits, home_visit_radius_km, home_visit_fee)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, make_slug(conn, data.name, data.city), data.qualification, data.registration_no,
                 data.registration_council, data.experience_years, "|".join(data.specialities), data.bio,
                 data.clinic_name, data.address, data.city, data.pincode, data.slot_minutes,
                 int(data.accepts_clinic_payment), int(data.accepts_online_payment), stamp(), stamp(),
                 data.category, data.lat, data.lng, data.state, int(data.offers_home_visits), data.home_visit_radius_km,
                 data.home_visit_fee))
            conn.executemany("INSERT INTO doctor_hours (doctor_id, weekday, start_time, end_time) VALUES (?, ?, ?, ?)",
                             [(user_id, d, s, e) for d, s, e in DEFAULT_HOURS])
            conn.execute("INSERT INTO services (doctor_id, name, duration, fee) VALUES (?, 'Consultation', 30, ?)",
                         (user_id, data.consultation_fee))
        user = dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        doctor = dict(get_doctor(conn, user_id))
        admins = [r["email"] for r in conn.execute("SELECT email FROM users WHERE role = 'admin' AND is_active = 1")]
    auth.start_session(response, user_id)
    background.add_task(doctors_emails.send_doctor_application_received, doctor)
    background.add_task(doctors_emails.send_new_doctor_to_admins, admins, doctor)
    return me_payload(user)


# ---------- Public doctor directory ----------
@router.get("/api/doctors")
def list_doctors(city: Optional[str] = None, speciality: Optional[str] = None, q: Optional[str] = None,
                 category: Optional[str] = None, lat: Optional[float] = None, lng: Optional[float] = None,
                 radius: Optional[float] = Query(None, gt=0, le=50),
                 sort: Literal["cheapest", "nearest", "experience", "rated"] = "cheapest",
                 home_visit: bool = False, video: bool = False, user: Optional[dict] = Depends(auth.current_user)):
    """Doctor directory. With lat/lng: only doctors within the radius (default 3 km), cheapest fee first.
    video=true lists doctors offering video consultations from anywhere (distance is still shown)."""
    sql = DOCTOR_SELECT + " WHERE d.status = 'approved' AND u.is_active = 1"
    params = []
    if category:
        sql += " AND d.category = ?"
        params.append(category)
    if home_visit:
        sql += " AND d.offers_home_visits = 1"
    if video:
        sql += " AND d.offers_video = 1"
    if city:
        sql += " AND lower(d.city) = lower(?)"
        params.append(city.strip())
    wanted = [x.strip() for x in (speciality or "").split("|") if x.strip()][:10]
    if wanted:  # one speciality, or several separated by "|" (any of them)
        sql += " AND (" + " OR ".join("('|' || d.specialities || '|') LIKE ?" for _ in wanted) + ")"
        params += [f"%|{x}|%" for x in wanted]
    if q:
        like = f"%{q.strip().lower()}%"
        sql += " AND (lower(u.name) LIKE ? OR lower(d.clinic_name) LIKE ? OR lower(COALESCE(d.bio, '')) LIKE ?)"
        params += [like, like, like]
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
        visits = visit_counts(conn, user["id"]) if user and user["role"] == "patient" else {}
    if lat is not None and lng is not None:
        try:
            geo.check_point(lat, lng)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        found = [{**doctor_public(r), "distance_km": d}
                 for d, r in geo.within(rows, lat, lng, 20000 if video else radius or settings.NEARBY_RADIUS_KM)]
    else:
        found = [doctor_public(r) for r in rows]
    for d in found:
        d["visited_count"], d["last_visit"] = visits.get(d["id"], (0, None))
    no_fee = float("inf")
    keys = {
        "cheapest": lambda d: (d["fee_from"] if d["fee_from"] is not None else no_fee, d.get("distance_km", 0)),
        "nearest": lambda d: (d.get("distance_km", 0), d["fee_from"] if d["fee_from"] is not None else no_fee),
        "experience": lambda d: (-d["experience_years"], d["name"]),
        "rated": lambda d: (-(d["rating"] or 0), -d["rating_count"], d["fee_from"] if d["fee_from"] is not None else no_fee),
    }
    return sorted(found, key=keys[sort])


@router.get("/api/cities")
def list_cities():
    with get_db() as conn:
        rows = conn.execute("SELECT DISTINCT city FROM doctors WHERE status = 'approved' ORDER BY city")
        return [r["city"] for r in rows]


@router.get("/api/doctors/{slug}")
def get_doctor_profile(slug: str, user: Optional[dict] = Depends(auth.current_user)):
    with get_db() as conn:
        row = conn.execute(DOCTOR_SELECT + " WHERE d.slug = ?", (slug,)).fetchone()
        # Unapproved profiles are only visible to their owner (preview) and admins
        owner = bool(user and row and (user["role"] == "admin" or user["id"] == row["user_id"]))
        if not row or ((row["status"] != "approved" or not row["is_active"]) and not owner):
            raise HTTPException(status_code=404, detail="Doctor not found")
        view = {**doctor_public(row), "status": row["status"], **doctor_extras(conn, row["user_id"])}
        if user and user["role"] == "patient":
            view["visited_count"], view["last_visit"] = visit_counts(conn, user["id"]).get(row["user_id"], (0, None))
        return view


@router.get("/api/doctors/{slug}/slots")
def doctor_slots(slug: str, date_str: str = Query(..., alias="date"), service_id: int = Query(...),
                 exclude: Optional[str] = None, user: Optional[dict] = Depends(auth.current_user)):
    d = scheduling.parse_date(date_str)
    with get_db() as conn:
        doctor = bookable_doctor(conn, slug)
        service = conn.execute("SELECT * FROM services WHERE id = ? AND doctor_id = ? AND active = 1",
                               (service_id, doctor["user_id"])).fetchone()
        if not service:
            raise HTTPException(status_code=404, detail="Service not found")
        # Only someone allowed to see that appointment may ignore it (when rescheduling)
        if exclude:
            try:
                if not user:
                    raise HTTPException(status_code=404)
                appointment_for(conn, exclude, user)
            except HTTPException:
                exclude = None
        return {"date": date_str, **scheduling.day_availability(conn, doctor, d, service["duration"], exclude)}


@router.get("/api/doctor/profile")
def my_doctor_profile(user: dict = Depends(doctor_user)):
    with get_db() as conn:
        return {**doctor_private(get_doctor(conn, user["id"])), **doctor_extras(conn, user["id"], include_inactive=True)}


@router.put("/api/doctor/profile")
def update_doctor_profile(data: DoctorProfileUpdate, background: BackgroundTasks, user: dict = Depends(doctor_user)):
    with get_db() as conn:
        with write_txn(conn):
            current = get_doctor(conn, user["id"])
            # Changing credentials sends an approved profile back for re-verification
            reverify = current["status"] == "approved" and (
                data.registration_no != current["registration_no"] or data.qualification != current["qualification"]
                or data.category != current["category"])
            # A rejected doctor who fixes their profile goes back into the approval queue
            reverify = reverify or current["status"] == "rejected"
            conn.execute("UPDATE users SET name = ?, phone = ? WHERE id = ?", (data.name, data.phone, user["id"]))
            conn.execute(
                """UPDATE doctors SET qualification = ?, registration_no = ?, registration_council = ?,
                   experience_years = ?, specialities = ?, bio = ?, clinic_name = ?, address = ?, city = ?,
                   pincode = ?, slot_minutes = ?, accepts_clinic_payment = ?, accepts_online_payment = ?,
                   status = ?, updated_at = ?, category = ?, lat = ?, lng = ?, state = ?, offers_home_visits = ?,
                   home_visit_radius_km = ?, home_visit_fee = ? WHERE user_id = ?""",
                (data.qualification, data.registration_no, data.registration_council, data.experience_years,
                 "|".join(data.specialities), data.bio, data.clinic_name, data.address, data.city, data.pincode,
                 data.slot_minutes, int(data.accepts_clinic_payment), int(data.accepts_online_payment),
                 "pending" if reverify else current["status"], stamp(), data.category, data.lat, data.lng, data.state,
                 int(data.offers_home_visits), data.home_visit_radius_km, data.home_visit_fee, user["id"]))
        doctor = dict(get_doctor(conn, user["id"]))
        if reverify:
            admins = [r["email"] for r in conn.execute("SELECT email FROM users WHERE role = 'admin' AND is_active = 1")]
            background.add_task(doctors_emails.send_new_doctor_to_admins, admins, doctor)
        return {**doctor_private(doctor), **doctor_extras(conn, user["id"], include_inactive=True),
                "reverification_required": reverify}


@router.put("/api/doctor/hours")
def set_hours(hours: List[HoursEntry], user: dict = Depends(doctor_user)):
    previous = {}
    for h in sorted(hours, key=lambda h: (h.weekday, h.start_time)):
        last = previous.get(h.weekday)
        if last and h.start_time < last.end_time:
            raise HTTPException(status_code=400, detail=f"{WEEKDAYS[h.weekday]}: time ranges overlap")
        previous[h.weekday] = h
    with get_db() as conn:
        with write_txn(conn):
            conn.execute("DELETE FROM doctor_hours WHERE doctor_id = ?", (user["id"],))
            conn.executemany("INSERT INTO doctor_hours (doctor_id, weekday, start_time, end_time) VALUES (?, ?, ?, ?)",
                             [(user["id"], h.weekday, h.start_time, h.end_time) for h in hours])
        return doctor_extras(conn, user["id"], include_inactive=True)["hours"]


@router.post("/api/doctor/services")
def add_service(data: ServiceIn, user: dict = Depends(doctor_user)):
    with get_db() as conn:
        conn.execute("INSERT INTO services (doctor_id, name, duration, fee, active) VALUES (?, ?, ?, ?, ?)",
                     (user["id"], data.name, data.duration, data.fee, int(data.active)))
        return doctor_extras(conn, user["id"], include_inactive=True)["services"]


@router.put("/api/doctor/services/{service_id}")
def update_service(service_id: int, data: ServiceIn, user: dict = Depends(doctor_user)):
    with get_db() as conn:
        with write_txn(conn):
            cur = conn.execute("UPDATE services SET name = ?, duration = ?, fee = ?, active = ? WHERE id = ? AND doctor_id = ?",
                               (data.name, data.duration, data.fee, int(data.active), service_id, user["id"]))
            if not cur.rowcount:
                raise HTTPException(status_code=404, detail="Service not found")
            if not conn.execute("SELECT 1 FROM services WHERE doctor_id = ? AND active = 1", (user["id"],)).fetchone():
                raise HTTPException(status_code=400, detail="Keep at least one active service")
        return doctor_extras(conn, user["id"], include_inactive=True)["services"]


@router.get("/api/doctor/blocks")
def list_blocks(user: dict = Depends(doctor_user)):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM blocked_times WHERE doctor_id = ? AND block_date >= ? ORDER BY block_date, start_time",
                            (user["id"], settings.now().date().isoformat())).fetchall()
    return [dict(r) for r in rows]


@router.post("/api/doctor/blocks")
def add_block(data: BlockIn, background: BackgroundTasks, user: dict = Depends(doctor_user)):
    """Block time off. Existing bookings are listed so the doctor can reschedule or cancel them."""
    scheduling.parse_date(data.block_date)
    with get_db() as conn:
        cur = conn.execute("INSERT INTO blocked_times (doctor_id, block_date, start_time, end_time, reason, created_at) "
                           "VALUES (?, ?, ?, ?, ?, ?)",
                           (user["id"], data.block_date, data.start_time, data.end_time, data.reason, stamp()))
        start = scheduling.to_minutes(data.start_time) if data.start_time else 0
        end = scheduling.to_minutes(data.end_time) if data.end_time else 24 * 60
        rows = conn.execute(APPOINTMENT_SELECT + " WHERE a.doctor_id = ? AND a.appointment_date = ? AND a.status = 'confirmed'",
                            (user["id"], data.block_date)).fetchall()
    conflicts = [appointment_view(r, "doctor") for r in rows
                 if scheduling.to_minutes(r["appointment_time"]) < end
                 and scheduling.to_minutes(r["appointment_time"]) + r["duration"] > start]
    cancelled = 0
    if data.cancel_conflicts:
        note = data.message or f"The doctor is unavailable at this time ({data.reason or 'time off'}). Please book another slot."
        for c in conflicts:
            if appointment_start(c) > settings.now():
                cancel(c["id"], CancelRequest(message=note), background, user)
                cancelled += 1
    return {"id": cur.lastrowid, "conflicts": conflicts, "cancelled": cancelled}


@router.delete("/api/doctor/blocks/{block_id}")
def delete_block(block_id: int, user: dict = Depends(doctor_user)):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM blocked_times WHERE id = ? AND doctor_id = ?", (block_id, user["id"]))
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="Block not found")
    return {"ok": True}


@router.get("/api/admin/doctors")
def admin_doctors(status: Optional[str] = None, user: dict = Depends(admin_user)):
    sql = DOCTOR_SELECT + (" WHERE d.status = ?" if status else "") + " ORDER BY d.created_at DESC"
    with get_db() as conn:
        return [doctor_private(r) for r in conn.execute(sql, (status,) if status else ())]


@router.post("/api/admin/doctors/{doctor_id}/status")
def admin_doctor_status(doctor_id: int, data: DoctorStatusUpdate, background: BackgroundTasks,
                        user: dict = Depends(admin_user)):
    if data.status in ("rejected", "suspended") and not data.reason:
        raise HTTPException(status_code=400, detail="Please give a reason; it's emailed to the doctor")
    with get_db() as conn:
        get_doctor(conn, doctor_id)
        conn.execute("UPDATE doctors SET status = ?, status_reason = ?, approved_at = COALESCE(?, approved_at), "
                     "updated_at = ? WHERE user_id = ?",
                     (data.status, data.reason, stamp() if data.status == "approved" else None, stamp(), doctor_id))
        audit(conn, user["id"], f"doctor.{data.status}", f"doctor:{doctor_id}", data.reason)
        doctor = dict(get_doctor(conn, doctor_id))
    if data.status in ("rejected", "suspended"):
        wind_down_appointments("doctor_id = ?", (doctor_id,),
                               "This doctor is no longer taking bookings on the platform. Please book another doctor.",
                               background)
    background.add_task(doctors_emails.send_doctor_status, doctor, data.status, data.reason)
    return doctor_private(doctor)


# Imported last, so slices can depend on each other in any order
from features.accounts.logic import _create_user, me_payload, registration_throttle  # noqa: E402
from features.appointments.api import cancel, CancelRequest  # noqa: E402
from features.appointments.logic import appointment_for, APPOINTMENT_SELECT, appointment_start, appointment_view, visit_counts, wind_down_appointments  # noqa: E402
from features.doctors.logic import bookable_doctor, DEFAULT_HOURS, doctor_extras, doctor_private, doctor_public, DOCTOR_SELECT, get_doctor, make_slug, WEEKDAYS  # noqa: E402
from features.appointments import scheduling  # noqa: E402
from features.doctors import emails as doctors_emails  # noqa: E402

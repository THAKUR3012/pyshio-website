"""Pharma insights: companies, divisions, MRs, brands, MR meetings and sales / demand insights.

This file: HTTP endpoints and request models.
"""
import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Literal, Optional

from shared import auth, reference_data as ref, settings, validators as v
from shared.auth import admin_user, company_user, doctor_user, mr_user
from shared.common import audit, stamp
from shared.db import get_db, write_txn

from features.pharma_insights.logic import care_area_list

router = APIRouter()


# ---------- Request models ----------
class CompanyFields(BaseModel):
    company_name: str
    cin: Optional[str] = Field(default=None, max_length=30)
    gstin: Optional[str] = Field(default=None, max_length=20)
    address: str
    city: str
    state: Optional[str] = None
    website: Optional[str] = Field(default=None, max_length=200)

    _company = field_validator("company_name")(v.required_text("Company name", 120))
    _texts = field_validator("cin", "gstin", "website")(v.optional_text)
    _address = field_validator("address")(v.required_text("Address", 300))
    _state = field_validator("state")(v.state)

    @field_validator("city")
    @classmethod
    def _city(cls, value):
        return v.required_text("City", 60)(value).title()

    @model_validator(mode="after")
    def _default_state(self):
        self.state = self.state or ref.state_for_city(self.city)
        return self


class CompanyRegister(CompanyFields):
    name: str
    email: str
    phone: str
    password: str

    _name = field_validator("name")(v.name)
    _email = field_validator("email")(v.email)
    _phone = field_validator("phone")(v.phone)
    _password = field_validator("password")(v.password)


class CompanyUpdate(CompanyFields):
    name: str
    phone: str

    _name = field_validator("name")(v.name)
    _phone = field_validator("phone")(v.phone)


class DivisionIn(BaseModel):
    name: str
    care_areas: List[str] = Field(min_length=1)
    active: bool = True

    _name = field_validator("name")(v.required_text("Division name", 80))
    _areas = field_validator("care_areas")(care_area_list)


class MrFields(BaseModel):
    division_id: Optional[int] = None
    employee_code: Optional[str] = Field(default=None, max_length=40)
    territory_city: Optional[str] = Field(default=None, max_length=60)
    territory_state: str

    _texts = field_validator("employee_code", "territory_city")(v.optional_text)

    @field_validator("territory_state")
    @classmethod
    def _state(cls, value):
        value = v.state(value)
        if not value:
            raise ValueError("Choose the MR's state")
        return value

    @model_validator(mode="after")
    def _city(self):
        if self.territory_city:
            self.territory_city = self.territory_city.title()
        return self


class MrInvite(MrFields):
    name: str
    email: str
    phone: str

    _name = field_validator("name")(v.name)
    _email = field_validator("email")(v.email)
    _phone = field_validator("phone")(v.phone)


class MrUpdate(MrFields):
    active: bool = True


class MrRequestIn(BaseModel):
    doctor_id: int
    purpose: str
    products: Optional[str] = Field(default=None, max_length=300)
    preferred_date: Optional[str] = None
    preferred_time: Optional[str] = None

    _purpose = field_validator("purpose")(v.required_text("Purpose", 300))
    _products = field_validator("products")(v.optional_text)

    @field_validator("preferred_date")
    @classmethod
    def _date(cls, value):
        value = v.optional_text(value)
        if value:
            v.iso_date(value)
            today = settings.now().date().isoformat()
            if value < today:
                raise ValueError("Pick a date from today onwards")
        return value

    @field_validator("preferred_time")
    @classmethod
    def _time(cls, value):
        value = v.optional_text(value)
        return v.hhmm(value) if value else None


class MrDecision(BaseModel):
    scheduled_for: Optional[str] = None  # YYYY-MM-DDTHH:MM, needed to accept
    note: Optional[str] = Field(default=None, max_length=300)

    _note = field_validator("note")(v.optional_text)

    @field_validator("scheduled_for")
    @classmethod
    def _when(cls, value):
        value = v.optional_text(value)
        if value:
            try:
                datetime.strptime(value[:16], "%Y-%m-%dT%H:%M")
            except ValueError:
                raise ValueError("Pick a meeting date and time")
            value = value[:16]
        return value


class DoctorSettings(BaseModel):
    share_insights: bool
    accept_mr_meetings: bool


class CompanyStatus(BaseModel):
    status: Literal["approved", "rejected", "suspended"]
    reason: Optional[str] = Field(default=None, max_length=500)

    _reason = field_validator("reason")(v.optional_text)


# ---------- Company accounts ----------
@router.post("/api/auth/register-company")
def register_company(data: CompanyRegister, response: Response, request: Request, background: BackgroundTasks):
    with get_db() as conn:
        ip = request.client.host if request.client else "unknown"
        if auth.rate_limited(conn, f"register:{ip}", 10, 60):
            raise HTTPException(status_code=429, detail="Too many sign-ups from this network. Please try again later.")
        with write_txn(conn):
            user_id = auth.create_user(conn, "company", data)
            conn.execute("INSERT INTO companies (admin_id, name, cin, gstin, address, city, state, website, status, "
                         "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                         (user_id, data.company_name, data.cin, data.gstin, data.address, data.city, data.state,
                          data.website, stamp(), stamp()))
        company = dict(conn.execute("SELECT * FROM companies WHERE admin_id = ?", (user_id,)).fetchone())
        user = dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        admins = admin_emails(conn)
    auth.start_session(response, user_id)
    background.add_task(pharma_insights_emails.send_company_application, user["name"], user["email"], company, admins)
    return {"user": {k: user[k] for k in ("id", "role", "name", "email", "phone", "created_at")},
            "company": company_view(company)}


@router.get("/api/company/profile")
def company_profile(user: dict = Depends(company_user)):
    with get_db() as conn:
        row = company_for(conn, user)
        counts = {"mrs": conn.execute("SELECT COUNT(*) FROM mrs m JOIN users u ON u.id = m.user_id WHERE m.company_id = ? "
                                      "AND u.is_active = 1", (row["id"],)).fetchone()[0],
                  "divisions": conn.execute("SELECT COUNT(*) FROM divisions WHERE company_id = ? AND active = 1",
                                            (row["id"],)).fetchone()[0],
                  "brands": conn.execute("SELECT COUNT(*) FROM medicines WHERE company_id = ?", (row["id"],)).fetchone()[0],
                  "meetings_pending": conn.execute("SELECT COUNT(*) FROM mr_requests WHERE company_id = ? "
                                                   "AND status = 'pending'", (row["id"],)).fetchone()[0],
                  "meetings_done": conn.execute("SELECT COUNT(*) FROM mr_requests WHERE company_id = ? "
                                                "AND status = 'completed'", (row["id"],)).fetchone()[0]}
        return {**company_view(row), "contact_name": user["name"], "phone": user["phone"], "email": user["email"],
                "counts": counts}


@router.put("/api/company/profile")
def update_company(data: CompanyUpdate, background: BackgroundTasks, user: dict = Depends(company_user)):
    with get_db() as conn:
        with write_txn(conn):
            row = company_for(conn, user)
            reverify = row["status"] == "rejected" or (row["status"] == "approved" and data.cin != row["cin"])
            conn.execute("UPDATE users SET name = ?, phone = ? WHERE id = ?", (data.name, data.phone, user["id"]))
            conn.execute("UPDATE companies SET name = ?, cin = ?, gstin = ?, address = ?, city = ?, state = ?, website = ?, "
                         "status = ?, updated_at = ? WHERE id = ?",
                         (data.company_name, data.cin, data.gstin, data.address, data.city, data.state, data.website,
                          "pending" if reverify else row["status"], stamp(), row["id"]))
        company = dict(conn.execute("SELECT * FROM companies WHERE id = ?", (row["id"],)).fetchone())
        if reverify:
            background.add_task(pharma_insights_emails.send_company_application, data.name, user["email"], company, admin_emails(conn))
        return {**company_view(company), "reverification_required": reverify}


@router.get("/api/company/divisions")
def list_divisions(user: dict = Depends(company_user)):
    with get_db() as conn:
        company = company_for(conn, user)
        rows = conn.execute("SELECT dv.*, (SELECT COUNT(*) FROM mrs m JOIN users u ON u.id = m.user_id "
                            "WHERE m.division_id = dv.id AND u.is_active = 1) AS mrs FROM divisions dv "
                            "WHERE dv.company_id = ? ORDER BY dv.active DESC, dv.name", (company["id"],)).fetchall()
        return [division_view(r) for r in rows]


@router.post("/api/company/divisions")
def add_division(data: DivisionIn, user: dict = Depends(company_user)):
    with get_db() as conn:
        company = company_for(conn, user)
        if conn.execute("SELECT 1 FROM divisions WHERE company_id = ? AND lower(name) = lower(?)",
                        (company["id"], data.name)).fetchone():
            raise HTTPException(status_code=409, detail="A division with this name already exists")
        cur = conn.execute("INSERT INTO divisions (company_id, name, care_areas, active, created_at) VALUES (?, ?, ?, ?, ?)",
                           (company["id"], data.name, "|".join(data.care_areas), int(data.active), stamp()))
        return division_view(conn.execute("SELECT * FROM divisions WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.put("/api/company/divisions/{division_id}")
def update_division(division_id: int, data: DivisionIn, user: dict = Depends(company_user)):
    with get_db() as conn:
        company = company_for(conn, user)
        if conn.execute("SELECT 1 FROM divisions WHERE company_id = ? AND lower(name) = lower(?) AND id != ?",
                        (company["id"], data.name, division_id)).fetchone():
            raise HTTPException(status_code=409, detail="A division with this name already exists")
        cur = conn.execute("UPDATE divisions SET name = ?, care_areas = ?, active = ? WHERE id = ? AND company_id = ?",
                           (data.name, "|".join(data.care_areas), int(data.active), division_id, company["id"]))
        if not cur.rowcount:
            raise HTTPException(status_code=404, detail="Division not found")
        return division_view(conn.execute("SELECT * FROM divisions WHERE id = ?", (division_id,)).fetchone())


@router.get("/api/company/mrs")
def list_mrs(user: dict = Depends(company_user)):
    with get_db() as conn:
        company = company_for(conn, user)
        rows = conn.execute(MR_SELECT + " WHERE m.company_id = ? ORDER BY u.is_active DESC, u.name", (company["id"],))
        out = []
        for r in rows.fetchall():
            stats = conn.execute("SELECT COUNT(*) AS total, SUM(status = 'completed') AS done FROM mr_requests "
                                 "WHERE mr_id = ?", (r["user_id"],)).fetchone()
            out.append({**mr_view(r), "requests": stats["total"], "meetings_done": stats["done"] or 0})
        return out


@router.post("/api/company/mrs")
def invite_mr(data: MrInvite, background: BackgroundTasks, user: dict = Depends(company_user)):
    """Create the MR's account and email them a link to set their own password."""
    with get_db() as conn:
        company = company_for(conn, user, approved=True)
        with write_txn(conn):
            check_division(conn, company["id"], data.division_id)
            invite = type("Invite", (), {"name": data.name, "email": data.email, "phone": data.phone,
                                         "password": secrets.token_urlsafe(24)})
            mr_id = auth.create_user(conn, "mr", invite)
            conn.execute("INSERT INTO mrs (user_id, company_id, division_id, employee_code, territory_city, "
                         "territory_state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (mr_id, company["id"], data.division_id, data.employee_code, data.territory_city,
                          data.territory_state, stamp()))
            audit(conn, user["id"], "mr.invited", f"user:{mr_id}", data.email)
        row = conn.execute(MR_SELECT + " WHERE m.user_id = ?", (mr_id,)).fetchone()
    token = auth.create_reset_token(mr_id)
    background.add_task(pharma_insights_emails.send_mr_invite, data.name, data.email, company["name"], token)
    return mr_view(row)


@router.put("/api/company/mrs/{mr_id}")
def update_mr(mr_id: int, data: MrUpdate, user: dict = Depends(company_user)):
    with get_db() as conn:
        company = company_for(conn, user)
        with write_txn(conn):
            if not conn.execute("SELECT 1 FROM mrs WHERE user_id = ? AND company_id = ?", (mr_id, company["id"])).fetchone():
                raise HTTPException(status_code=404, detail="MR not found")
            check_division(conn, company["id"], data.division_id)
            conn.execute("UPDATE mrs SET division_id = ?, employee_code = ?, territory_city = ?, territory_state = ? "
                         "WHERE user_id = ?", (data.division_id, data.employee_code, data.territory_city,
                                               data.territory_state, mr_id))
            conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (int(data.active), mr_id))
            if not data.active:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (mr_id,))
                conn.execute("UPDATE mr_requests SET status = 'cancelled', doctor_note = COALESCE(doctor_note, ?), "
                             "updated_at = ? WHERE mr_id = ? AND status IN ('pending', 'accepted')",
                             ("The representative left the company", stamp(), mr_id))
            audit(conn, user["id"], "mr.updated" if data.active else "mr.deactivated", f"user:{mr_id}")
        return mr_view(conn.execute(MR_SELECT + " WHERE m.user_id = ?", (mr_id,)).fetchone())


# ---------- Brands (catalogue medicines a company makes) ----------
@router.get("/api/company/brands")
def company_brands(q: str = "", user: dict = Depends(company_user)):
    with get_db() as conn:
        company = company_for(conn, user)
        if q.strip():
            like = f"%{q.strip().lower()}%"
            rows = conn.execute("SELECT * FROM medicines WHERE lower(name) LIKE ? OR lower(COALESCE(generic_name, '')) "
                                "LIKE ? ORDER BY company_id = ? DESC, name LIMIT 50", (like, like, company["id"]))
        else:
            rows = conn.execute("SELECT * FROM medicines WHERE company_id = ? ORDER BY name", (company["id"],))
        return [{k: r[k] for k in ("id", "name", "generic_name", "strength", "form", "care_area", "manufacturer")}
                | {"own": r["company_id"] == company["id"], "claimed_by_other": bool(r["company_id"])
                   and r["company_id"] != company["id"]} for r in rows.fetchall()]


@router.post("/api/company/brands/{medicine_id}")
def claim_brand(medicine_id: int, user: dict = Depends(company_user)):
    with get_db() as conn:
        company = company_for(conn, user, approved=True)
        cur = conn.execute("UPDATE medicines SET company_id = ?, manufacturer = ? WHERE id = ? "
                           "AND (company_id IS NULL OR company_id = ?)",
                           (company["id"], company["name"], medicine_id, company["id"]))
        if not cur.rowcount:
            raise HTTPException(status_code=409, detail="Another company already listed this brand. Contact support "
                                                        "if it's yours.")
        audit(conn, user["id"], "brand.claimed", f"medicine:{medicine_id}")
        return {"ok": True}


@router.delete("/api/company/brands/{medicine_id}")
def release_brand(medicine_id: int, user: dict = Depends(company_user)):
    with get_db() as conn:
        company = company_for(conn, user)
        conn.execute("UPDATE medicines SET company_id = NULL, manufacturer = NULL WHERE id = ? AND company_id = ?",
                     (medicine_id, company["id"]))
        return {"ok": True}


@router.get("/api/company/insights")
def company_insights(days: int = 90, division_id: Optional[int] = None, care_area: Optional[str] = None,
                     state: Optional[str] = None, city: Optional[str] = None, product_id: Optional[int] = None,
                     user: dict = Depends(company_user)):
    with get_db() as conn:
        company = company_for(conn, user, approved=True)
        areas = [care_area] if care_area else None
        if division_id:
            dv = conn.execute("SELECT * FROM divisions WHERE id = ? AND company_id = ?",
                              (division_id, company["id"])).fetchone()
            if not dv:
                raise HTTPException(status_code=404, detail="Division not found")
            areas = [a for a in split_areas(dv["care_areas"]) if not care_area or a == care_area] or ["-"]
        try:
            state = v.state(state) if state else None
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        doctors = scope_doctors(conn, state, v.optional_text(city))
        return build_insights(conn, company["id"], doctors, areas, period(days), product_id)


@router.get("/api/company/meetings")
def company_meetings(status: Optional[str] = None, user: dict = Depends(company_user)):
    with get_db() as conn:
        company = company_for(conn, user)
        sql, params = REQUEST_SELECT + " WHERE q.company_id = ?", [company["id"]]
        if status:
            sql += " AND q.status = ?"
            params.append(status)
        return [request_view(r) for r in conn.execute(sql + " ORDER BY q.created_at DESC LIMIT 500", params)]


@router.get("/api/mr/profile")
def mr_profile(user: dict = Depends(mr_user)):
    with get_db() as conn:
        mr = mr_for(conn, user)
        return {**mr_view(mr), "care_areas_all": ref.CARE_AREAS}


@router.get("/api/mr/doctors")
def mr_doctors(care_area: Optional[str] = None, q: Optional[str] = None, user: dict = Depends(mr_user)):
    """Approved doctors in the MR's territory (public directory details only)."""
    with get_db() as conn:
        mr = mr_for(conn, user)
        sql = ("SELECT d.user_id, d.slug, u.name, d.clinic_name, d.address, d.city, d.state, d.category, d.specialities, "
               "d.share_insights_at, d.accept_mr_meetings, "
               "(SELECT status FROM mr_requests q WHERE q.doctor_id = d.user_id AND q.mr_id = ? "
               " ORDER BY q.created_at DESC LIMIT 1) AS last_request, "
               "(SELECT MAX(updated_at) FROM mr_requests q WHERE q.doctor_id = d.user_id AND q.mr_id = ? "
               " AND q.status = 'completed') AS last_met "
               "FROM doctors d JOIN users u ON u.id = d.user_id WHERE d.status = 'approved' AND u.is_active = 1 "
               "AND d.state = ?")
        params = [user["id"], user["id"], mr["territory_state"]]
        if mr["territory_city"]:
            sql += " AND lower(d.city) = lower(?)"
            params.append(mr["territory_city"])
        if q:
            like = f"%{q.strip().lower()}%"
            sql += " AND (lower(u.name) LIKE ? OR lower(d.clinic_name) LIKE ? OR lower(d.specialities) LIKE ?)"
            params += [like, like, like]
        rows = conn.execute(sql + " ORDER BY u.name", params).fetchall()
    out = []
    for r in rows:
        specialities = split_areas(r["specialities"])
        out.append({"id": r["user_id"], "slug": r["slug"], "name": r["name"], "clinic_name": r["clinic_name"],
                    "address": r["address"], "city": r["city"], "state": r["state"],
                    "category": settings.DOCTOR_CATEGORIES.get(r["category"], {}).get("label", r["category"]),
                    "specialities": specialities, "shares_insights": bool(r["share_insights_at"]),
                    "accepts_meetings": bool(r["accept_mr_meetings"]), "last_request": r["last_request"],
                    "last_met": r["last_met"]})
    if care_area:
        wanted = {a.lower() for a in SPECIALITY_AREAS.get(care_area, [])}
        out = [d for d in out if not wanted or wanted & {s.lower() for s in d["specialities"]}
               or d["category"] in ("General physician",)]
    return out


@router.get("/api/mr/insights")
def mr_insights(days: int = 90, care_area: Optional[str] = None, product_id: Optional[int] = None,
                user: dict = Depends(mr_user)):
    with get_db() as conn:
        mr = mr_for(conn, user)
        areas, doctors = mr_scope(conn, mr)
        if care_area:
            if areas and care_area not in areas:
                raise HTTPException(status_code=403, detail="That care area isn't part of your division")
            areas = [care_area]
        return {**build_insights(conn, mr["company_id"], doctors, areas, period(days), product_id),
                "territory": {"state": mr["territory_state"], "city": mr["territory_city"]},
                "division": mr["division_name"]}


@router.get("/api/mr/requests")
def mr_requests(user: dict = Depends(mr_user)):
    with get_db() as conn:
        mr_for(conn, user)
        return [request_view(r) for r in conn.execute(REQUEST_SELECT + " WHERE q.mr_id = ? ORDER BY q.created_at DESC",
                                                      (user["id"],))]


@router.post("/api/mr/requests")
def send_mr_request(data: MrRequestIn, background: BackgroundTasks, user: dict = Depends(mr_user)):
    with get_db() as conn:
        mr = mr_for(conn, user)
        with write_txn(conn):
            doctor = conn.execute("SELECT d.*, u.name, u.email, u.is_active FROM doctors d JOIN users u ON u.id = d.user_id "
                                  "WHERE d.user_id = ?", (data.doctor_id,)).fetchone()
            if not doctor or doctor["status"] != "approved" or not doctor["is_active"]:
                raise HTTPException(status_code=404, detail="Doctor not found")
            if not doctor["accept_mr_meetings"]:
                raise HTTPException(status_code=403, detail="This doctor isn't accepting meeting requests")
            if conn.execute("SELECT 1 FROM mr_requests WHERE mr_id = ? AND doctor_id = ? AND status = 'pending'",
                            (user["id"], data.doctor_id)).fetchone():
                raise HTTPException(status_code=409, detail="You already have a request waiting with this doctor")
            month_ago = stamp(settings.now() - timedelta(days=30))
            recent = conn.execute("SELECT COUNT(*) FROM mr_requests WHERE mr_id = ? AND doctor_id = ? AND created_at > ?",
                                  (user["id"], data.doctor_id, month_ago)).fetchone()[0]
            if recent >= settings.MR_REQUESTS_PER_MONTH:
                raise HTTPException(status_code=429, detail="You've reached the monthly limit of requests to this doctor")
            cur = conn.execute("INSERT INTO mr_requests (mr_id, doctor_id, company_id, division_id, purpose, products, "
                               "preferred_date, preferred_time, status, created_at, updated_at) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                               (user["id"], data.doctor_id, mr["company_id"], mr["division_id"], data.purpose,
                                data.products, data.preferred_date, data.preferred_time, stamp(), stamp()))
        row = conn.execute(REQUEST_SELECT + " WHERE q.id = ?", (cur.lastrowid,)).fetchone()
    background.add_task(pharma_insights_emails.send_mr_request_to_doctor, dict(row))
    return request_view(row)


@router.post("/api/mr/requests/{request_id}/cancel")
def cancel_mr_request(request_id: int, user: dict = Depends(mr_user)):
    with get_db() as conn:
        mr_for(conn, user)
        cur = conn.execute("UPDATE mr_requests SET status = 'cancelled', updated_at = ? WHERE id = ? AND mr_id = ? "
                           "AND status IN ('pending', 'accepted')", (stamp(), request_id, user["id"]))
        if not cur.rowcount:
            raise HTTPException(status_code=400, detail="This request can't be cancelled")
        return request_view(conn.execute(REQUEST_SELECT + " WHERE q.id = ?", (request_id,)).fetchone())


# ---------- Doctor side: settings and requests inbox ----------
@router.put("/api/doctor/settings")
def doctor_settings(data: DoctorSettings, user: dict = Depends(doctor_user)):
    with get_db() as conn:
        with write_txn(conn):
            row = conn.execute("SELECT share_insights_at, accept_mr_meetings FROM doctors WHERE user_id = ?",
                               (user["id"],)).fetchone()
            sharing = bool(row["share_insights_at"])
            conn.execute("UPDATE doctors SET share_insights_at = ?, accept_mr_meetings = ? WHERE user_id = ?",
                         ((row["share_insights_at"] or stamp()) if data.share_insights else None,
                          int(data.accept_mr_meetings), user["id"]))
            if data.share_insights != sharing:
                audit(conn, user["id"], "insights.consent_given" if data.share_insights else "insights.consent_withdrawn",
                      f"user:{user['id']}")
            if data.accept_mr_meetings != bool(row["accept_mr_meetings"]):
                audit(conn, user["id"], "mr_meetings.on" if data.accept_mr_meetings else "mr_meetings.off",
                      f"user:{user['id']}")
        row = conn.execute("SELECT share_insights_at, accept_mr_meetings FROM doctors WHERE user_id = ?",
                           (user["id"],)).fetchone()
        return {"share_insights": bool(row["share_insights_at"]), "share_insights_at": row["share_insights_at"],
                "accept_mr_meetings": bool(row["accept_mr_meetings"])}


@router.get("/api/doctor/requests")
def doctor_requests(user: dict = Depends(doctor_user)):
    """Everything waiting on the doctor: new bookings, patient messages, refills and MR meeting requests."""
    now = settings.now()
    today = now.date().isoformat()
    with get_db() as conn:
        def rows(sql, *params):
            return [dict(r) for r in conn.execute(sql, params)]
        new_bookings = rows(
            "SELECT a.id, a.reference, a.appointment_date, a.appointment_time, a.service_name, a.reason, a.created_at, "
            "u.name AS patient_name, u.phone AS patient_phone, "
            "(SELECT COUNT(*) FROM appointments p WHERE p.patient_id = a.patient_id AND p.doctor_id = a.doctor_id "
            " AND p.status IN ('completed', 'no_show')) AS previous_visits "
            "FROM appointments a JOIN users u ON u.id = a.patient_id WHERE a.doctor_id = ? AND a.status = 'confirmed' "
            "AND a.appointment_date >= ? AND a.created_at >= ? ORDER BY a.appointment_date, a.appointment_time",
            user["id"], today, stamp(now - timedelta(days=7)))
        awaiting_reply = rows(
            "SELECT a.id, a.reference, a.appointment_date, a.appointment_time, u.name AS patient_name, m.body, "
            "m.created_at FROM messages m JOIN appointments a ON a.id = m.appointment_id "
            "JOIN users u ON u.id = a.patient_id WHERE a.doctor_id = ? AND m.created_at >= ? AND m.author_role = 'patient' "
            "AND m.id = (SELECT MAX(id) FROM messages WHERE appointment_id = a.id) ORDER BY m.created_at DESC",
            user["id"], stamp(now - timedelta(days=30)))
        home_visits = rows(
            "SELECT a.id, a.reference, a.appointment_date, a.appointment_time, a.service_name, a.reason, a.status, a.visit_address, "
            "a.fee, a.travel_fee, a.duration, u.name AS patient_name, u.phone AS patient_phone FROM appointments a "
            "JOIN users u ON u.id = a.patient_id WHERE a.doctor_id = ? AND a.status IN ('requested', 'awaiting_patient') "
            "ORDER BY a.appointment_date, a.appointment_time", user["id"])
        refills = rows(
            "SELECT r.id, r.reference, r.refill_number, r.created_at, p.reference AS refill_of, u.name AS patient_name, "
            "p.refills_allowed, p.refills_used FROM prescriptions r JOIN prescriptions p ON p.id = r.parent_id "
            "JOIN users u ON u.id = r.patient_id WHERE r.doctor_id = ? AND r.created_at >= ? ORDER BY r.created_at DESC",
            user["id"], stamp(now - timedelta(days=30)))
        mr = [request_view(r) for r in conn.execute(
            REQUEST_SELECT + " WHERE q.doctor_id = ? ORDER BY CASE q.status WHEN 'pending' THEN 0 WHEN 'accepted' THEN 1 "
            "ELSE 2 END, q.created_at DESC LIMIT 100", (user["id"],))]
        settings_row = conn.execute("SELECT share_insights_at, accept_mr_meetings FROM doctors WHERE user_id = ?",
                                    (user["id"],)).fetchone()
    return {"new_bookings": new_bookings, "awaiting_reply": awaiting_reply, "refills": refills, "mr_requests": mr,
            "home_visits": home_visits,
            "counts": {"new_bookings": len(new_bookings), "awaiting_reply": len(awaiting_reply),
                       "home_visits": sum(1 for h in home_visits if h["status"] == "requested"),
                       "refills": len(refills), "mr_pending": sum(1 for r in mr if r["status"] == "pending")},
            "settings": {"share_insights": bool(settings_row["share_insights_at"]),
                         "accept_mr_meetings": bool(settings_row["accept_mr_meetings"])}}


@router.post("/api/doctor/mr-requests/{request_id}/{action}")
def decide_mr_request(request_id: int, action: Literal["accept", "decline", "done"], data: MrDecision,
                      background: BackgroundTasks, user: dict = Depends(doctor_user)):
    before, after = MR_TRANSITIONS[action]
    if action == "accept" and not data.scheduled_for:
        raise HTTPException(status_code=400, detail="Pick a meeting date and time")
    if action == "accept" and data.scheduled_for < settings.now().isoformat()[:16]:
        raise HTTPException(status_code=400, detail="Pick a time in the future")
    with get_db() as conn:
        with write_txn(conn):
            cur = conn.execute("UPDATE mr_requests SET status = ?, doctor_note = COALESCE(?, doctor_note), "
                               "scheduled_for = COALESCE(?, scheduled_for), updated_at = ? "
                               "WHERE id = ? AND doctor_id = ? AND status = ?",
                               (after, data.note, data.scheduled_for if action == "accept" else None, stamp(),
                                request_id, user["id"], before))
            if not cur.rowcount:
                raise HTTPException(status_code=400, detail=f"Only {before} requests can be marked {after}")
        row = conn.execute(REQUEST_SELECT + " WHERE q.id = ?", (request_id,)).fetchone()
    if action != "done":
        background.add_task(pharma_insights_emails.send_mr_request_update, dict(row))
    return request_view(row)


# ---------- Super admin: companies ----------
@router.get("/api/admin/companies")
def admin_companies(status: Optional[str] = None, user: dict = Depends(admin_user)):
    sql = ("SELECT c.*, u.name AS contact_name, u.email, u.phone, "
           "(SELECT COUNT(*) FROM mrs m WHERE m.company_id = c.id) AS mrs, "
           "(SELECT COUNT(*) FROM divisions dv WHERE dv.company_id = c.id) AS divisions, "
           "(SELECT COUNT(*) FROM mr_requests q WHERE q.company_id = c.id) AS meetings "
           "FROM companies c JOIN users u ON u.id = c.admin_id")
    params = []
    if status:
        sql += " WHERE c.status = ?"
        params.append(status)
    with get_db() as conn:
        return [{**company_view(r), "contact_name": r["contact_name"], "email": r["email"], "phone": r["phone"],
                 "mrs": r["mrs"], "divisions": r["divisions"], "meetings": r["meetings"]}
                for r in conn.execute(sql + " ORDER BY c.status = 'pending' DESC, c.created_at DESC", params)]


@router.post("/api/admin/companies/{company_id}/status")
def admin_company_status(company_id: int, data: CompanyStatus, background: BackgroundTasks,
                         user: dict = Depends(admin_user)):
    with get_db() as conn:
        row = conn.execute("SELECT c.*, u.name AS contact_name, u.email FROM companies c JOIN users u ON u.id = c.admin_id "
                           "WHERE c.id = ?", (company_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Company not found")
        conn.execute("UPDATE companies SET status = ?, status_reason = ?, approved_at = CASE WHEN ? = 'approved' "
                     "THEN COALESCE(approved_at, ?) ELSE approved_at END, updated_at = ? WHERE id = ?",
                     (data.status, data.reason, data.status, stamp(), stamp(), company_id))
        if data.status != "approved":
            # Open meeting requests can't go ahead while the company is off the platform
            conn.execute("UPDATE mr_requests SET status = 'cancelled', updated_at = ? WHERE company_id = ? "
                         "AND status IN ('pending', 'accepted')", (stamp(), company_id))
        audit(conn, user["id"], f"company.{data.status}", f"company:{company_id}", data.reason)
    background.add_task(pharma_insights_emails.send_company_status, row["contact_name"], row["email"], row["name"], data.status,
                        data.reason)
    return {"ok": True}


# Imported last, so slices can depend on each other in any order
from features.pharma_insights.logic import admin_emails, build_insights, check_division, company_for, company_view, division_view, mr_for, mr_scope, MR_SELECT, MR_TRANSITIONS, mr_view, period, REQUEST_SELECT, request_view, scope_doctors, SPECIALITY_AREAS, split_areas  # noqa: E402
from features.pharma_insights import emails as pharma_insights_emails  # noqa: E402

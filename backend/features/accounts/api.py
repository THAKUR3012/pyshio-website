"""Accounts: sign-up, login, password reset, profile, home location, consent, data export and account closure.

This file: HTTP endpoints and request models.
"""
import json
import secrets
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal, Optional

from shared import auth, reference_data as ref, settings, validators as v
from shared.auth import patient_user
from shared.common import audit, stamp
from shared.db import get_db, write_txn
from shared.locations import LocationFields

from features.accounts.logic import _lower_email

router = APIRouter()


# ---------- Request models ----------
class PatientRegister(BaseModel):
    name: str
    email: str
    phone: str
    password: str

    _name = field_validator("name")(v.name)
    _email = field_validator("email")(v.email)
    _phone = field_validator("phone")(v.phone)
    _password = field_validator("password")(v.password)


class PatientSignup(PatientRegister):
    # Prescriptions are shared automatically with nearby verified pharmacies; patients agree at sign-up
    share_consent: bool = Field(default=False, validate_default=True)

    @field_validator("share_consent")
    @classmethod
    def _consent(cls, value):
        if not value:
            raise ValueError("Please agree to sharing prescriptions with nearby pharmacies to get the best prices")
        return value


class LoginRequest(BaseModel):
    email: str
    password: str

    _email = field_validator("email")(_lower_email)


class ForgotRequest(BaseModel):
    email: str

    _email = field_validator("email")(_lower_email)


class ResetRequest(BaseModel):
    token: str
    password: str

    _password = field_validator("password")(v.password)


class ChangePassword(BaseModel):
    current_password: str
    new_password: str

    _password = field_validator("new_password")(v.password)


class AccountUpdate(BaseModel):
    name: str
    phone: str
    dob: Optional[str] = None
    sex: Optional[Literal["M", "F", "O"]] = None

    _name = field_validator("name")(v.name)
    _phone = field_validator("phone")(v.phone)

    @field_validator("dob")
    @classmethod
    def _dob(cls, value):
        value = v.optional_text(value)
        if value is None:
            return None
        try:
            d = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("Enter a valid date of birth")
        if d > settings.now().date() or d.year < settings.now().year - 120:
            raise ValueError("Enter a valid date of birth")
        return value


class ConsentIn(BaseModel):
    share: bool


class DeleteAccountIn(BaseModel):
    password: str


# ---------- Auth ----------
@router.get("/api/config")
def get_config():
    return {
        "platform_name": settings.PLATFORM_NAME,
        "specialities": settings.SPECIALITIES,
        "doctor_categories": settings.DOCTOR_CATEGORIES,
        "prescribing_categories": settings.PRESCRIBING_CATEGORIES,
        "radius_km": settings.NEARBY_RADIUS_KM,
        "wider_radii_km": settings.WIDER_RADII_KM,
        "medicine_forms": medicines.FORMS,
        "support_email": settings.SUPPORT_EMAIL,
        "support_phone": settings.SUPPORT_PHONE,
        "order_accept_minutes": settings.ORDER_ACCEPT_MINUTES,
        "online_payments": settings.online_payments_enabled(),
        "razorpay_key_id": settings.RAZORPAY_KEY_ID if settings.online_payments_enabled() else None,
        "max_advance_days": settings.MAX_ADVANCE_DAYS,
        "refund_cutoff_hours": settings.REFUND_CUTOFF_HOURS,
        "payment_hold_minutes": settings.PAYMENT_HOLD_MINUTES,
        "states": ref.INDIAN_STATES,
        "care_areas": ref.CARE_AREAS,
        "max_refills": settings.MAX_REFILLS,
        "min_group_size": settings.MIN_GROUP_SIZE,
        "test_categories": labs.TEST_CATEGORIES,
        "max_upload_mb": settings.MAX_UPLOAD_MB,
    }


@router.get("/api/auth/me")
def me(user: Optional[dict] = Depends(auth.current_user)):
    return me_payload(user) if user else {"user": None}


@router.post("/api/auth/register")
def register_patient(data: PatientSignup, response: Response, request: Request, background: BackgroundTasks):
    registration_throttle(request)
    with get_db() as conn:
        with write_txn(conn):
            user_id = _create_user(conn, "patient", data, share_consent_at=stamp())
        user = dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
    auth.start_session(response, user_id)
    background.add_task(accounts_emails.send_welcome, user["name"], user["email"])
    return me_payload(user)


@router.post("/api/auth/login")
def login(data: LoginRequest, response: Response, request: Request):
    user = auth.authenticate(data.email, data.password, client_ip(request))
    auth.start_session(response, user["id"])
    return me_payload(user)


@router.post("/api/auth/logout")
def logout(response: Response, request: Request):
    auth.end_session(response, request.cookies.get(auth.COOKIE_NAME))
    return {"ok": True}


@router.post("/api/auth/forgot")
def forgot_password(data: ForgotRequest, background: BackgroundTasks, request: Request):
    with get_db() as conn:
        # Quietly cap reset emails so nobody can flood an inbox (the answer is the same either way)
        flooded = auth.rate_limited(conn, f"forgot-ip:{client_ip(request)}", 20, 60) or \
            auth.rate_limited(conn, f"forgot:{data.email}", 3, 60)
        user = conn.execute("SELECT * FROM users WHERE email = ? AND is_active = 1 AND deleted_at IS NULL",
                            (data.email,)).fetchone()
    if user and not flooded:
        token = auth.create_reset_token(user["id"])
        background.add_task(accounts_emails.send_password_reset, user["email"], user["name"], token)
    # Same answer either way so nobody can probe which emails are registered
    return {"message": "If that email is registered, a reset link is on its way."}


@router.post("/api/auth/reset")
def do_reset(data: ResetRequest):
    auth.reset_password(data.token, data.password)
    return {"message": "Password updated. Please log in."}


@router.patch("/api/auth/me")
def update_me(data: AccountUpdate, user: dict = Depends(auth.require())):
    with get_db() as conn:
        conn.execute("UPDATE users SET name = ?, phone = ?, dob = ?, sex = ? WHERE id = ?",
                     (data.name, data.phone, data.dob, data.sex, user["id"]))
        return me_payload(dict(conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()))


@router.post("/api/auth/change-password")
def change_password(data: ChangePassword, request: Request, user: dict = Depends(auth.require())):
    if not auth.verify_password(data.current_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    with get_db() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (auth.hash_password(data.new_password), user["id"]))
    auth.end_other_sessions(user["id"], request.cookies.get(auth.COOKIE_NAME))
    return {"message": "Password changed. You've been signed out on other devices."}


@router.post("/api/auth/logout-all")
def logout_all(request: Request, user: dict = Depends(auth.require())):
    auth.end_other_sessions(user["id"], request.cookies.get(auth.COOKIE_NAME))
    return {"message": "Signed out on all other devices"}


# ---------- Privacy (DPDP): consent, export, deletion ----------
@router.put("/api/my/consent")
def set_consent(data: ConsentIn, user: dict = Depends(auth.require("patient"))):
    with get_db() as conn:
        conn.execute("UPDATE users SET share_consent_at = ? WHERE id = ?", (stamp() if data.share else None, user["id"]))
        audit(conn, user["id"], "consent.given" if data.share else "consent.withdrawn", f"user:{user['id']}")
    return {"share_consent": data.share}


@router.get("/api/my/data")
def export_my_data(user: dict = Depends(auth.require())):
    """Everything we hold about the signed-in user, as a JSON download."""
    with get_db() as conn:
        def all_rows(sql, *params):
            return [dict(r) for r in conn.execute(sql, params)]
        me_row = dict(conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone())
        me_row.pop("password_hash", None)
        data = {"exported_at": stamp(), "account": me_row}
        if user["role"] == "patient":
            data.update(
                appointments=all_rows("SELECT * FROM appointments WHERE patient_id = ?", user["id"]),
                messages=all_rows("SELECT m.* FROM messages m JOIN appointments a ON a.id = m.appointment_id "
                                  "WHERE a.patient_id = ?", user["id"]),
                prescriptions=all_rows("SELECT * FROM prescriptions WHERE patient_id = ?", user["id"]),
                prescription_items=all_rows("SELECT i.* FROM prescription_items i JOIN prescriptions r ON "
                                            "r.id = i.prescription_id WHERE r.patient_id = ?", user["id"]),
                medicine_orders=all_rows("SELECT * FROM medicine_orders WHERE patient_id = ?", user["id"]),
                order_items=all_rows("SELECT oi.* FROM order_items oi JOIN medicine_orders o ON o.id = oi.order_id "
                                     "WHERE o.patient_id = ?", user["id"]),
                reviews=all_rows("SELECT * FROM reviews WHERE patient_id = ?", user["id"]),
                lab_bookings=all_rows("SELECT * FROM lab_bookings WHERE patient_id = ?", user["id"]),
                health_records=all_rows("SELECT id, kind, title, record_date, file_name, notes, created_at FROM "
                                        "health_records WHERE patient_id = ? AND deleted_at IS NULL", user["id"]),
                test_results=all_rows("SELECT rv.* FROM record_values rv JOIN health_records h ON h.id = rv.record_id "
                                      "WHERE h.patient_id = ? AND h.deleted_at IS NULL", user["id"]),
                other_medicines=all_rows("SELECT * FROM patient_medicines WHERE patient_id = ?", user["id"]),
                uploaded_prescriptions=all_rows("SELECT id, reference, note, status, created_at FROM rx_uploads WHERE patient_id = ?",
                                                user["id"]),
                upload_orders=all_rows("SELECT * FROM upload_orders WHERE patient_id = ?", user["id"]),
                family_members=all_rows("SELECT * FROM family_members WHERE patient_id = ?", user["id"]),
                dose_log=all_rows("SELECT source, source_id, name, dose_date, slot, status, logged_at FROM dose_logs "
                                  "WHERE patient_id = ?", user["id"]))
        data["notifications"] = all_rows("SELECT title, body, link, created_at, read_at FROM notifications WHERE user_id = ?",
                                         user["id"])
        for row in data.get("appointments", []) + data.get("medicine_orders", []):
            row.pop("razorpay_order_id", None)
    return Response(content=json.dumps(data, indent=2, default=str), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="my-pyshio-data.json"'})


@router.post("/api/my/delete")
def delete_my_account(data: DeleteAccountIn, response: Response, background: BackgroundTasks,
                      user: dict = Depends(auth.require("patient"))):
    """Close the account: cancel what's pending, then strip personal details. Medical/financial records are kept
    in anonymised form because the law requires them to be retained."""
    if not auth.verify_password(data.password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Password is incorrect")
    reason = "The patient closed their account."
    wind_down_appointments("patient_id = ?", (user["id"],), reason, background)
    medicine_orders_logic.wind_down_patient_orders(user["id"], reason, background)
    labs.wind_down_patient_bookings(user["id"], reason, background)
    prescription_uploads.wind_down_patient(user["id"], background)
    with get_db() as conn:
        health_records_logic.delete_patient_files(conn, user["id"])
        conn.execute("UPDATE users SET name = 'Deleted user', email = ?, phone = NULL, home_address = NULL, home_lat = NULL, "
                     "home_lng = NULL, dob = NULL, sex = NULL, share_consent_at = NULL, is_active = 0, deleted_at = ?, "
                     "blood_group = NULL, allergies = NULL, chronic_conditions = NULL, emergency_name = NULL, "
                     "emergency_phone = NULL, remind_times = NULL, dose_email_digest = 0, password_hash = ? WHERE id = ?",
                     (f"deleted-{user['id']}@deleted.invalid", stamp(), auth.hash_password(secrets.token_urlsafe(16)),
                      user["id"]))
        conn.execute("UPDATE prescriptions SET status = 'cancelled' WHERE patient_id = ? AND status = 'active'", (user["id"],))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
        # Family profiles keep only a placeholder name (old prescriptions point at them); reminders and alerts go
        conn.execute("UPDATE family_members SET name = 'Family member', dob = NULL, sex = NULL, blood_group = NULL, "
                     "allergies = NULL, conditions = NULL, deleted_at = COALESCE(deleted_at, ?) WHERE patient_id = ?",
                     (stamp(), user["id"]))
        conn.execute("DELETE FROM dose_logs WHERE patient_id = ?", (user["id"],))
        conn.execute("DELETE FROM notifications WHERE user_id = ?", (user["id"],))
        audit(conn, user["id"], "account.deleted", f"user:{user['id']}")
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    background.add_task(accounts_emails.send_account_closed, user["name"], user["email"])
    return {"message": "Your account has been closed"}


class HomeLocation(LocationFields):
    address: Optional[str] = Field(default=None, max_length=300)
    city: Optional[str] = Field(default=None, max_length=60)
    state: Optional[str] = None

    _address = field_validator("address", "city")(v.optional_text)
    _state = field_validator("state")(v.state)

    @model_validator(mode="after")
    def _default_state(self):
        if self.city:
            self.city = self.city.title()
        self.state = self.state or ref.state_for_city(self.city)
        return self


@router.put("/api/my/location")
def set_home(data: HomeLocation, background: BackgroundTasks, user: dict = Depends(patient_user)):
    """Save the patient's home. Active prescriptions move with them so nearby pharmacies stay relevant."""
    moved = []
    with get_db() as conn:
        with write_txn(conn):
            conn.execute("UPDATE users SET home_lat = ?, home_lng = ?, home_address = COALESCE(?, home_address), "
                         "city = COALESCE(?, city), state = COALESCE(?, state) WHERE id = ?",
                         (data.lat, data.lng, data.address, data.city, data.state, user["id"]))
            for row in conn.execute(RX_SELECT + " WHERE r.patient_id = ? AND r.status = 'active' AND r.valid_until > ?",
                                    (user["id"], stamp())).fetchall():
                if row["lat"] == data.lat and row["lng"] == data.lng:
                    continue
                medicines_left = {i["id"] for i in rx_items(conn, row["id"])} - ordered_item_ids(conn, row["id"])
                tests_left = {t["id"] for t in labs.rx_tests(conn, row["id"])} - labs.booked_test_ids(conn, row["id"])
                if not (medicines_left or tests_left):
                    continue  # everything already ordered / booked
                conn.execute("UPDATE prescriptions SET lat = ?, lng = ?, location_source = 'home' WHERE id = ?",
                             (data.lat, data.lng, row["id"]))
                share = row["share_consent_at"]
                added = distribute(conn, row["id"], data.lat, data.lng, row["radius_km"]) if share and medicines_left else []
                labs_added = labs.distribute_tests(conn, row["id"], data.lat, data.lng, row["radius_km"]) \
                    if share and tests_left else []
                moved.append((row["id"], added, labs_added))
        for rx_id, added, labs_added in moved:
            email_new_requests(background, conn, rx_id, added)
            labs.email_lab_requests(background, conn, rx_id, labs_added)
    return {"lat": data.lat, "lng": data.lng, "address": data.address or user.get("home_address"),
            "city": data.city or user.get("city"), "state": data.state or user.get("state"),
            "prescriptions_moved": len(moved)}


# Imported last, so slices can depend on each other in any order
from features.accounts.logic import _create_user, client_ip, me_payload, registration_throttle  # noqa: E402
from features.appointments.logic import wind_down_appointments  # noqa: E402
from features.pharmacy_offers.logic import distribute, email_new_requests  # noqa: E402
from features.prescriptions.logic import ordered_item_ids, rx_items, RX_SELECT  # noqa: E402
from features.accounts import emails as accounts_emails  # noqa: E402
from features.health_records import logic as health_records_logic  # noqa: E402
from features.labs import logic as labs  # noqa: E402
from features.medicine_orders import logic as medicine_orders_logic  # noqa: E402
from features.medicines import logic as medicines  # noqa: E402
from features.prescription_uploads import logic as prescription_uploads  # noqa: E402

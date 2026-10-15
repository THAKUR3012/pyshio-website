"""Pharmacies: registration, profile, nearby search, stats and admin approval.

This file: HTTP endpoints and request models.
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import Field, field_validator, model_validator
from typing import Optional

from shared import auth, geo, mailer, reference_data as ref, settings, validators as v
from shared.auth import admin_user, pharmacy_user
from shared.common import audit, paise, stamp
from shared.db import get_db, write_txn
from shared.locations import LocationFields
from shared.partners import PartnerStatus


router = APIRouter()


class PharmacyFields(LocationFields):
    store_name: str
    drug_licence_no: str
    pharmacist_name: str
    pharmacist_reg_no: str
    gstin: Optional[str] = None
    address: str
    city: str
    pincode: Optional[str] = None
    opening_hours: Optional[str] = Field(default=None, max_length=200)
    offers_delivery: bool = False
    delivery_radius_km: float = Field(default=3, ge=0.5, le=25)
    delivery_fee: float = Field(default=0, ge=0, le=1000)
    accepts_online_payment: bool = False
    # "We give X% off MRP": prices any catalogue medicine with a known MRP even without a price list
    flat_discount_percent: Optional[float] = Field(default=None, ge=0, le=90)
    pickup_ready_minutes: int = Field(default=15, ge=0, le=1440)
    delivery_minutes: int = Field(default=60, ge=5, le=1440)
    state: Optional[str] = None

    _store = field_validator("store_name")(v.required_text("Store name", 120))
    _licence = field_validator("drug_licence_no")(v.required_text("Drug licence number", 120))
    _pharmacist = field_validator("pharmacist_name")(v.required_text("Pharmacist name", 100))
    _pharmacist_reg = field_validator("pharmacist_reg_no")(v.required_text("Pharmacist registration number", 60))
    _gstin = field_validator("gstin")(v.optional_text)
    _address = field_validator("address")(v.required_text("Address", 300))
    _pincode = field_validator("pincode")(v.pincode)
    _hours = field_validator("opening_hours")(v.optional_text)
    _state = field_validator("state")(v.state)

    @field_validator("city")
    @classmethod
    def _city(cls, value):
        return v.required_text("City", 60)(value).title()

    @model_validator(mode="after")
    def _default_state(self):
        self.state = self.state or ref.state_for_city(self.city)
        return self


class PharmacyRegister(PharmacyFields):
    name: str
    email: str
    phone: str
    password: str

    _name = field_validator("name")(v.name)
    _email = field_validator("email")(v.email)
    _phone = field_validator("phone")(v.phone)
    _password = field_validator("password")(v.password)


class PharmacyUpdate(PharmacyFields):
    name: str
    phone: str

    _name = field_validator("name")(v.name)
    _phone = field_validator("phone")(v.phone)


@router.post("/api/auth/register-pharmacy")
def register_pharmacy(data: PharmacyRegister, response: Response, background: BackgroundTasks):
    with get_db() as conn:
        with write_txn(conn):
            user_id = auth.create_user(conn, "pharmacy", data)
            conn.execute(
                """INSERT INTO pharmacies (user_id, slug, store_name, drug_licence_no, pharmacist_name, pharmacist_reg_no,
                   gstin, address, city, pincode, lat, lng, opening_hours, offers_delivery, delivery_radius_km,
                   delivery_fee_paise, accepts_online_payment, status, created_at, updated_at,
                   flat_discount_percent, pickup_ready_minutes, delivery_minutes, state)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)""",
                (user_id, make_slug(conn, data.store_name, data.city), data.store_name, data.drug_licence_no,
                 data.pharmacist_name, data.pharmacist_reg_no, data.gstin, data.address, data.city, data.pincode,
                 data.lat, data.lng, data.opening_hours, int(data.offers_delivery), data.delivery_radius_km,
                 paise(data.delivery_fee), int(data.accepts_online_payment), stamp(), stamp(),
                 data.flat_discount_percent, data.pickup_ready_minutes, data.delivery_minutes, data.state))
        pharmacy = dict(get_pharmacy(conn, user_id))
        admins = [r["email"] for r in conn.execute("SELECT email FROM users WHERE role = 'admin' AND is_active = 1")]
        user = dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
    auth.start_session(response, user_id)
    background.add_task(pharmacies_emails.send_pharmacy_application, pharmacy, admins)
    return {"user": {k: user[k] for k in ("id", "role", "name", "email", "phone", "created_at")},
            "pharmacy": {"status": "pending", "slug": pharmacy["slug"], "store_name": pharmacy["store_name"]}}


@router.get("/api/pharmacy/profile")
def my_pharmacy(user: dict = Depends(pharmacy_user)):
    with get_db() as conn:
        return pharmacy_private(get_pharmacy(conn, user["id"]))


@router.put("/api/pharmacy/profile")
def update_pharmacy(data: PharmacyUpdate, background: BackgroundTasks, user: dict = Depends(pharmacy_user)):
    with get_db() as conn:
        with write_txn(conn):
            current = get_pharmacy(conn, user["id"])
            # A new licence or pharmacist must be verified again
            reverify = current["status"] == "approved" and (
                data.drug_licence_no != current["drug_licence_no"] or data.pharmacist_reg_no != current["pharmacist_reg_no"])
            reverify = reverify or current["status"] == "rejected"  # fixed profile goes back into the queue
            conn.execute("UPDATE users SET name = ?, phone = ? WHERE id = ?", (data.name, data.phone, user["id"]))
            conn.execute(
                """UPDATE pharmacies SET store_name = ?, drug_licence_no = ?, pharmacist_name = ?, pharmacist_reg_no = ?,
                   gstin = ?, address = ?, city = ?, pincode = ?, lat = ?, lng = ?, opening_hours = ?, offers_delivery = ?,
                   delivery_radius_km = ?, delivery_fee_paise = ?, accepts_online_payment = ?, status = ?, updated_at = ?,
                   flat_discount_percent = ?, pickup_ready_minutes = ?, delivery_minutes = ?, state = ?
                   WHERE user_id = ?""",
                (data.store_name, data.drug_licence_no, data.pharmacist_name, data.pharmacist_reg_no, data.gstin,
                 data.address, data.city, data.pincode, data.lat, data.lng, data.opening_hours, int(data.offers_delivery),
                 data.delivery_radius_km, paise(data.delivery_fee), int(data.accepts_online_payment),
                 "pending" if reverify else current["status"], stamp(), data.flat_discount_percent,
                 data.pickup_ready_minutes, data.delivery_minutes, data.state, user["id"]))
        row = get_pharmacy(conn, user["id"])
        if reverify:
            admins = [r["email"] for r in conn.execute("SELECT email FROM users WHERE role = 'admin' AND is_active = 1")]
            background.add_task(pharmacies_emails.send_pharmacy_application, dict(row), admins)
        return {**pharmacy_private(row), "reverification_required": reverify}


@router.get("/api/pharmacies/nearby")
def pharmacies_nearby(lat: float, lng: float, radius: float = Query(None, gt=0, le=25)):
    try:
        geo.check_point(lat, lng)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    with get_db() as conn:
        return [pharmacy_public(r, d) for d, r in nearby_pharmacies(conn, lat, lng, radius or settings.NEARBY_RADIUS_KM)]


@router.get("/api/pharmacy/stats")
def pharmacy_stats(user: dict = Depends(pharmacy_user)):
    month_start = settings.now().date().replace(day=1).isoformat()
    with get_db() as conn:
        def one(sql, *params):
            return conn.execute(sql, params).fetchone()[0]
        return {
            "open_requests": one("SELECT COUNT(*) FROM rx_requests q JOIN prescriptions r ON r.id = q.prescription_id "
                                 "WHERE q.pharmacy_id = ? AND q.status = 'open' AND r.status = 'active' AND r.valid_until > ?",
                                 user["id"], stamp()),
            "open_orders": one("SELECT COUNT(*) FROM medicine_orders WHERE pharmacy_id = ? AND status IN "
                               "('placed', 'accepted', 'ready', 'out_for_delivery')", user["id"]),
            "completed_month": one("SELECT COUNT(*) FROM medicine_orders WHERE pharmacy_id = ? AND status = 'completed' "
                                   "AND created_at >= ?", user["id"], month_start),
            "sales_month_paise": one("SELECT COALESCE(SUM(total_paise), 0) FROM medicine_orders WHERE pharmacy_id = ? "
                                     "AND status = 'completed' AND created_at >= ?", user["id"], month_start),
            "price_list_items": one("SELECT COUNT(*) FROM pharmacy_stock WHERE pharmacy_id = ?", user["id"]),
            "settlement": settlement_for(conn, "pharmacy", user["id"]),
        }


# ---------- Admin ----------
@router.get("/api/admin/pharmacies")
def admin_pharmacies(status: Optional[str] = None, user: dict = Depends(admin_user)):
    sql = PHARMACY_SELECT + (" WHERE p.status = ?" if status else "") + " ORDER BY p.created_at DESC"
    with get_db() as conn:
        return [pharmacy_private(r) for r in conn.execute(sql, (status,) if status else ())]


@router.post("/api/admin/pharmacies/{pharmacy_id}/status")
def admin_pharmacy_status(pharmacy_id: int, data: PartnerStatus, background: BackgroundTasks,
                          user: dict = Depends(admin_user)):
    if data.status in ("rejected", "suspended") and not data.reason:
        raise HTTPException(status_code=400, detail="Please give a reason; it's emailed to the pharmacy")
    with get_db() as conn:
        get_pharmacy(conn, pharmacy_id)
        conn.execute("UPDATE pharmacies SET status = ?, status_reason = ?, approved_at = COALESCE(?, approved_at), "
                     "updated_at = ? WHERE user_id = ?",
                     (data.status, data.reason, stamp() if data.status == "approved" else None, stamp(), pharmacy_id))
        audit(conn, user["id"], f"pharmacy.{data.status}", f"pharmacy:{pharmacy_id}", data.reason)
        row = get_pharmacy(conn, pharmacy_id)
    if data.status in ("rejected", "suspended"):
        wind_down_pharmacy(pharmacy_id, f"The pharmacy is no longer available on {settings.PLATFORM_NAME}", background)
    background.add_task(mailer.send_partner_status, row["name"], row["email"], data.status, data.reason,
                        "/pharmacy", "pharmacy listing")
    return pharmacy_private(row)


# Imported last, so slices can depend on each other in any order
from features.pharmacies.logic import get_pharmacy, make_slug, nearby_pharmacies, pharmacy_private, pharmacy_public, PHARMACY_SELECT, wind_down_pharmacy  # noqa: E402
from features.settlements.logic import settlement_for  # noqa: E402
from features.pharmacies import emails as pharmacies_emails  # noqa: E402

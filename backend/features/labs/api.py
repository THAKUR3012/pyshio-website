"""Labs: registration, test catalogue and prices, tests sent from prescriptions, bookings and reports.

This file: HTTP endpoints and request models.
"""
import json
import uuid
from datetime import timedelta
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Literal, Optional

from shared import auth, geo, locations, mailer, partners, reference_data as ref, settings, validators as v
from shared.auth import admin_user, lab_user, patient_user
from shared.common import audit, new_reference, paise, stamp
from shared.db import get_db, write_txn


router = APIRouter()


# ---------- Request models ----------
class LabFields(locations.LocationFields):
    lab_name: str
    registration_no: str
    nabl_no: Optional[str] = Field(default=None, max_length=60)
    pathologist_name: str
    pathologist_reg_no: str
    gstin: Optional[str] = None
    address: str
    city: str
    state: Optional[str] = None
    pincode: Optional[str] = None
    opening_hours: Optional[str] = Field(default=None, max_length=200)
    home_collection: bool = False
    collection_fee: float = Field(default=0, ge=0, le=2000)
    collection_radius_km: float = Field(default=3, ge=0.5, le=25)
    flat_discount_percent: Optional[float] = Field(default=None, ge=0, le=90)
    report_hours: int = Field(default=24, ge=1, le=720)

    _lab = field_validator("lab_name")(v.required_text("Lab name", 120))
    _reg = field_validator("registration_no")(v.required_text("Registration number", 120))
    _path = field_validator("pathologist_name")(v.required_text("Pathologist name", 100))
    _path_reg = field_validator("pathologist_reg_no")(v.required_text("Pathologist registration number", 60))
    _texts = field_validator("nabl_no", "gstin", "opening_hours")(v.optional_text)
    _address = field_validator("address")(v.required_text("Address", 300))
    _pincode = field_validator("pincode")(v.pincode)
    _state = field_validator("state")(v.state)

    @field_validator("city")
    @classmethod
    def _city(cls, value):
        return v.required_text("City", 60)(value).title()

    @model_validator(mode="after")
    def _default_state(self):
        self.state = self.state or ref.state_for_city(self.city)
        return self


class LabRegister(LabFields):
    name: str
    email: str
    phone: str
    password: str

    _name = field_validator("name")(v.name)
    _email = field_validator("email")(v.email)
    _phone = field_validator("phone")(v.phone)
    _password = field_validator("password")(v.password)


class LabUpdate(LabFields):
    name: str
    phone: str

    _name = field_validator("name")(v.name)
    _phone = field_validator("phone")(v.phone)


class TestIn(BaseModel):
    name: str
    category: str = "Blood"
    sample_type: Optional[str] = Field(default=None, max_length=40)
    fasting: bool = False
    mrp: Optional[float] = Field(default=None, gt=0, le=200_000)
    unit: Optional[str] = Field(default=None, max_length=20)
    ref_low: Optional[float] = None
    ref_high: Optional[float] = None

    _name = field_validator("name")(v.required_text("Test name", 120))
    _texts = field_validator("sample_type", "unit")(v.optional_text)

    @field_validator("category")
    @classmethod
    def _category(cls, value):
        if value not in TEST_CATEGORIES:
            raise ValueError(f"Category must be one of: {', '.join(TEST_CATEGORIES)}")
        return value


class LabPriceIn(BaseModel):
    test_id: Optional[int] = None
    test: Optional[TestIn] = None
    mrp: float = Field(gt=0, le=200_000)
    price: float = Field(gt=0, le=200_000)
    available: bool = True
    report_hours: Optional[int] = Field(default=None, ge=1, le=720)

    @model_validator(mode="after")
    def _check(self):
        if not (self.test_id or self.test):
            raise ValueError("Choose a test")
        if self.price > self.mrp:
            raise ValueError("Price can't be above MRP")
        return self


class LabQuoteItemIn(BaseModel):
    prescription_test_id: int
    available: bool
    price: float = Field(default=0, ge=0, le=200_000)
    mrp: Optional[float] = Field(default=None, gt=0, le=200_000)


class LabQuoteIn(BaseModel):
    items: List[LabQuoteItemIn] = Field(min_length=1)
    home_collection: bool = False
    collection_fee: float = Field(default=0, ge=0, le=2000)
    report_hours: Optional[int] = Field(default=None, ge=1, le=720)
    note: Optional[str] = Field(default=None, max_length=500)

    _note = field_validator("note")(v.optional_text)


class LabShareIn(BaseModel):
    radius: Optional[float] = Field(default=None, gt=0, le=25)
    use_home: bool = False


class LabBookingIn(BaseModel):
    lab_id: int
    collection: Literal["visit", "home"] = "visit"
    preferred_date: Optional[str] = None
    preferred_time: Optional[str] = None
    address: Optional[str] = Field(default=None, max_length=300)

    _address = field_validator("address")(v.optional_text)

    @field_validator("preferred_date")
    @classmethod
    def _date(cls, value):
        value = v.optional_text(value)
        if value:
            v.iso_date(value)
            if value < settings.now().date().isoformat():
                raise ValueError("Pick a date from today onwards")
        return value

    @field_validator("preferred_time")
    @classmethod
    def _time(cls, value):
        value = v.optional_text(value)
        return v.hhmm(value) if value else None


class LabStatusIn(BaseModel):
    status: Literal["accepted", "rejected", "sample_collected", "completed"]
    reason: Optional[str] = Field(default=None, max_length=300)

    _reason = field_validator("reason")(v.optional_text)


class BookingCancelIn(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=300)


@router.post("/api/auth/register-lab")
def register_lab(data: LabRegister, response: Response, background: BackgroundTasks):
    with get_db() as conn:
        with write_txn(conn):
            user_id = auth.create_user(conn, "lab", data)
            conn.execute(
                """INSERT INTO labs (user_id, slug, lab_name, registration_no, nabl_no, pathologist_name, pathologist_reg_no,
                   gstin, address, city, state, pincode, lat, lng, opening_hours, home_collection, collection_fee_paise,
                   collection_radius_km, flat_discount_percent, report_hours, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (user_id, make_slug(conn, data.lab_name, data.city), data.lab_name, data.registration_no, data.nabl_no,
                 data.pathologist_name, data.pathologist_reg_no, data.gstin, data.address, data.city, data.state,
                 data.pincode, data.lat, data.lng, data.opening_hours, int(data.home_collection), paise(data.collection_fee),
                 data.collection_radius_km, data.flat_discount_percent, data.report_hours, stamp(), stamp()))
        lab = dict(get_lab(conn, user_id))
        user = dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        admins = admin_emails(conn)
    auth.start_session(response, user_id)
    background.add_task(labs_emails.send_lab_application, lab, admins)
    return {"user": {k: user[k] for k in ("id", "role", "name", "email", "phone", "created_at")},
            "lab": {"status": "pending", "slug": lab["slug"], "lab_name": lab["lab_name"]}}


@router.get("/api/lab/profile")
def my_lab(user: dict = Depends(lab_user)):
    with get_db() as conn:
        return lab_private(get_lab(conn, user["id"]))


@router.put("/api/lab/profile")
def update_lab(data: LabUpdate, background: BackgroundTasks, user: dict = Depends(lab_user)):
    with get_db() as conn:
        with write_txn(conn):
            current = get_lab(conn, user["id"])
            reverify = current["status"] == "rejected" or (current["status"] == "approved" and (
                data.registration_no != current["registration_no"] or data.pathologist_reg_no != current["pathologist_reg_no"]))
            conn.execute("UPDATE users SET name = ?, phone = ? WHERE id = ?", (data.name, data.phone, user["id"]))
            conn.execute(
                """UPDATE labs SET lab_name = ?, registration_no = ?, nabl_no = ?, pathologist_name = ?, pathologist_reg_no = ?,
                   gstin = ?, address = ?, city = ?, state = ?, pincode = ?, lat = ?, lng = ?, opening_hours = ?,
                   home_collection = ?, collection_fee_paise = ?, collection_radius_km = ?, flat_discount_percent = ?,
                   report_hours = ?, status = ?, updated_at = ? WHERE user_id = ?""",
                (data.lab_name, data.registration_no, data.nabl_no, data.pathologist_name, data.pathologist_reg_no, data.gstin,
                 data.address, data.city, data.state, data.pincode, data.lat, data.lng, data.opening_hours,
                 int(data.home_collection), paise(data.collection_fee), data.collection_radius_km, data.flat_discount_percent,
                 data.report_hours, "pending" if reverify else current["status"], stamp(), user["id"]))
        row = get_lab(conn, user["id"])
        if reverify:
            background.add_task(labs_emails.send_lab_application, dict(row), admin_emails(conn))
        return {**lab_private(row), "reverification_required": reverify}


@router.get("/api/labs/nearby")
def labs_nearby(lat: float, lng: float, radius: float = Query(None, gt=0, le=25)):
    try:
        geo.check_point(lat, lng)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    with get_db() as conn:
        return [lab_public(r, d) for d, r in nearby_labs(conn, lat, lng, radius or settings.NEARBY_RADIUS_KM)]


@router.get("/api/lab-tests")
def search_tests(q: str = "", limit: int = Query(30, ge=1, le=200), user: dict = Depends(auth.require())):
    like = f"%{q.strip().lower()}%"
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM lab_tests WHERE lower(name) LIKE ? OR lower(COALESCE(code, '')) LIKE ? "
                            "ORDER BY (lower(name) LIKE ?) DESC, name LIMIT ?",
                            (like, like, q.strip().lower() + "%", limit)).fetchall()
    return [test_view(r) for r in rows]


@router.post("/api/lab-tests")
def add_test(data: TestIn, user: dict = Depends(auth.require("lab", "doctor", "admin"))):
    with get_db() as conn:
        with write_txn(conn):
            test_id = find_or_create_test(conn, data, user)
        return test_view(conn.execute("SELECT * FROM lab_tests WHERE id = ?", (test_id,)).fetchone())


@router.put("/api/admin/lab-tests/{test_id}")
def admin_update_test(test_id: int, data: TestIn, user: dict = Depends(admin_user)):
    with get_db() as conn:
        cur = conn.execute("UPDATE lab_tests SET name = ?, category = ?, sample_type = ?, fasting = ?, mrp_paise = ?, unit = ?, "
                           "ref_low = ?, ref_high = ? WHERE id = ?",
                           (data.name, data.category, data.sample_type, int(data.fasting),
                            paise(data.mrp) if data.mrp else None, data.unit, data.ref_low, data.ref_high, test_id))
        if not cur.rowcount:
            raise HTTPException(status_code=404, detail="Test not found")
        return test_view(conn.execute("SELECT * FROM lab_tests WHERE id = ?", (test_id,)).fetchone())


@router.get("/api/admin/lab-tests")
def admin_tests(q: str = "", user: dict = Depends(admin_user)):
    like = f"%{q.strip().lower()}%"
    with get_db() as conn:
        rows = conn.execute("SELECT t.*, (SELECT COUNT(*) FROM lab_prices p WHERE p.test_id = t.id) AS priced_by, "
                            "(SELECT COUNT(*) FROM prescription_tests pt WHERE pt.test_id = t.id) AS ordered "
                            "FROM lab_tests t WHERE lower(t.name) LIKE ? ORDER BY t.category, t.name", (like,)).fetchall()
        return [test_view(r) | {"priced_by": r["priced_by"], "ordered": r["ordered"]} for r in rows]


@router.get("/api/lab/prices")
def list_prices(q: str = "", user: dict = Depends(lab_user)):
    like = f"%{q.strip().lower()}%"
    with get_db() as conn:
        return [price_view(r) for r in conn.execute(PRICE_SELECT + " WHERE p.lab_id = ? AND lower(t.name) LIKE ? "
                                                    "ORDER BY t.category, t.name", (user["id"], like))]


@router.put("/api/lab/prices")
def save_price(data: LabPriceIn, user: dict = Depends(lab_user)):
    with get_db() as conn:
        with write_txn(conn):
            test_id = data.test_id or find_or_create_test(conn, data.test, user)
            if not conn.execute("SELECT 1 FROM lab_tests WHERE id = ?", (test_id,)).fetchone():
                raise HTTPException(status_code=404, detail="Test not found")
            conn.execute("INSERT INTO lab_prices (lab_id, test_id, mrp_paise, price_paise, available, report_hours, updated_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (lab_id, test_id) DO UPDATE SET mrp_paise = excluded.mrp_paise, "
                         "price_paise = excluded.price_paise, available = excluded.available, "
                         "report_hours = excluded.report_hours, updated_at = excluded.updated_at",
                         (user["id"], test_id, paise(data.mrp), paise(data.price), int(data.available), data.report_hours,
                          stamp()))
        return price_view(conn.execute(PRICE_SELECT + " WHERE p.lab_id = ? AND p.test_id = ?", (user["id"], test_id)).fetchone())


@router.delete("/api/lab/prices/{test_id}")
def delete_price(test_id: int, user: dict = Depends(lab_user)):
    with get_db() as conn:
        conn.execute("DELETE FROM lab_prices WHERE lab_id = ? AND test_id = ?", (user["id"], test_id))
    return {"ok": True}


@router.get("/api/prescriptions/{rx_id}/lab-options")
def prescription_lab_options(rx_id: str, sort: Literal["cheapest", "fastest", "nearest"] = "cheapest",
                             radius: Optional[float] = Query(None, gt=0, le=25),
                             user: dict = Depends(auth.require("patient", "doctor", "admin"))):
    with get_db() as conn:
        row, level = prescriptions_logic.rx_for(conn, rx_id, user)
        if level != "full":
            raise HTTPException(status_code=404, detail="Prescription not found")
        return options_payload(conn, row, sort, radius)


@router.post("/api/prescriptions/{rx_id}/lab-share")
def share_with_labs(rx_id: str, data: LabShareIn, background: BackgroundTasks, user: dict = Depends(patient_user)):
    """The patient sends their tests to nearby labs (or a wider circle) for prices."""
    with get_db() as conn:
        with write_txn(conn):
            row, _ = prescriptions_logic.rx_for(conn, rx_id, user)
            if not prescriptions_logic.rx_is_live(row):
                raise HTTPException(status_code=400, detail="This prescription has expired or was cancelled")
            if data.use_home:
                if row["home_lat"] is None:
                    raise HTTPException(status_code=400, detail="Set your home location first")
                conn.execute("UPDATE prescriptions SET lat = ?, lng = ?, location_source = 'home' WHERE id = ?",
                             (row["home_lat"], row["home_lng"], rx_id))
                row = conn.execute(prescriptions_logic.RX_SELECT + " WHERE r.id = ?", (rx_id,)).fetchone()
            if row["lat"] is None:
                raise HTTPException(status_code=400, detail="Set your home location first")
            added = distribute_tests(conn, rx_id, row["lat"], row["lng"], data.radius or row["radius_km"])
        email_lab_requests(background, conn, rx_id, added)
        return {**options_payload(conn, row, "cheapest", data.radius), "labs_notified": len(added)}


# ---------- Lab side: requests & quotes ----------
@router.get("/api/lab/requests")
def lab_requests(status: str = "open", user: dict = Depends(lab_user)):
    sql = ("SELECT q.*, r.reference, r.created_at AS rx_created, r.valid_until, r.status AS rx_status FROM lab_requests q "
           "JOIN prescriptions r ON r.id = q.prescription_id WHERE q.lab_id = ?")
    params = [user["id"]]
    if status == "open":
        sql += " AND q.status IN ('open', 'quoted') AND r.status = 'active' AND r.valid_until > ?"
        params.append(stamp())
    elif status:
        sql += " AND q.status = ?"
        params.append(status)
    with get_db() as conn:
        lab = get_lab(conn, user["id"])
        out = []
        for q in conn.execute(sql + " ORDER BY q.created_at DESC LIMIT 200", params).fetchall():
            tests = rx_tests(conn, q["prescription_id"])
            booked = booked_test_ids(conn, q["prescription_id"])
            remaining = [t for t in tests if t["id"] not in booked]
            quote = conn.execute("SELECT * FROM lab_quotes WHERE prescription_id = ? AND lab_id = ? AND status = 'active'",
                                 (q["prescription_id"], user["id"])).fetchone()
            suggestion = estimate(conn, lab, remaining)
            out.append({"id": q["id"], "reference": q["reference"], "distance_km": q["distance_km"], "status": q["status"],
                        "created_at": q["created_at"], "valid_until": q["valid_until"],
                        "tests": [{k: t[k] for k in ("id", "name", "category", "sample_type", "instructions")}
                                  | {"fasting": bool(t["fasting"]), "booked": t["id"] in booked} for t in tests],
                        "suggested": suggestion["lines"],
                        "my_quote": quote_offer(conn, quote, tests) | {"subtotal_paise": quote["subtotal_paise"]} if quote else None})
        return out


@router.post("/api/lab/requests/{req_id}/quote")
def send_lab_quote(req_id: int, data: LabQuoteIn, user: dict = Depends(lab_user)):
    with get_db() as conn:
        with write_txn(conn):
            lab = get_lab(conn, user["id"])
            if lab["status"] != "approved":
                raise HTTPException(status_code=403, detail="Your lab must be approved before sending quotes")
            req = request_for(conn, req_id, user["id"])
            rx = conn.execute("SELECT * FROM prescriptions WHERE id = ?", (req["prescription_id"],)).fetchone()
            if not prescriptions_logic.rx_is_live(rx) or req["status"] in ("declined", "closed"):
                raise HTTPException(status_code=400, detail="This request is no longer open")
            valid = {t["id"] for t in rx_tests(conn, rx["id"])}
            if any(i.prescription_test_id not in valid for i in data.items):
                raise HTTPException(status_code=400, detail="Quote has a test that isn't on this prescription")
            if any(i.available and i.price <= 0 for i in data.items):
                raise HTTPException(status_code=400, detail="Enter a price for every available test")
            if any(i.mrp and i.available and i.price > i.mrp for i in data.items):
                raise HTTPException(status_code=400, detail="Price can't be above MRP")
            if data.home_collection and req["distance_km"] > lab["collection_radius_km"]:
                raise HTTPException(status_code=400, detail="The patient is outside your home-collection area")
            conn.execute("UPDATE lab_quotes SET status = 'withdrawn' WHERE prescription_id = ? AND lab_id = ? AND status = 'active'",
                         (rx["id"], user["id"]))
            subtotal = sum(paise(i.price) for i in data.items if i.available)
            cur = conn.execute("INSERT INTO lab_quotes (prescription_id, lab_id, subtotal_paise, home_collection, "
                               "collection_fee_paise, report_hours, note, status, created_at, valid_until) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                               (rx["id"], user["id"], subtotal, int(data.home_collection), paise(data.collection_fee),
                                data.report_hours or lab["report_hours"], data.note, stamp(),
                                stamp(settings.now() + timedelta(hours=settings.QUOTE_VALID_HOURS))))
            conn.executemany("INSERT INTO lab_quote_items (quote_id, prescription_test_id, available, price_paise, mrp_paise) "
                             "VALUES (?, ?, ?, ?, ?)", [(cur.lastrowid, i.prescription_test_id, int(i.available),
                                                         paise(i.price) if i.available else 0,
                                                         paise(i.mrp) if i.mrp else None) for i in data.items])
            conn.execute("UPDATE lab_requests SET status = 'quoted', updated_at = ? WHERE id = ?", (stamp(), req_id))
        return {"quote_id": cur.lastrowid, "subtotal_paise": subtotal}


@router.post("/api/lab/requests/{req_id}/decline")
def decline_lab_request(req_id: int, user: dict = Depends(lab_user)):
    with get_db() as conn:
        req = request_for(conn, req_id, user["id"])
        conn.execute("UPDATE lab_requests SET status = 'declined', updated_at = ? WHERE id = ?", (stamp(), req_id))
        conn.execute("UPDATE lab_quotes SET status = 'withdrawn' WHERE prescription_id = ? AND lab_id = ? AND status = 'active'",
                     (req["prescription_id"], user["id"]))
    return {"ok": True}


@router.post("/api/prescriptions/{rx_id}/lab-bookings")
def book_lab(rx_id: str, data: LabBookingIn, background: BackgroundTasks, user: dict = Depends(patient_user)):
    with get_db() as conn:
        with write_txn(conn):
            row, _ = prescriptions_logic.rx_for(conn, rx_id, user)
            if not prescriptions_logic.rx_is_live(row):
                raise HTTPException(status_code=400, detail="This prescription has expired or was cancelled")
            tests = rx_tests(conn, rx_id)
            remaining = [t for t in tests if t["id"] not in booked_test_ids(conn, rx_id)]
            if not remaining:
                raise HTTPException(status_code=400, detail="All tests on this prescription are already booked")
            option = next((o for o in lab_options(conn, row, remaining, 25) if o["lab_id"] == data.lab_id), None)
            if not option:
                raise HTTPException(status_code=400, detail="This lab can't do these tests for you")
            if data.collection == "home":
                if not option["home_collection"]:
                    raise HTTPException(status_code=400, detail="This lab doesn't collect samples at your address")
                if not (data.address or user.get("home_address")):
                    raise HTTPException(status_code=400, detail="Enter the address for sample collection")
            fee = option["collection_fee_paise"] if data.collection == "home" else 0
            booking_id = str(uuid.uuid4())
            conn.execute("INSERT INTO lab_bookings (id, reference, prescription_id, lab_id, patient_id, quote_id, collection, "
                         "preferred_date, preferred_time, address, distance_km, subtotal_paise, collection_fee_paise, "
                         "total_paise, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                         "'booked', ?, ?)",
                         (booking_id, new_reference(conn, "lab_bookings", "LB"), rx_id, data.lab_id, user["id"],
                          option["quote_id"], data.collection, data.preferred_date, data.preferred_time,
                          (data.address or user.get("home_address")) if data.collection == "home" else None,
                          option["distance_km"], option["subtotal_paise"], fee, option["subtotal_paise"] + fee, stamp(), stamp()))
            names = {t["id"]: t for t in remaining}
            conn.executemany("INSERT INTO lab_booking_items (booking_id, prescription_test_id, test_id, name, price_paise) "
                             "VALUES (?, ?, ?, ?, ?)",
                             [(booking_id, l["prescription_test_id"], names[l["prescription_test_id"]]["test_id"], l["name"],
                               l["price_paise"]) for l in option["lines"] if l["available"]])
            if option["quote_id"]:
                conn.execute("UPDATE lab_quotes SET status = 'accepted' WHERE id = ?", (option["quote_id"],))
            conn.execute("UPDATE lab_requests SET outcome = 'chosen' WHERE prescription_id = ? AND lab_id = ?", (rx_id, data.lab_id))
            losers = []
            if not (set(names) - {l["prescription_test_id"] for l in option["lines"] if l["available"]}):
                # Everything is booked: other labs can stop quoting, and those who quoted are told
                losers = [(r["email"], r["lab_name"], row["reference"]) for r in conn.execute(
                    "SELECT u.email, l.lab_name FROM lab_requests q JOIN users u ON u.id = q.lab_id JOIN labs l ON l.user_id = q.lab_id "
                    "WHERE q.prescription_id = ? AND q.lab_id != ? AND q.status = 'quoted'", (rx_id, data.lab_id))]
                conn.execute("UPDATE lab_requests SET status = 'closed', outcome = 'chosen_other', updated_at = ? WHERE prescription_id = ? "
                             "AND lab_id != ? AND status IN ('open', 'quoted')", (stamp(), rx_id, data.lab_id))
                conn.execute("UPDATE lab_requests SET status = 'closed', updated_at = ? WHERE prescription_id = ? AND lab_id = ? "
                             "AND status IN ('open', 'quoted')", (stamp(), rx_id, data.lab_id))
                conn.execute("UPDATE lab_quotes SET status = 'lost' WHERE prescription_id = ? AND status = 'active'", (rx_id,))
            booking = conn.execute("SELECT reference FROM lab_bookings WHERE id = ?", (booking_id,)).fetchone()
            chat_id = chats.open_conversation(conn, user["id"], "lab", data.lab_id, "lab_booking", booking_id,
                                              f"{booking['reference']} · {option['lab_name']}",
                                              f"Booking {booking['reference']} with {option['lab_name']}. Chat here about timing, "
                                              "fasting or sample collection.")
        notify(background, conn, booking_id, ["lab", "patient"])
        medicine_orders.tell_losers(background, losers, "tests")
        return {**booking_view(conn, conn.execute(BOOKING_SELECT + " WHERE b.id = ?", (booking_id,)).fetchone(), "patient"),
                "chat_id": chat_id}


@router.get("/api/my/lab-bookings")
def my_lab_bookings(user: dict = Depends(patient_user)):
    with get_db() as conn:
        return [booking_view(conn, r, "patient") for r in conn.execute(
            BOOKING_SELECT + " WHERE b.patient_id = ? ORDER BY b.created_at DESC", (user["id"],))]


@router.get("/api/lab-bookings/{booking_id}")
def get_booking(booking_id: str, user: dict = Depends(auth.require("patient", "lab", "admin"))):
    with get_db() as conn:
        return booking_view(conn, booking_for(conn, booking_id, user), user["role"])


@router.post("/api/lab-bookings/{booking_id}/cancel")
def cancel_booking(booking_id: str, data: BookingCancelIn, background: BackgroundTasks, user: dict = Depends(patient_user)):
    with get_db() as conn:
        with write_txn(conn):
            row = booking_for(conn, booking_id, user)
            if row["status"] not in ("booked", "accepted"):
                raise HTTPException(status_code=400, detail="The sample has been collected; this booking can't be cancelled")
            conn.execute("UPDATE lab_bookings SET status = 'cancelled', status_reason = ?, updated_at = ? WHERE id = ?",
                         (data.reason or "Cancelled by the patient", stamp(), booking_id))
            reopen_lab_requests(conn, row["prescription_id"])
            chats.note_on_order(conn, "lab_booking", booking_id, chats.order_note("cancelled", "lab"))
        notify(background, conn, booking_id, ["lab"])
        return booking_view(conn, conn.execute(BOOKING_SELECT + " WHERE b.id = ?", (booking_id,)).fetchone(), "patient")


@router.get("/api/lab/bookings")
def lab_bookings(status: Optional[str] = None, user: dict = Depends(lab_user)):
    sql, params = BOOKING_SELECT + " WHERE b.lab_id = ?", [user["id"]]
    if status == "open":
        sql += " AND b.status IN ('booked', 'accepted', 'sample_collected', 'report_ready')"
    elif status:
        sql += " AND b.status = ?"
        params.append(status)
    with get_db() as conn:
        return [booking_view(conn, r, "lab") for r in conn.execute(sql + " ORDER BY b.created_at DESC LIMIT 500", params)]


@router.post("/api/lab/bookings/{booking_id}/status")
def update_booking_status(booking_id: str, data: LabStatusIn, background: BackgroundTasks, user: dict = Depends(lab_user)):
    with get_db() as conn:
        with write_txn(conn):
            row = booking_for(conn, booking_id, user)
            if data.status not in NEXT_STATUS.get(row["status"], set()):
                raise HTTPException(status_code=400, detail=f"Can't change a booking from {row['status']} to {data.status}"
                                    + (" — upload the report instead" if row["status"] == "sample_collected" else ""))
            if data.status == "rejected" and not data.reason:
                raise HTTPException(status_code=400, detail="Please tell the patient why")
            conn.execute("UPDATE lab_bookings SET status = ?, status_reason = ?, payment_status = ?, updated_at = ? WHERE id = ?",
                         (data.status, data.reason, "paid" if data.status == "completed" else row["payment_status"], stamp(),
                          booking_id))
            if data.status == "rejected":
                reopen_lab_requests(conn, row["prescription_id"])
            chats.note_on_order(conn, "lab_booking", booking_id, chats.order_note(data.status, "lab", data.reason))
        notify(background, conn, booking_id, ["patient"],
               "You can book the same tests at another lab nearby." if data.status == "rejected" else None)
        return booking_view(conn, conn.execute(BOOKING_SELECT + " WHERE b.id = ?", (booking_id,)).fetchone(), "lab")


@router.post("/api/lab/bookings/{booking_id}/report")
async def upload_report(booking_id: str, background: BackgroundTasks, file: UploadFile = File(...),
                        values: str = Form("[]"), notes: Optional[str] = Form(None), user: dict = Depends(lab_user)):
    """The report (PDF/photo) plus optional key results; makes the patient's health record."""
    content = await file.read(settings.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    try:
        parsed = [health_records_api.ValueIn(**x) for x in json.loads(values or "[]")][:50]
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"Results: {e}")
    with get_db() as conn:
        row = booking_for(conn, booking_id, user)
        if row["status"] not in ("sample_collected", "report_ready"):
            raise HTTPException(status_code=400, detail="Mark the sample as collected before uploading the report")
        stored = health_records_logic.store_file(content, file.filename)
        with write_txn(conn):
            old = conn.execute("SELECT * FROM health_records WHERE lab_booking_id = ? AND deleted_at IS NULL",
                               (booking_id,)).fetchone()
            if old:  # a corrected report replaces the earlier one
                conn.execute("UPDATE health_records SET deleted_at = ? WHERE id = ?", (stamp(), old["id"]))
                health_records_logic.remove_file(old["file_path"])
            title = ", ".join(i["name"] for i in booking_items(conn, booking_id))[:200]
            record_id = health_records_logic.create_record(
                conn, patient_id=row["patient_id"], kind="lab_report", title=title, record_date=settings.now().date().isoformat(),
                stored=stored, user=user, lab_booking_id=booking_id, lab_id=row["lab_id"], doctor_id=row["doctor_id"],
                notes=v.optional_text(notes), values=parsed)
            conn.execute("UPDATE lab_bookings SET status = 'report_ready', updated_at = ? WHERE id = ?", (stamp(), booking_id))
            chats.note_on_order(conn, "lab_booking", booking_id, chats.order_note("report_ready", "lab"))
        data = full_booking(conn, booking_id)
    background.add_task(labs_emails.send_report_ready, data["patient_email"], data["patient_name"], data["reference"],
                        data["lab_name"], False)
    background.add_task(labs_emails.send_report_ready, data["doctor_email"], data["doctor_name"], data["reference"],
                        data["lab_name"], True, data["patient_name"])
    return {"record_id": record_id, "status": "report_ready"}


@router.get("/api/lab/stats")
def lab_stats(user: dict = Depends(lab_user)):
    month_start = settings.now().date().replace(day=1).isoformat()
    with get_db() as conn:
        def one(sql, *params):
            return conn.execute(sql, params).fetchone()[0]
        return {
            "open_requests": one("SELECT COUNT(*) FROM lab_requests q JOIN prescriptions r ON r.id = q.prescription_id "
                                 "WHERE q.lab_id = ? AND q.status = 'open' AND r.status = 'active' AND r.valid_until > ?",
                                 user["id"], stamp()),
            "new_bookings": one("SELECT COUNT(*) FROM lab_bookings WHERE lab_id = ? AND status = 'booked'", user["id"]),
            "awaiting_report": one("SELECT COUNT(*) FROM lab_bookings WHERE lab_id = ? AND status = 'sample_collected'",
                                   user["id"]),
            "completed_month": one("SELECT COUNT(*) FROM lab_bookings WHERE lab_id = ? AND status IN ('report_ready', "
                                   "'completed') AND created_at >= ?", user["id"], month_start),
            "revenue_month_paise": one("SELECT COALESCE(SUM(total_paise), 0) FROM lab_bookings WHERE lab_id = ? AND "
                                       "status IN ('report_ready', 'completed') AND created_at >= ?", user["id"], month_start),
            "price_list_tests": one("SELECT COUNT(*) FROM lab_prices WHERE lab_id = ?", user["id"]),
        }


# ---------- Admin ----------
@router.get("/api/admin/labs")
def admin_labs(status: Optional[str] = None, user: dict = Depends(admin_user)):
    sql, params = LAB_SELECT, []
    if status:
        sql += " WHERE l.status = ?"
        params.append(status)
    with get_db() as conn:
        return [lab_private(r) for r in conn.execute(sql + " ORDER BY l.status = 'pending' DESC, l.created_at DESC", params)]


@router.post("/api/admin/labs/{lab_id}/status")
def admin_lab_status(lab_id: int, data: partners.PartnerStatus, background: BackgroundTasks, user: dict = Depends(admin_user)):
    with get_db() as conn:
        row = get_lab(conn, lab_id)
        conn.execute("UPDATE labs SET status = ?, status_reason = ?, approved_at = CASE WHEN ? = 'approved' "
                     "THEN COALESCE(approved_at, ?) ELSE approved_at END, updated_at = ? WHERE user_id = ?",
                     (data.status, data.reason, data.status, stamp(), stamp(), lab_id))
        audit(conn, user["id"], f"lab.{data.status}", f"user:{lab_id}", data.reason)
    if data.status != "approved":
        wind_down_lab(lab_id, "The lab is no longer available on the platform.", background)
    background.add_task(mailer.send_partner_status, row["name"], row["email"], data.status, data.reason, "/lab", "lab")
    return {"ok": True}


@router.get("/api/admin/lab-bookings")
def admin_lab_bookings(status: Optional[str] = None, user: dict = Depends(admin_user)):
    sql, params = BOOKING_SELECT, []
    if status:
        sql += " WHERE b.status = ?"
        params.append(status)
    with get_db() as conn:
        return [booking_view(conn, r, "admin") for r in conn.execute(sql + " ORDER BY b.created_at DESC LIMIT 1000", params)]


# Imported last, so slices can depend on each other in any order
from features.labs.logic import admin_emails, booked_test_ids, booking_for, booking_items, BOOKING_SELECT, booking_view, distribute_tests, email_lab_requests, estimate, find_or_create_test, full_booking, get_lab, lab_options, lab_private, lab_public, LAB_SELECT, make_slug, nearby_labs, NEXT_STATUS, notify, options_payload, PRICE_SELECT, price_view, quote_offer, reopen_lab_requests, request_for, rx_tests, TEST_CATEGORIES, test_view, wind_down_lab  # noqa: E402
from features.chats import logic as chats  # noqa: E402
from features.health_records import api as health_records_api  # noqa: E402
from features.health_records import logic as health_records_logic  # noqa: E402
from features.labs import emails as labs_emails  # noqa: E402
from features.medicine_orders import logic as medicine_orders  # noqa: E402
from features.prescriptions import logic as prescriptions_logic  # noqa: E402

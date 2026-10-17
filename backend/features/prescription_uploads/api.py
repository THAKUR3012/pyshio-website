"""Prescription uploads: a patient uploads any prescription, nearby pharmacies and labs quote.

This file: HTTP endpoints and request models.
"""
import json
import secrets
import uuid
from datetime import timedelta
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Literal, Optional

from shared import auth, settings, validators as v
from shared.auth import partner_user, patient_user
from shared.common import new_reference, paise, stamp
from shared.db import get_db, write_txn


router = APIRouter()


# ---------- Models ----------
class QuoteLineIn(BaseModel):
    name: str
    medicine_id: Optional[int] = None  # set when the line came from the catalogue (pre-filled from the photo)
    quantity: int = Field(default=1, ge=1, le=1000)
    available: bool = True
    unit_price: float = Field(default=0, ge=0, le=200_000)
    mrp: Optional[float] = Field(default=None, gt=0, le=200_000)

    _name = field_validator("name")(v.required_text("Item name", 120))

    @model_validator(mode="after")
    def _prices(self):
        if self.available and self.unit_price <= 0:
            raise ValueError(f"Enter a price for {self.name}")
        if self.available and self.mrp and self.unit_price > self.mrp:
            raise ValueError(f"Price for {self.name} can't be above MRP")
        return self


class UploadQuoteIn(BaseModel):
    lines: List[QuoteLineIn] = Field(min_length=1, max_length=40)
    delivery_available: bool = False          # pharmacy: home delivery · lab: home sample collection
    delivery_fee: float = Field(default=0, ge=0, le=2000)
    ready_minutes: Optional[int] = Field(default=None, ge=0, le=10080)     # pharmacy: ready for pickup · lab: report time
    delivery_minutes: Optional[int] = Field(default=None, ge=5, le=1440)   # pharmacy delivery time
    note: Optional[str] = Field(default=None, max_length=500)

    _note = field_validator("note")(v.optional_text)


class WidenIn(BaseModel):
    radius: float = Field(gt=0, le=25)


class UploadOrderIn(BaseModel):
    quote_id: int
    fulfilment: Literal["pickup", "delivery", "visit", "home"]
    address: Optional[str] = Field(default=None, max_length=300)
    preferred_date: Optional[str] = None
    preferred_time: Optional[str] = None

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


class OrderStatusIn(BaseModel):
    status: Literal["accepted", "rejected", "ready", "sample_collected", "completed"]
    reason: Optional[str] = Field(default=None, max_length=300)
    code: Optional[str] = Field(default=None, max_length=8)

    _reason = field_validator("reason")(v.optional_text)


class CancelIn(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=300)


# ---------- Patient ----------
@router.post("/api/my/uploads")
async def upload_prescription(background: BackgroundTasks, file: UploadFile = File(...), want_medicines: bool = Form(True),
                              want_tests: bool = Form(False), note: Optional[str] = Form(None),
                              ai_read_consent: bool = Form(False), typed_lines: Optional[str] = Form(None),
                              user: dict = Depends(patient_user)):
    if not (want_medicines or want_tests):
        raise HTTPException(status_code=422, detail="Choose medicines, tests or both")
    note = v.optional_text(note)
    if note and len(note) > 500:
        raise HTTPException(status_code=422, detail="Keep the note under 500 characters")
    typed_lines = v.optional_text(typed_lines)
    if typed_lines and len(typed_lines) > 1500:
        raise HTTPException(status_code=422, detail="Keep the typed medicine names under 1,500 characters")
    if user.get("home_lat") is None:
        raise HTTPException(status_code=400, detail="Set your home location first so we can find pharmacies and labs near you")
    with get_db() as conn:
        if auth.rate_limited(conn, f"upload-rx:{user['id']}", 10, 24 * 60):
            raise HTTPException(status_code=429, detail="You can upload up to 10 prescriptions a day")
    stored = health_records_logic.store_file(await file.read(settings.MAX_UPLOAD_MB * 1024 * 1024 + 1), file.filename)
    upload_id = str(uuid.uuid4())
    with get_db() as conn:
        with write_txn(conn):
            record_id = health_records_logic.create_record(conn, user["id"], "prescription_copy", "Uploaded prescription",
                                              settings.now().date().isoformat(), stored, user, notes=note)
            conn.execute("INSERT INTO rx_uploads (id, reference, patient_id, record_id, want_medicines, want_tests, note, lat, lng, "
                         "radius_km, status, created_at, valid_until, ai_read_consent, typed_lines) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)",
                         (upload_id, new_reference(conn, "rx_uploads", "UP"), user["id"], record_id, int(want_medicines),
                          int(want_tests), note, user["home_lat"], user["home_lng"], settings.NEARBY_RADIUS_KM, stamp(),
                          stamp(settings.now() + timedelta(days=settings.UPLOAD_VALID_DAYS)), int(ai_read_consent), typed_lines))
            reader.queue(conn, upload_id, record_id)
            upload = conn.execute("SELECT * FROM rx_uploads WHERE id = ?", (upload_id,)).fetchone()
            added = distribute(conn, upload)
        email_requests(background, upload, added)
        background.add_task(reader.run, upload_id)  # pre-fill lines for partners (cron retries if this doesn't finish)
        return {**detail(conn, upload, "cheapest"),
                "pharmacies_notified": sum(1 for a in added if a[0] == "pharmacy"),
                "labs_notified": sum(1 for a in added if a[0] == "lab")}


@router.get("/api/my/uploads")
def my_uploads(user: dict = Depends(patient_user)):
    with get_db() as conn:
        out = []
        for u in conn.execute("SELECT * FROM rx_uploads WHERE patient_id = ? ORDER BY created_at DESC", (user["id"],)).fetchall():
            d = detail(conn, u, "cheapest")
            best = [d[k]["highlights"]["cheapest"] for k in ("medicines", "tests") if d[k]["highlights"]["cheapest"]]
            out.append({k: d[k] for k in ("id", "reference", "note", "status", "created_at", "valid_until", "live", "file_url",
                                          "want_medicines", "want_tests")}
                       | {"quotes": len(d["medicines"]["options"]) + len(d["tests"]["options"]),
                          "sent_to": d["medicines"]["sent_to"] + d["tests"]["sent_to"],
                          "best_paise": min((b["subtotal_paise"] for b in best), default=None),
                          "orders": [{k: o[k] for k in ("reference", "partner_name", "status", "total_paise")} for o in d["orders"]]})
        return out


@router.get("/api/my/uploads/{upload_id}")
def get_upload(upload_id: str, sort: Literal["cheapest", "discount", "fastest", "nearest"] = "cheapest",
               user: dict = Depends(patient_user)):
    with get_db() as conn:
        upload = upload_for(conn, upload_id, user)
        return {**detail(conn, upload, sort), "read": reader.patient_view(conn, upload, user)}


@router.post("/api/my/uploads/{upload_id}/widen")
def widen(upload_id: str, data: WidenIn, background: BackgroundTasks, user: dict = Depends(patient_user)):
    with get_db() as conn:
        with write_txn(conn):
            upload = upload_for(conn, upload_id, user)
            if not is_live(upload):
                raise HTTPException(status_code=400, detail="This upload is closed")
            if data.radius <= upload["radius_km"]:
                raise HTTPException(status_code=400, detail="Choose a wider distance")
            conn.execute("UPDATE rx_uploads SET radius_km = ? WHERE id = ?", (data.radius, upload_id))
            upload = conn.execute("SELECT * FROM rx_uploads WHERE id = ?", (upload_id,)).fetchone()
            added = distribute(conn, upload)
        email_requests(background, upload, added)
        return {**detail(conn, upload, "cheapest"), "newly_notified": len(added)}


@router.post("/api/my/uploads/{upload_id}/cancel")
def cancel_upload(upload_id: str, user: dict = Depends(patient_user)):
    with get_db() as conn:
        with write_txn(conn):
            upload = upload_for(conn, upload_id, user)
            conn.execute("UPDATE rx_uploads SET status = 'cancelled' WHERE id = ?", (upload_id,))
            conn.execute("UPDATE upload_requests SET status = 'closed', updated_at = ? WHERE upload_id = ? AND status IN ('open', 'quoted')",
                         (stamp(), upload_id))
            conn.execute("UPDATE upload_quotes SET status = 'lost' WHERE upload_id = ? AND status = 'active'", (upload_id,))
        return detail(conn, conn.execute("SELECT * FROM rx_uploads WHERE id = ?", (upload_id,)).fetchone(), "cheapest")


@router.post("/api/my/uploads/{upload_id}/orders")
def book(upload_id: str, data: UploadOrderIn, background: BackgroundTasks, user: dict = Depends(patient_user)):
    with get_db() as conn:
        with write_txn(conn):
            upload = upload_for(conn, upload_id, user)
            if not is_live(upload):
                raise HTTPException(status_code=400, detail="This upload has expired or was cancelled. Upload it again for fresh quotes.")
            q = conn.execute(QUOTE_SELECT + " WHERE q.id = ? AND q.upload_id = ?", (data.quote_id, upload_id)).fetchone()
            if not q or q["status"] != "active" or q["valid_until"] <= stamp():
                raise HTTPException(status_code=400, detail="This quotation is no longer available")
            role = q["partner_role"]
            if conn.execute("SELECT 1 FROM upload_orders WHERE upload_id = ? AND partner_role = ? AND status IN "
                            f"({','.join('?' * len(ACTIVE_ORDER))})", (upload_id, role, *ACTIVE_ORDER)).fetchone():
                raise HTTPException(status_code=400, detail=f"You've already booked the {KIND[role]} for this prescription")
            allowed = ("pickup", "delivery") if role == "pharmacy" else ("visit", "home")
            if data.fulfilment not in allowed:
                raise HTTPException(status_code=400, detail=f"Choose {' or '.join(allowed)}")
            home = data.fulfilment in ("delivery", "home")
            if home and not q["delivery_available"]:
                raise HTTPException(status_code=400, detail="This partner doesn't offer that for your address")
            address = (data.address or user.get("home_address")) if home else None
            if home and not address:
                raise HTTPException(status_code=400, detail="Enter the address")
            fee = q["delivery_fee_paise"] if home else 0
            order_id = str(uuid.uuid4())
            conn.execute("INSERT INTO upload_orders (id, reference, upload_id, quote_id, patient_id, partner_role, partner_id, fulfilment, "
                         "address, preferred_date, preferred_time, subtotal_paise, fee_paise, total_paise, status, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'placed', ?, ?)",
                         (order_id, new_reference(conn, "upload_orders", "UO"), upload_id, q["id"], user["id"], role, q["partner_id"],
                          data.fulfilment, address, data.preferred_date, data.preferred_time, q["subtotal_paise"], fee,
                          q["subtotal_paise"] + fee, stamp(), stamp()))
            conn.execute("UPDATE upload_quotes SET status = 'accepted' WHERE id = ?", (q["id"],))
            conn.execute("UPDATE upload_quotes SET status = 'lost' WHERE upload_id = ? AND partner_role = ? AND status = 'active'",
                         (upload_id, role))
            # Everyone else who got this prescription is told the patient chose someone; only those who quoted get an email
            losers = [(r["email"], r["name"], upload["reference"]) for r in conn.execute(
                "SELECT u.email, COALESCE(ph.store_name, l.lab_name) AS name FROM upload_requests r JOIN users u ON u.id = r.partner_id "
                "LEFT JOIN pharmacies ph ON ph.user_id = r.partner_id LEFT JOIN labs l ON l.user_id = r.partner_id "
                "WHERE r.upload_id = ? AND r.partner_role = ? AND r.partner_id != ? AND r.status = 'quoted'",
                (upload_id, role, q["partner_id"]))]
            conn.execute("UPDATE upload_requests SET status = 'closed', updated_at = ?, "
                         "outcome = CASE WHEN partner_id = ? THEN 'chosen' ELSE 'chosen_other' END "
                         "WHERE upload_id = ? AND partner_role = ? AND status IN ('open', 'quoted')",
                         (stamp(), q["partner_id"], upload_id, role))
            reference = conn.execute("SELECT reference FROM upload_orders WHERE id = ?", (order_id,)).fetchone()["reference"]
            chat_id = chats.open_conversation(
                conn, user["id"], role, q["partner_id"], "upload_order", order_id, f"{reference} · {q['partner_name']}",
                f"Booked {q['partner_name']} for your uploaded prescription ({reference}). Chat here about the "
                + ("medicines, substitutes or delivery." if role == "pharmacy" else "tests, fasting or sample collection."))
        notify(background, conn, order_id, "partner")
        notify(background, conn, order_id, "patient")
        medicine_orders.tell_losers(background, losers, KIND[role])
        return {**order_view(conn, conn.execute(ORDER_SELECT + " WHERE o.id = ?", (order_id,)).fetchone(), "patient"), "chat_id": chat_id}


@router.get("/api/my/upload-orders")
def my_upload_orders(user: dict = Depends(patient_user)):
    with get_db() as conn:
        return [order_view(conn, r, "patient") for r in conn.execute(
            ORDER_SELECT + " WHERE o.patient_id = ? ORDER BY o.created_at DESC", (user["id"],))]


@router.post("/api/my/upload-orders/{order_id}/cancel")
def cancel_order(order_id: str, data: CancelIn, background: BackgroundTasks, user: dict = Depends(patient_user)):
    with get_db() as conn:
        with write_txn(conn):
            row = conn.execute(ORDER_SELECT + " WHERE o.id = ? AND o.patient_id = ?", (order_id, user["id"])).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Order not found")
            if row["status"] not in ("placed", "accepted"):
                raise HTTPException(status_code=400, detail="This order can't be cancelled any more")
            conn.execute("UPDATE upload_orders SET status = 'cancelled', status_reason = ?, updated_at = ? WHERE id = ?",
                         (data.reason or "Cancelled by the patient", stamp(), order_id))
            reopen(conn, row)
            chats.note_on_order(conn, "upload_order", order_id, chats.order_note("cancelled", row["partner_role"]))
        notify(background, conn, order_id, "partner")
        return order_view(conn, conn.execute(ORDER_SELECT + " WHERE o.id = ?", (order_id,)).fetchone(), "patient")


@router.get("/api/partner/uploads")
def partner_uploads(status: str = "open", user: dict = Depends(partner_user)):
    sql = ("SELECT r.*, x.reference, x.note, x.record_id, x.valid_until, x.status AS upload_status, x.want_medicines, x.want_tests "
           "FROM upload_requests r JOIN rx_uploads x ON x.id = r.upload_id WHERE r.partner_id = ?")
    params = [user["id"]]
    if status == "open":
        sql += " AND r.status IN ('open', 'quoted') AND x.status = 'open' AND x.valid_until > ?"
        params.append(stamp())
    elif status == "decided":
        sql += " AND r.outcome IS NOT NULL"
    with get_db() as conn:
        out = []
        for r in conn.execute(sql + " ORDER BY r.created_at DESC LIMIT 200", params).fetchall():
            mine = conn.execute(QUOTE_SELECT + " WHERE q.upload_id = ? AND q.partner_id = ? AND q.status IN ('active', 'accepted') "
                                "ORDER BY q.id DESC LIMIT 1", (r["upload_id"], user["id"])).fetchone()
            out.append({"id": r["id"], "reference": r["reference"], "note": r["note"], "distance_km": r["distance_km"],
                        "status": r["status"], "created_at": r["created_at"], "valid_until": r["valid_until"],
                        "file_url": f"/api/records/{r['record_id']}/file", "kind": KIND[r["partner_role"]], "outcome": r["outcome"],
                        "my_quote": quote_view(conn, mine) if mine else None})
        return out


@router.post("/api/partner/uploads/{request_id}/quote")
def send_quote(request_id: int, data: UploadQuoteIn, background: BackgroundTasks, user: dict = Depends(partner_user)):
    with get_db() as conn:
        with write_txn(conn):
            approved_partner(conn, user)
            req = conn.execute("SELECT r.*, x.status AS upload_status, x.valid_until, x.reference, x.patient_id FROM upload_requests r "
                               "JOIN rx_uploads x ON x.id = r.upload_id WHERE r.id = ? AND r.partner_id = ?",
                               (request_id, user["id"])).fetchone()
            if not req:
                raise HTTPException(status_code=404, detail="Request not found")
            if req["status"] not in ("open", "quoted") or req["upload_status"] != "open" or req["valid_until"] <= stamp():
                raise HTTPException(status_code=400, detail="This request is no longer open")
            conn.execute("UPDATE upload_quotes SET status = 'withdrawn' WHERE upload_id = ? AND partner_id = ? AND status = 'active'",
                         (req["upload_id"], user["id"]))
            available = [l for l in data.lines if l.available]
            subtotal = sum(paise(l.unit_price) * l.quantity for l in available)
            mrp_total = sum(paise(l.mrp or l.unit_price) * l.quantity for l in available)
            cur = conn.execute("INSERT INTO upload_quotes (upload_id, partner_role, partner_id, subtotal_paise, mrp_total_paise, "
                               "lines_total, lines_available, delivery_available, delivery_fee_paise, ready_minutes, delivery_minutes, "
                               "note, status, created_at, valid_until) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                               (req["upload_id"], user["role"], user["id"], subtotal, mrp_total, len(data.lines), len(available),
                                int(data.delivery_available), paise(data.delivery_fee) if data.delivery_available else 0,
                                data.ready_minutes, data.delivery_minutes if user["role"] == "pharmacy" else None, data.note,
                                stamp(), stamp(settings.now() + timedelta(hours=settings.QUOTE_VALID_HOURS))))
            known = {r["id"] for r in conn.execute(
                f"SELECT id FROM medicines WHERE id IN ({','.join('?' * len(data.lines))})", [l.medicine_id or 0 for l in data.lines])}
            conn.executemany("INSERT INTO upload_quote_lines (quote_id, name, quantity, available, unit_price_paise, mrp_paise, medicine_id) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?)", [(cur.lastrowid, l.name, l.quantity, int(l.available),
                                                              paise(l.unit_price) if l.available else 0, paise(l.mrp) if l.mrp else None,
                                                              l.medicine_id if l.medicine_id in known else None)
                                                             for l in data.lines])
            conn.execute("UPDATE upload_requests SET status = 'quoted', updated_at = ? WHERE id = ?", (stamp(), request_id))
            patient = conn.execute("SELECT name, email FROM users WHERE id = ?", (req["patient_id"],)).fetchone()
            name = conn.execute(f"SELECT {'store_name' if user['role'] == 'pharmacy' else 'lab_name'} FROM "
                                f"{'pharmacies' if user['role'] == 'pharmacy' else 'labs'} WHERE user_id = ?", (user["id"],)).fetchone()[0]
        background.add_task(prescription_uploads_emails.send_upload_quote, patient["email"], patient["name"], req["reference"], name, subtotal,
                            len(available), len(data.lines))
        return {"quote_id": cur.lastrowid, "subtotal_paise": subtotal}


@router.get("/api/partner/uploads/{request_id}/suggested-lines")
def suggested_lines(request_id: int, user: dict = Depends(partner_user)):
    """Lines read from the photo, priced from this partner's own list. The partner checks each one before quoting."""
    with get_db() as conn:
        req = conn.execute("SELECT * FROM upload_requests WHERE id = ? AND partner_id = ?", (request_id, user["id"])).fetchone()
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        partner = conn.execute("SELECT * FROM pharmacies WHERE user_id = ?", (user["id"],)).fetchone() \
            if user["role"] == "pharmacy" else None
        return reader.suggested_lines(conn, req, partner)


@router.post("/api/partner/uploads/{request_id}/decline")
def decline(request_id: int, user: dict = Depends(partner_user)):
    with get_db() as conn:
        req = conn.execute("SELECT * FROM upload_requests WHERE id = ? AND partner_id = ?", (request_id, user["id"])).fetchone()
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        conn.execute("UPDATE upload_requests SET status = 'declined', updated_at = ? WHERE id = ?", (stamp(), request_id))
        conn.execute("UPDATE upload_quotes SET status = 'withdrawn' WHERE upload_id = ? AND partner_id = ? AND status = 'active'",
                     (req["upload_id"], user["id"]))
    return {"ok": True}


@router.get("/api/partner/upload-orders")
def partner_orders(status: Optional[str] = None, user: dict = Depends(partner_user)):
    sql, params = ORDER_SELECT + " WHERE o.partner_id = ?", [user["id"]]
    if status == "open":
        sql += " AND o.status IN ('placed', 'accepted', 'ready', 'sample_collected', 'report_ready')"
    elif status:
        sql += " AND o.status = ?"
        params.append(status)
    with get_db() as conn:
        return [order_view(conn, r, "partner") for r in conn.execute(sql + " ORDER BY o.created_at DESC LIMIT 300", params)]


@router.post("/api/partner/upload-orders/{order_id}/status")
def update_status(order_id: str, data: OrderStatusIn, background: BackgroundTasks, user: dict = Depends(partner_user)):
    with get_db() as conn:
        with write_txn(conn):
            row = partner_order(conn, order_id, user)
            if data.status not in NEXT[row["partner_role"]].get(row["status"], set()):
                hint = " Upload the report instead." if row["status"] == "sample_collected" else ""
                raise HTTPException(status_code=400, detail=f"Can't change an order from {row['status']} to {data.status}.{hint}")
            if data.status == "rejected" and not data.reason:
                raise HTTPException(status_code=400, detail="Please tell the patient why")
            code = row["handover_code"]
            if data.status == "ready":
                code = f"{secrets.randbelow(10000):04d}"
            if data.status == "completed" and row["partner_role"] == "pharmacy" and (data.code or "").strip() != row["handover_code"]:
                raise HTTPException(status_code=400, detail="Enter the 4-digit handover code from the patient")
            conn.execute("UPDATE upload_orders SET status = ?, status_reason = ?, handover_code = ?, payment_status = ?, updated_at = ? "
                         "WHERE id = ?", (data.status, data.reason, code, "paid" if data.status == "completed" else row["payment_status"],
                                          stamp(), order_id))
            if data.status == "rejected":
                reopen(conn, row)
            chats.note_on_order(conn, "upload_order", order_id, chats.order_note(
                "out_for_delivery" if data.status == "ready" and row["fulfilment"] == "delivery" else data.status, row["partner_role"], data.reason))
        notify(background, conn, order_id, "patient",
               "You can choose another quotation for the same prescription." if data.status == "rejected" else None)
        return order_view(conn, conn.execute(ORDER_SELECT + " WHERE o.id = ?", (order_id,)).fetchone(), "partner")


@router.post("/api/partner/upload-orders/{order_id}/report")
async def upload_report(order_id: str, background: BackgroundTasks, file: UploadFile = File(...), values: str = Form("[]"),
                        notes: Optional[str] = Form(None), user: dict = Depends(auth.require("lab"))):
    content = await file.read(settings.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    try:
        parsed = [health_records_api.ValueIn(**x) for x in json.loads(values or "[]")][:50]
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"Results: {e}")
    with get_db() as conn:
        row = partner_order(conn, order_id, user)
        if row["status"] not in ("sample_collected", "report_ready"):
            raise HTTPException(status_code=400, detail="Mark the sample as collected before uploading the report")
        stored = health_records_logic.store_file(content, file.filename)
        with write_txn(conn):
            old = conn.execute("SELECT * FROM health_records WHERE upload_order_id = ? AND deleted_at IS NULL", (order_id,)).fetchone()
            if old:
                conn.execute("UPDATE health_records SET deleted_at = ? WHERE id = ?", (stamp(), old["id"]))
                health_records_logic.remove_file(old["file_path"])
            title = ", ".join(l["name"] for l in conn.execute("SELECT name FROM upload_quote_lines WHERE quote_id = ? AND available = 1",
                                                                (row["quote_id"],)))[:200] or "Lab report"
            record_id = health_records_logic.create_record(conn, row["patient_id"], "lab_report", title, settings.now().date().isoformat(), stored,
                                              user, lab_id=user["id"], notes=v.optional_text(notes), values=parsed)
            conn.execute("UPDATE health_records SET upload_order_id = ? WHERE id = ?", (order_id, record_id))
            conn.execute("UPDATE upload_orders SET status = 'report_ready', updated_at = ? WHERE id = ?", (stamp(), order_id))
            chats.note_on_order(conn, "upload_order", order_id, chats.order_note("report_ready", "lab"))
    background.add_task(labs_emails.send_report_ready, row["patient_email"], row["patient_name"], row["reference"], row["partner_name"], False)
    return {"record_id": record_id, "status": "report_ready"}


@router.get("/api/partner/upload-stats")
def partner_stats(user: dict = Depends(partner_user)):
    with get_db() as conn:
        open_requests = conn.execute("SELECT COUNT(*) FROM upload_requests r JOIN rx_uploads x ON x.id = r.upload_id WHERE r.partner_id = ? "
                                     "AND r.status = 'open' AND x.status = 'open' AND x.valid_until > ?", (user["id"], stamp())).fetchone()[0]
        open_orders = conn.execute("SELECT COUNT(*) FROM upload_orders WHERE partner_id = ? AND status IN "
                                   "('placed', 'accepted', 'ready', 'sample_collected')", (user["id"],)).fetchone()[0]
    return {"open_requests": open_requests, "open_orders": open_orders}


@router.get("/api/partner/outcomes")
def partner_outcomes(user: dict = Depends(partner_user)):
    """Requests decided in the last 14 days: did the patient choose this pharmacy / lab or someone else?"""
    since = stamp(settings.now() - timedelta(days=14))
    with get_db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT x.reference, r.outcome, r.updated_at, r.status = 'quoted' OR EXISTS (SELECT 1 FROM upload_quotes q WHERE "
            "q.upload_id = r.upload_id AND q.partner_id = r.partner_id) AS quoted, 'uploaded prescription' AS source "
            "FROM upload_requests r JOIN rx_uploads x ON x.id = r.upload_id WHERE r.partner_id = ? AND r.outcome IS NOT NULL "
            "AND r.updated_at >= ?", (user["id"], since))]
        if user["role"] == "pharmacy":
            rows += [dict(r) for r in conn.execute(
                "SELECT p.reference, r.outcome, r.updated_at, EXISTS (SELECT 1 FROM quotes q WHERE q.prescription_id = r.prescription_id "
                "AND q.pharmacy_id = r.pharmacy_id) AS quoted, 'prescription' AS source FROM rx_requests r JOIN prescriptions p "
                "ON p.id = r.prescription_id WHERE r.pharmacy_id = ? AND r.outcome IS NOT NULL AND r.updated_at >= ?", (user["id"], since))]
        else:
            rows += [dict(r) for r in conn.execute(
                "SELECT p.reference, r.outcome, r.updated_at, EXISTS (SELECT 1 FROM lab_quotes q WHERE q.prescription_id = r.prescription_id "
                "AND q.lab_id = r.lab_id) AS quoted, 'prescription' AS source FROM lab_requests r JOIN prescriptions p "
                "ON p.id = r.prescription_id WHERE r.lab_id = ? AND r.outcome IS NOT NULL AND r.updated_at >= ?", (user["id"], since))]
    rows.sort(key=lambda r: r["updated_at"], reverse=True)
    return {"outcomes": rows[:30], "won": sum(r["outcome"] == "chosen" for r in rows),
            "lost": sum(r["outcome"] == "chosen_other" for r in rows)}


# Imported last, so slices can depend on each other in any order
from features.prescription_uploads.logic import ACTIVE_ORDER, approved_partner, detail, distribute, email_requests, is_live, KIND, NEXT, notify, ORDER_SELECT, order_view, partner_order, QUOTE_SELECT, quote_view, reopen, upload_for  # noqa: E402
from features.chats import logic as chats  # noqa: E402
from features.health_records import api as health_records_api  # noqa: E402
from features.health_records import logic as health_records_logic  # noqa: E402
from features.labs import emails as labs_emails  # noqa: E402
from features.medicine_orders import logic as medicine_orders  # noqa: E402
from features.prescription_uploads import emails as prescription_uploads_emails  # noqa: E402
from features.prescription_uploads import reader  # noqa: E402

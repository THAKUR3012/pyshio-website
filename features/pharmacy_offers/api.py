"""Pharmacy offers: prescriptions go to nearby pharmacies, they quote, the patient compares prices.

This file: HTTP endpoints and request models.
"""
from datetime import timedelta
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import List, Literal, Optional

from shared import settings, validators as v
from shared.auth import patient_user, pharmacy_user
from shared.common import paise, stamp
from shared.db import get_db, write_txn


router = APIRouter()


class QuoteItemIn(BaseModel):
    prescription_item_id: int
    available: bool
    supplied_name: Optional[str] = Field(default=None, max_length=120)
    unit_price: float = Field(default=0, ge=0, le=1_000_000)
    mrp: Optional[float] = Field(default=None, gt=0, le=1_000_000)  # MRP per unit, shows the patient the discount

    _name = field_validator("supplied_name")(v.optional_text)


class QuoteIn(BaseModel):
    items: List[QuoteItemIn] = Field(min_length=1)
    delivery_available: bool = False
    delivery_fee: float = Field(default=0, ge=0, le=1000)
    ready_in_minutes: Optional[int] = Field(default=None, ge=0, le=2880)
    delivery_minutes: Optional[int] = Field(default=None, ge=5, le=1440)
    note: Optional[str] = Field(default=None, max_length=500)

    _note = field_validator("note")(v.optional_text)


class ShareIn(BaseModel):
    radius: Optional[float] = None
    use_home: bool = False  # move the search to the patient's current home location


@router.get("/api/prescriptions/{rx_id}/options")
def prescription_options(rx_id: str, sort: Literal["cheapest", "discount", "fastest", "nearest"] = "cheapest",
                         user: dict = Depends(patient_user)):
    """Patient view: nearby pharmacies and their prices (read-only)."""
    with get_db() as conn:
        row, _ = rx_for(conn, rx_id, user)
        if not rx_is_live(row):
            raise HTTPException(status_code=400, detail="This prescription has expired or was cancelled")
        return options_payload(conn, row, sort)


@router.post("/api/prescriptions/{rx_id}/share")
def share(rx_id: str, data: ShareIn, background: BackgroundTasks,
          sort: Literal["cheapest", "discount", "fastest", "nearest"] = "cheapest", user: dict = Depends(patient_user)):
    """The patient shares (or widens, or re-points to a new home) their prescription with nearby pharmacies."""
    allowed = [settings.NEARBY_RADIUS_KM] + settings.WIDER_RADII_KM
    if data.radius is not None and data.radius not in allowed:
        raise HTTPException(status_code=400, detail=f"Radius must be one of {allowed} km")
    with get_db() as conn:
        with write_txn(conn):
            row, _ = rx_for(conn, rx_id, user)
            if not rx_is_live(row):
                raise HTTPException(status_code=400, detail="This prescription has expired or was cancelled")
            added = share_prescription(conn, row, data.radius, data.use_home)
        email_new_requests(background, conn, rx_id, added)
        return {**options_payload(conn, conn.execute(RX_SELECT + " WHERE r.id = ?", (rx_id,)).fetchone(), sort),
                "pharmacies_notified": len(added)}


@router.get("/api/pharmacy/requests")
def pharmacy_requests(status: str = "open", user: dict = Depends(pharmacy_user)):
    sql = ("SELECT q.* FROM rx_requests q JOIN prescriptions r ON r.id = q.prescription_id "
           "WHERE q.pharmacy_id = ? AND r.status = 'active' AND r.valid_until > ?")
    params = [user["id"], stamp()]
    if status != "all":
        sql += " AND q.status = ?"
        params.append(status)
    with get_db() as conn:
        me = get_pharmacy(conn, user["id"])
        result = []
        for req in conn.execute(sql + " ORDER BY q.created_at DESC LIMIT 100", params).fetchall():
            row = conn.execute(RX_SELECT + " WHERE r.id = ?", (req["prescription_id"],)).fetchone()
            view = rx_view(conn, row, "pharmacy")
            ordered = ordered_item_ids(conn, row["id"])
            items = [i for i in rx_items(conn, row["id"]) if i["id"] not in ordered]
            quote = active_quote(conn, row["id"], user["id"])
            result.append({
                "request_id": req["id"], "status": req["status"], "distance_km": req["distance_km"],
                "received_at": req["created_at"], "prescription": view,
                "open_item_ids": [i["id"] for i in items],
                "suggested": estimate(conn, me, items, {i["id"]: substitutes(conn, i) for i in items}),
                "my_quote": quote_offer(conn, quote, rx_items(conn, row["id"])) if quote else None,
            })
    return result


@router.post("/api/pharmacy/requests/{req_id}/quote")
def send_quote(req_id: int, data: QuoteIn, background: BackgroundTasks, user: dict = Depends(pharmacy_user)):
    with get_db() as conn:
        pharmacy = get_pharmacy(conn, user["id"])
        if pharmacy["status"] != "approved":
            raise HTTPException(status_code=403, detail="Your pharmacy must be approved before quoting")
        with write_txn(conn):
            req = request_for(conn, req_id, user["id"])
            rx = conn.execute(RX_SELECT + " WHERE r.id = ?", (req["prescription_id"],)).fetchone()
            if not rx_is_live(rx):
                raise HTTPException(status_code=400, detail="This prescription is no longer active")
            items = {i["id"]: i for i in rx_items(conn, rx["id"])}
            lines = []
            for q in data.items:
                item = items.get(q.prescription_item_id)
                if not item:
                    raise HTTPException(status_code=400, detail="Quote lines must match the prescription")
                unit = paise(q.unit_price) if q.available else 0
                mrp = paise(q.mrp) if q.available and q.mrp else None
                if q.available and unit <= 0:
                    raise HTTPException(status_code=400, detail=f"Enter a price for {item['name']}")
                if mrp and unit > mrp:
                    raise HTTPException(status_code=400, detail=f"Price for {item['name']} can't be above MRP")
                lines.append((item["id"], q.available, q.supplied_name, unit, item["quantity"], unit * item["quantity"], mrp))
            if not any(l[1] for l in lines):
                raise HTTPException(status_code=400, detail="Mark at least one medicine as available, or decline instead")
            if data.delivery_available and not pharmacy["offers_delivery"]:
                raise HTTPException(status_code=400, detail="Turn on delivery in your profile first")
            if data.delivery_available and req["distance_km"] > pharmacy["delivery_radius_km"]:
                raise HTTPException(status_code=400, detail=f"This patient is {req['distance_km']:g} km away, outside your "
                                                            f"{pharmacy['delivery_radius_km']:g} km delivery radius")
            conn.execute("UPDATE quotes SET status = 'withdrawn' WHERE prescription_id = ? AND pharmacy_id = ? "
                         "AND status = 'active'", (rx["id"], user["id"]))
            cur = conn.execute(
                "INSERT INTO quotes (prescription_id, pharmacy_id, subtotal_paise, delivery_available, delivery_fee_paise, "
                "ready_in_minutes, note, status, created_at, valid_until, delivery_minutes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
                (rx["id"], user["id"], sum(l[5] for l in lines if l[1]), int(data.delivery_available),
                 paise(data.delivery_fee) if data.delivery_available else 0, data.ready_in_minutes, data.note, stamp(),
                 stamp(settings.now() + timedelta(hours=settings.QUOTE_VALID_HOURS)),
                 (data.delivery_minutes or pharmacy["delivery_minutes"]) if data.delivery_available else None))
            conn.executemany("INSERT INTO quote_items (quote_id, prescription_item_id, available, supplied_name, "
                             "unit_price_paise, quantity, line_paise, mrp_paise) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             [(cur.lastrowid, *l) for l in lines])
            conn.execute("UPDATE rx_requests SET status = 'quoted', updated_at = ? WHERE id = ?", (stamp(), req_id))
        options = price_options(conn, rx)
    # Tell the patient only when this quote now leads on price, discount or delivery speed
    tops = highlights(options)
    wins = [k for k, h in tops.items() if h and h["pharmacy_id"] == user["id"]]
    if wins:
        background.add_task(pharmacy_offers_emails.send_cheaper_quote, {"email": rx["patient_email"]}, rx["reference"],
                            tops[wins[0]], wins)
    return {"ok": True, "quote_id": cur.lastrowid}


@router.post("/api/pharmacy/requests/{req_id}/decline")
def decline_request(req_id: int, user: dict = Depends(pharmacy_user)):
    with get_db() as conn:
        with write_txn(conn):
            req = request_for(conn, req_id, user["id"])
            conn.execute("UPDATE rx_requests SET status = 'declined', updated_at = ? WHERE id = ?", (stamp(), req["id"]))
            conn.execute("UPDATE quotes SET status = 'withdrawn' WHERE prescription_id = ? AND pharmacy_id = ? "
                         "AND status = 'active'", (req["prescription_id"], user["id"]))
    return {"ok": True}


# Imported last, so slices can depend on each other in any order
from features.pharmacies.logic import get_pharmacy  # noqa: E402
from features.pharmacy_offers.logic import active_quote, email_new_requests, estimate, highlights, options_payload, price_options, quote_offer, request_for, share_prescription, substitutes  # noqa: E402
from features.prescriptions.logic import ordered_item_ids, rx_for, rx_is_live, rx_items, RX_SELECT, rx_view  # noqa: E402
from features.pharmacy_offers import emails as pharmacy_offers_emails  # noqa: E402

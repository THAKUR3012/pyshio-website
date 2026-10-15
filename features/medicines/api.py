"""Medicines: the shared catalogue and each pharmacy's price list / stock.

This file: HTTP endpoints and request models.
"""
import csv
import io
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional

from shared import auth, reference_data as ref, salts, validators as v
from shared.auth import admin_user, pharmacy_user
from shared.common import csv_safe, paise, stamp
from shared.db import get_db, write_txn

router = APIRouter()


class MedicineIn(BaseModel):
    name: str
    generic_name: Optional[str] = None
    strength: Optional[str] = None
    form: Optional[str] = None
    pack_size: int = Field(default=1, ge=1, le=1000)
    rx_required: bool = True
    mrp: Optional[float] = Field(default=None, gt=0, le=1_000_000)  # printed MRP per pack
    care_area: Optional[str] = None
    manufacturer: Optional[str] = Field(default=None, max_length=120)

    _name = field_validator("name")(v.required_text("Medicine name", 120))
    _generic = field_validator("generic_name", "strength", "manufacturer")(v.optional_text)

    @field_validator("care_area")
    @classmethod
    def _care_area(cls, value):
        value = v.optional_text(value)
        if value and value not in ref.CARE_AREAS:
            raise ValueError("Unknown care area")
        return value

    @field_validator("form")
    @classmethod
    def _form(cls, value):
        value = (value or "").strip().lower() or None
        if value and value not in FORMS:
            raise ValueError(f"Form must be one of: {', '.join(FORMS)}")
        return value


class StockIn(BaseModel):
    medicine_id: Optional[int] = None
    medicine: Optional[MedicineIn] = None
    mrp: float = Field(gt=0, le=1_000_000)
    price: float = Field(gt=0, le=1_000_000)
    in_stock: bool = True

    @model_validator(mode="after")
    def _check(self):
        if not (self.medicine_id or self.medicine):
            raise ValueError("Choose a medicine")
        if self.price > self.mrp:
            raise ValueError("Selling price can't be above MRP")
        return self


class StockImport(BaseModel):
    csv: str = Field(max_length=2_000_000)


@router.get("/api/medicines")
def search_medicines(q: str = "", limit: int = Query(20, ge=1, le=100),
                     user: dict = Depends(auth.require("doctor", "pharmacy", "admin"))):
    like = f"%{q.strip().lower()}%"
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM medicines WHERE lower(name) LIKE ? OR lower(COALESCE(generic_name, '')) LIKE ? "
                            "ORDER BY (lower(name) LIKE ?) DESC, name LIMIT ?",
                            (like, like, q.strip().lower() + "%", limit)).fetchall()
    return [medicine_view(r) for r in rows]


@router.post("/api/medicines")
def add_medicine(data: MedicineIn, user: dict = Depends(auth.require("doctor", "pharmacy", "admin"))):
    with get_db() as conn:
        with write_txn(conn):
            try:
                med_id = find_or_create_medicine(conn, data, user["id"], is_trusted(conn, user))
            except ValueError as e:
                raise HTTPException(status_code=403, detail=str(e))
        return medicine_view(conn.execute("SELECT * FROM medicines WHERE id = ?", (med_id,)).fetchone())


@router.put("/api/admin/medicines/{medicine_id}")
def admin_update_medicine(medicine_id: int, data: MedicineIn, user: dict = Depends(admin_user)):
    with get_db() as conn:
        cur = conn.execute("UPDATE medicines SET name = ?, generic_name = ?, strength = ?, form = ?, pack_size = ?, "
                           "rx_required = ?, mrp_paise = ?, care_area = ?, manufacturer = COALESCE(?, manufacturer) "
                           "WHERE id = ?",
                           (data.name, data.generic_name, data.strength, data.form, data.pack_size,
                            int(data.rx_required), paise(data.mrp) if data.mrp else None,
                            data.care_area or ref.care_area_for(data.generic_name), data.manufacturer, medicine_id))
        if not cur.rowcount:
            raise HTTPException(status_code=404, detail="Medicine not found")
        salts.set_keys(conn, medicine_id)
        return medicine_view(conn.execute("SELECT * FROM medicines WHERE id = ?", (medicine_id,)).fetchone())


@router.get("/api/pharmacy/stock")
def list_stock(q: str = "", user: dict = Depends(pharmacy_user)):
    like = f"%{q.strip().lower()}%"
    with get_db() as conn:
        rows = conn.execute(STOCK_SELECT + " WHERE s.pharmacy_id = ? AND (lower(m.name) LIKE ? OR "
                            "lower(COALESCE(m.generic_name, '')) LIKE ?) ORDER BY m.name", (user["id"], like, like))
        return [stock_view(r) for r in rows]


@router.put("/api/pharmacy/stock")
def save_stock(data: StockIn, user: dict = Depends(pharmacy_user)):
    with get_db() as conn:
        with write_txn(conn):
            trusted = is_trusted(conn, user)
            med_id = data.medicine_id
            if med_id:
                if not conn.execute("SELECT 1 FROM medicines WHERE id = ?", (med_id,)).fetchone():
                    raise HTTPException(status_code=404, detail="Medicine not found")
            else:
                try:
                    med_id = find_or_create_medicine(conn, data.medicine, user["id"], trusted)
                except ValueError as e:
                    raise HTTPException(status_code=403, detail=str(e))
            upsert_stock(conn, user["id"], med_id, data.mrp, data.price, data.in_stock, trusted)
        return stock_view(conn.execute(STOCK_SELECT + " WHERE s.pharmacy_id = ? AND s.medicine_id = ?",
                                       (user["id"], med_id)).fetchone())


class OnHandIn(BaseModel):
    on_hand: Optional[int] = Field(default=None, ge=0, le=1_000_000)  # units (tablets) on the shelf; null = not tracked


@router.put("/api/pharmacy/stock/{medicine_id}/on-hand")
def save_on_hand(medicine_id: int, data: OnHandIn, user: dict = Depends(pharmacy_user)):
    """How many units are on the shelf, so the forecast can suggest how much to reorder."""
    with get_db() as conn:
        cur = conn.execute("UPDATE pharmacy_stock SET on_hand = ?, updated_at = ? WHERE pharmacy_id = ? AND medicine_id = ?",
                           (data.on_hand, stamp(), user["id"], medicine_id))
        if not cur.rowcount:
            raise HTTPException(status_code=404, detail="Add this medicine to your price list first")
        row = conn.execute(STOCK_SELECT + " WHERE s.pharmacy_id = ? AND s.medicine_id = ?", (user["id"], medicine_id)).fetchone()
        return {**stock_view(row), "on_hand": row["on_hand"]}


@router.delete("/api/pharmacy/stock/{medicine_id}")
def delete_stock(medicine_id: int, user: dict = Depends(pharmacy_user)):
    with get_db() as conn:
        conn.execute("DELETE FROM pharmacy_stock WHERE pharmacy_id = ? AND medicine_id = ?", (user["id"], medicine_id))
    return {"ok": True}


@router.post("/api/pharmacy/stock/import")
def import_stock(data: StockImport, user: dict = Depends(pharmacy_user)):
    """Bulk price list upload. Columns: name, generic_name, strength, form, pack_size, mrp, price, in_stock."""
    reader = csv.DictReader(io.StringIO(data.csv.lstrip("﻿")))
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in ("name", "mrp", "price") if c not in headers]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing column(s): {', '.join(missing)}. Expected: {', '.join(CSV_COLUMNS)}")
    imported, errors = 0, []
    with get_db() as conn:
        trusted = is_trusted(conn, user)
        with write_txn(conn):
            for line_no, raw in enumerate(reader, start=2):
                row = {(k or "").strip().lower(): (val or "").strip() for k, val in raw.items()}
                if not any(row.values()):
                    continue
                try:
                    med = MedicineIn(name=row.get("name"), generic_name=row.get("generic_name"), strength=row.get("strength"),
                                     form=row.get("form"), pack_size=int(row.get("pack_size") or 1))
                    stock = StockIn(medicine=med, mrp=float(row["mrp"]), price=float(row["price"]),
                                    in_stock=(row.get("in_stock") or "yes").lower() not in ("0", "no", "false", "n"))
                    upsert_stock(conn, user["id"], find_or_create_medicine(conn, med, user["id"], trusted),
                                 stock.mrp, stock.price, stock.in_stock, trusted)
                    imported += 1
                except (ValueError, KeyError) as e:
                    errors.append({"line": line_no, "error": str(e).split("\n")[-1][:200]})
                if len(errors) >= 50:
                    break
    return {"imported": imported, "errors": errors}


@router.get("/api/pharmacy/stock.csv")
def export_stock(user: dict = Depends(pharmacy_user)):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    with get_db() as conn:
        for r in conn.execute(STOCK_SELECT + " WHERE s.pharmacy_id = ? ORDER BY m.name", (user["id"],)):
            writer.writerow([csv_safe(r["name"]), csv_safe(r["generic_name"]), csv_safe(r["strength"]),
                             csv_safe(r["form"]), r["pack_size"], f"{r['mrp_paise'] / 100:.2f}",
                             f"{r['price_paise'] / 100:.2f}", "yes" if r["in_stock"] else "no"])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="price-list.csv"'})


@router.get("/api/admin/medicines")
def admin_medicines(q: str = "", user: dict = Depends(admin_user)):
    like = f"%{q.strip().lower()}%"
    with get_db() as conn:
        rows = conn.execute("SELECT m.*, (SELECT COUNT(*) FROM pharmacy_stock s WHERE s.medicine_id = m.id) AS stocked_by "
                            "FROM medicines m WHERE lower(m.name) LIKE ? OR lower(COALESCE(m.generic_name, '')) LIKE ? "
                            "ORDER BY m.name LIMIT 300", (like, like)).fetchall()
        return [medicine_view(r) | {"stocked_by": r["stocked_by"]} for r in rows]


# Imported last, so slices can depend on each other in any order
from features.medicines.logic import CSV_COLUMNS, find_or_create_medicine, FORMS, is_trusted, medicine_view, STOCK_SELECT, stock_view, upsert_stock  # noqa: E402

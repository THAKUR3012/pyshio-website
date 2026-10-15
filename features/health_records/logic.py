"""Health records: reports, files and other medicines in one place for the patient.

This file: logic and queries.
"""
from __future__ import annotations

import secrets
from datetime import date
from fastapi import HTTPException
from pathlib import Path
from typing import List, Optional

from shared import salts, settings
from shared.common import stamp


# File signatures we accept (checked on the bytes, not the name the browser sent)
SIGNATURES = [(b"%PDF-", "application/pdf", ".pdf"), (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
              (b"\xff\xd8\xff", "image/jpeg", ".jpg")]
KINDS = ("lab_report", "other_report", "prescription_copy", "medicine_bill", "other")
PATIENT_KINDS = ("other_report", "prescription_copy", "medicine_bill", "other")


# ---------- Files ----------
def upload_root() -> Path:
    root = Path(settings.UPLOAD_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def store_file(content: bytes, original_name: Optional[str]) -> dict:
    """Validate and save an upload; returns what to record. Raises 400 on a bad file."""
    if not content:
        raise HTTPException(status_code=400, detail="The file is empty")
    if len(content) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Files must be under {settings.MAX_UPLOAD_MB} MB")
    match = next(((ctype, ext) for sig, ctype, ext in SIGNATURES if content.startswith(sig)), None)
    if not match:
        raise HTTPException(status_code=400, detail="Upload a PDF, JPG or PNG file")
    ctype, ext = match
    now = settings.now()
    rel = Path(f"{now:%Y}") / f"{now:%m}" / f"{secrets.token_hex(16)}{ext}"
    target = upload_root() / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    name = Path(original_name or f"report{ext}").name[:120]
    if not name.lower().endswith(ext) and not (ext == ".jpg" and name.lower().endswith(".jpeg")):
        name += ext
    return {"file_path": rel.as_posix(), "file_name": name, "content_type": ctype, "size_bytes": len(content)}


def read_file(rel: Optional[str]) -> Optional[bytes]:
    """The stored bytes of an upload (None when missing or outside the upload folder)."""
    if not rel:
        return None
    path = (upload_root() / rel).resolve()
    if upload_root().resolve() in path.parents and path.exists():
        return path.read_bytes()
    return None


def remove_file(rel: Optional[str]) -> None:
    if not rel:
        return
    path = (upload_root() / rel).resolve()
    if upload_root().resolve() in path.parents and path.exists():
        path.unlink()


def to_number(text: str) -> Optional[float]:
    try:
        return float(text.replace(",", "").strip())
    except ValueError:
        return None


def flag_for(value: Optional[float], low: Optional[float], high: Optional[float]) -> Optional[str]:
    if value is None or (low is None and high is None):
        return None
    if low is not None and value < low:
        return "low"
    if high is not None and value > high:
        return "high"
    return "normal"


def create_record(conn, patient_id: int, kind: str, title: str, record_date: str, stored: Optional[dict], user: dict,
                  lab_booking_id=None, lab_id=None, doctor_id=None, notes=None, values: List[ValueIn] = (),
                  share: bool = True, family_member_id: Optional[int] = None) -> int:
    stored = stored or {}
    if lab_booking_id and not family_member_id:
        # a lab report belongs to whoever the prescription was for (features/family)
        row = conn.execute("SELECT r.family_member_id FROM lab_bookings b JOIN prescriptions r ON r.id = b.prescription_id "
                           "WHERE b.id = ?", (lab_booking_id,)).fetchone()
        family_member_id = row["family_member_id"] if row else None
    cur = conn.execute(
        "INSERT INTO health_records (patient_id, kind, title, record_date, file_path, file_name, content_type, size_bytes, "
        "uploaded_by, uploader_role, lab_booking_id, lab_id, doctor_id, share_with_doctors, notes, created_at, "
        "family_member_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (patient_id, kind, title, record_date, stored.get("file_path"), stored.get("file_name"), stored.get("content_type"),
         stored.get("size_bytes"), user["id"], user["role"], lab_booking_id, lab_id, doctor_id, int(share), notes, stamp(),
         family_member_id))
    for val in values:
        test = conn.execute("SELECT * FROM lab_tests WHERE id = ?", (val.test_id,)).fetchone() if val.test_id else None
        low = val.ref_low if val.ref_low is not None else (test["ref_low"] if test else None)
        high = val.ref_high if val.ref_high is not None else (test["ref_high"] if test else None)
        number = to_number(val.value)
        conn.execute("INSERT INTO record_values (record_id, test_id, name, value_num, value_text, unit, ref_low, ref_high, flag) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (cur.lastrowid, val.test_id, val.name, number, None if number is not None else val.value,
                      val.unit or (test["unit"] if test else None), low, high, flag_for(number, low, high)))
    return cur.lastrowid


# ---------- Views & access ----------
RECORD_SELECT = """SELECT h.*, l.lab_name, du.name AS doctor_name, b.reference AS booking_reference, fm.name AS member_name
                   FROM health_records h LEFT JOIN labs l ON l.user_id = h.lab_id
                   LEFT JOIN family_members fm ON fm.id = h.family_member_id
                   LEFT JOIN users du ON du.id = h.doctor_id LEFT JOIN lab_bookings b ON b.id = h.lab_booking_id"""


def record_values(conn, record_id: int) -> list:
    return [{k: r[k] for k in ("name", "value_num", "value_text", "unit", "ref_low", "ref_high", "flag", "test_id")}
            for r in conn.execute("SELECT * FROM record_values WHERE record_id = ? ORDER BY id", (record_id,))]


def record_view(conn, row) -> dict:
    view = {k: row[k] for k in ("id", "kind", "title", "record_date", "file_name", "content_type", "size_bytes",
                                "uploader_role", "lab_name", "doctor_name", "booking_reference", "notes", "created_at")}
    view.update(share_with_doctors=bool(row["share_with_doctors"]), has_file=bool(row["file_path"]),
                file_url=f"/api/records/{row['id']}/file" if row["file_path"] else None,
                values=record_values(conn, row["id"]), family_member_id=row["family_member_id"],
                for_name=row["member_name"])
    return view


def doctor_sees_patient(conn, doctor_id: int, patient_id: int) -> bool:
    return bool(conn.execute("SELECT 1 FROM appointments WHERE doctor_id = ? AND patient_id = ? "
                             "AND status IN ('confirmed', 'completed', 'no_show')", (doctor_id, patient_id)).fetchone())


def partner_sees_upload(conn, user: dict, record_id: int) -> bool:
    """A pharmacy or lab may open an uploaded prescription while it's asking them for a quote, or once they got the order."""
    return bool(conn.execute(
        "SELECT 1 FROM rx_uploads u WHERE u.record_id = ? AND ("
        " EXISTS (SELECT 1 FROM upload_requests q WHERE q.upload_id = u.id AND q.partner_id = ? AND q.status IN ('open', 'quoted')"
        "         AND u.status = 'open') OR"
        " EXISTS (SELECT 1 FROM upload_orders o WHERE o.upload_id = u.id AND o.partner_id = ? AND o.status != 'cancelled'))",
        (record_id, user["id"], user["id"])).fetchone())


def can_view(conn, user: dict, row) -> bool:
    if row["deleted_at"]:
        return False
    if user["role"] == "patient":
        return row["patient_id"] == user["id"]
    if user["role"] in ("pharmacy", "lab") and row["kind"] == "prescription_copy" and partner_sees_upload(conn, user, row["id"]):
        return True  # a prescription the patient uploaded for quotes, sent to this pharmacy / lab
    if user["role"] == "lab":
        return row["lab_id"] == user["id"] and row["uploaded_by"] == user["id"]
    if user["role"] == "doctor":
        return row["doctor_id"] == user["id"] or (bool(row["share_with_doctors"])
                                                  and doctor_sees_patient(conn, user["id"], row["patient_id"]))
    return False  # admins, pharmacies, companies and MRs never open health records


def own_record(conn, record_id: int, user: dict):
    row = conn.execute(RECORD_SELECT + " WHERE h.id = ? AND h.patient_id = ? AND h.deleted_at IS NULL",
                       (record_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Record not found")
    return row


def trends_for(conn, patient_id: int, doctor_id: int = None) -> list:
    """Numeric results over time, one series per test (e.g. HbA1c), oldest first."""
    sql = ("SELECT h.record_date, h.id AS record_id, rv.* FROM record_values rv JOIN health_records h ON h.id = rv.record_id "
           "WHERE h.patient_id = ? AND h.deleted_at IS NULL AND rv.value_num IS NOT NULL")
    params = [patient_id]
    rows = conn.execute(sql + " ORDER BY h.record_date, h.id", params).fetchall()
    series = {}
    for r in rows:
        if doctor_id is not None:
            rec = conn.execute("SELECT * FROM health_records WHERE id = ?", (r["record_id"],)).fetchone()
            if not can_view(conn, {"role": "doctor", "id": doctor_id}, rec):
                continue
        # The same catalogue test is one series even when labs name it differently
        key = f"test:{r['test_id']}" if r["test_id"] else r["name"].strip().lower()
        s = series.setdefault(key, {"name": r["name"], "unit": r["unit"], "ref_low": r["ref_low"], "ref_high": r["ref_high"],
                                    "test_id": r["test_id"], "points": []})
        s["points"].append({"date": r["record_date"], "value": r["value_num"], "flag": r["flag"]})
    for s in series.values():
        s.update(trend_of(s))
    return sorted(series.values(), key=lambda s: (-len(s["points"]), s["name"]))


def trend_of(series: dict) -> dict:
    """Least-squares slope per month, a direction (up / down / flat) and whether the latest move is toward normal."""
    pts = [(date.fromisoformat(p["date"][:10]), p["value"]) for p in series["points"] if p["value"] is not None]
    if len(pts) < 2:
        return {"slope_per_month": None, "direction": None, "toward_normal": None}
    xs = [(d - pts[0][0]).days for d, _ in pts]
    ys = [v for _, v in pts]
    n, mx, my = len(xs), sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:  # same day: compare first and last
        slope_day = 0.0
        change = ys[-1] - ys[0]
    else:
        slope_day = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        change = slope_day * (xs[-1] - xs[0])
    low, high = series.get("ref_low"), series.get("ref_high")
    scale = (high - low) if low is not None and high is not None and high > low else (abs(my) or 1)
    direction = "flat" if abs(change) < 0.03 * scale else ("up" if change > 0 else "down")
    latest = ys[-1]
    toward = None
    if direction != "flat":
        if high is not None and (latest > high or ys[0] > high):
            toward = direction == "down"
        elif low is not None and (latest < low or ys[0] < low):
            toward = direction == "up"
    return {"slope_per_month": round(slope_day * 30, 3), "direction": direction, "toward_normal": toward}


# ---------- Other medicines ----------
def link_catalogue(conn, med_id: int) -> None:
    """Match a medicine the patient typed to the catalogue, so doctors' safety checks know its salt."""
    row = conn.execute("SELECT name, strength FROM patient_medicines WHERE id = ?", (med_id,)).fetchone()
    found = salts.best_match(conn, row["name"], row["strength"], limit=1) if row else []
    best = found[0] if found and found[0]["confidence"] >= 0.85 else None
    conn.execute("UPDATE patient_medicines SET medicine_id = ?, generic_name = ? WHERE id = ?",
                 (best["medicine_id"] if best else None, best["generic_name"] if best else None, med_id))


def medicine_view(row) -> dict:
    return {k: row[k] for k in ("id", "name", "strength", "dosage", "since", "prescribed_by", "notes", "created_at",
                                "updated_at")} | {"active": bool(row["active"])}


def delete_patient_files(conn, patient_id: int) -> None:
    """Account closure: the patient's own files go; lab reports are kept (medical records) without contact details."""
    for r in conn.execute("SELECT id, file_path FROM health_records WHERE patient_id = ? AND kind != 'lab_report' "
                          "AND deleted_at IS NULL", (patient_id,)).fetchall():
        remove_file(r["file_path"])
        conn.execute("UPDATE health_records SET deleted_at = ?, file_path = NULL WHERE id = ?", (stamp(), r["id"]))
    conn.execute("DELETE FROM patient_medicines WHERE patient_id = ?", (patient_id,))


# Imported last, so slices can depend on each other in any order
from features.health_records.api import ValueIn  # noqa: E402

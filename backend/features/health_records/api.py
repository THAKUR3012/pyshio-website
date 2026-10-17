"""Health records: reports, files and other medicines in one place for the patient.

This file: HTTP endpoints and request models.
"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional

from shared import auth, settings, validators as v
from shared.auth import doctor_user, patient_user
from shared.common import stamp
from shared.db import get_db, write_txn

router = APIRouter()


class ValueIn(BaseModel):
    test_id: Optional[int] = None
    name: str
    value: str = Field(max_length=60)
    unit: Optional[str] = Field(default=None, max_length=20)
    ref_low: Optional[float] = None
    ref_high: Optional[float] = None

    _name = field_validator("name")(v.required_text("Result name", 80))
    _value = field_validator("value")(v.required_text("Result value", 60))
    _unit = field_validator("unit")(v.optional_text)


class RecordUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=1000)
    share_with_doctors: Optional[bool] = None

    _texts = field_validator("title", "notes")(v.optional_text)


class MedicineIn(BaseModel):
    name: str
    strength: Optional[str] = Field(default=None, max_length=40)
    dosage: Optional[str] = Field(default=None, max_length=100)
    since: Optional[str] = None
    prescribed_by: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=300)
    active: bool = True

    _name = field_validator("name")(v.required_text("Medicine name", 120))
    _texts = field_validator("strength", "dosage", "prescribed_by", "notes")(v.optional_text)

    @field_validator("since")
    @classmethod
    def _since(cls, value):
        value = v.optional_text(value)
        return v.iso_date(value) if value else None


@router.get("/api/records/{record_id}/file")
def record_file(record_id: int, download: bool = False, user: dict = Depends(auth.require())):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM health_records WHERE id = ?", (record_id,)).fetchone()
        if not row or not row["file_path"] or not can_view(conn, user, row):
            raise HTTPException(status_code=404, detail="Record not found")
    path = (upload_root() / row["file_path"]).resolve()
    if upload_root().resolve() not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type=row["content_type"], filename=row["file_name"],
                        content_disposition_type="attachment" if download else "inline",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@router.get("/api/my/records")
def my_records(user: dict = Depends(patient_user)):
    with get_db() as conn:
        rows = conn.execute(RECORD_SELECT + " WHERE h.patient_id = ? AND h.deleted_at IS NULL "
                            "ORDER BY h.record_date DESC, h.id DESC", (user["id"],)).fetchall()
        return [record_view(conn, r) for r in rows]


@router.post("/api/my/records")
async def add_my_record(file: Optional[UploadFile] = File(None), title: str = Form(...),
                        kind: Literal["other_report", "prescription_copy", "medicine_bill", "other"] = Form("other_report"),
                        record_date: Optional[str] = Form(None), notes: Optional[str] = Form(None),
                        share_with_doctors: bool = Form(True), family_member_id: Optional[int] = Form(None),
                        user: dict = Depends(patient_user)):
    """The patient keeps their own files here: old reports, outside prescriptions, bills."""
    try:
        title = v.required_text("Title", 200)(title)
        record_date = v.iso_date(record_date) if v.optional_text(record_date) else settings.now().date().isoformat()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if record_date > settings.now().date().isoformat():
        raise HTTPException(status_code=422, detail="The date can't be in the future")
    stored = None
    if file is not None and file.filename:
        stored = store_file(await file.read(settings.MAX_UPLOAD_MB * 1024 * 1024 + 1), file.filename)
    with get_db() as conn:
        member_for(conn, user["id"], family_member_id)  # only the patient's own family
        with write_txn(conn):
            record_id = create_record(conn, user["id"], kind, title, record_date, stored, user,
                                      notes=v.optional_text(notes), share=share_with_doctors,
                                      family_member_id=family_member_id)
        return record_view(conn, conn.execute(RECORD_SELECT + " WHERE h.id = ?", (record_id,)).fetchone())


@router.patch("/api/my/records/{record_id}")
def update_my_record(record_id: int, data: RecordUpdate, user: dict = Depends(patient_user)):
    with get_db() as conn:
        row = own_record(conn, record_id, user)
        changes = {}
        if data.share_with_doctors is not None:
            changes["share_with_doctors"] = int(data.share_with_doctors)
        if row["kind"] != "lab_report":  # a lab's report keeps the lab's title and notes
            if data.title:
                changes["title"] = data.title
            if data.notes is not None:
                changes["notes"] = data.notes
        if changes:
            conn.execute(f"UPDATE health_records SET {', '.join(f'{k} = ?' for k in changes)} WHERE id = ?",
                         (*changes.values(), record_id))
        return record_view(conn, conn.execute(RECORD_SELECT + " WHERE h.id = ?", (record_id,)).fetchone())


@router.delete("/api/my/records/{record_id}")
def delete_my_record(record_id: int, user: dict = Depends(patient_user)):
    with get_db() as conn:
        row = own_record(conn, record_id, user)
        if row["kind"] == "lab_report":
            raise HTTPException(status_code=400, detail="Lab reports are kept as medical records. You can stop sharing them instead.")
        conn.execute("UPDATE health_records SET deleted_at = ? WHERE id = ?", (stamp(), record_id))
    remove_file(row["file_path"])
    return {"ok": True}


@router.get("/api/my/trends")
def my_trends(user: dict = Depends(patient_user)):
    with get_db() as conn:
        return trends_for(conn, user["id"])


@router.get("/api/my/medicines")
def my_medicines(user: dict = Depends(patient_user)):
    with get_db() as conn:
        return [medicine_view(r) for r in conn.execute(
            "SELECT * FROM patient_medicines WHERE patient_id = ? ORDER BY active DESC, name", (user["id"],))]


@router.post("/api/my/medicines")
def add_my_medicine(data: MedicineIn, user: dict = Depends(patient_user)):
    with get_db() as conn:
        if conn.execute("SELECT COUNT(*) FROM patient_medicines WHERE patient_id = ?", (user["id"],)).fetchone()[0] >= 100:
            raise HTTPException(status_code=400, detail="You can keep up to 100 medicines here")
        cur = conn.execute("INSERT INTO patient_medicines (patient_id, name, strength, dosage, since, prescribed_by, notes, "
                           "active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (user["id"], data.name, data.strength, data.dosage, data.since, data.prescribed_by, data.notes,
                            int(data.active), stamp(), stamp()))
        link_catalogue(conn, cur.lastrowid)
        return medicine_view(conn.execute("SELECT * FROM patient_medicines WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.put("/api/my/medicines/{med_id}")
def update_my_medicine(med_id: int, data: MedicineIn, user: dict = Depends(patient_user)):
    with get_db() as conn:
        cur = conn.execute("UPDATE patient_medicines SET name = ?, strength = ?, dosage = ?, since = ?, prescribed_by = ?, "
                           "notes = ?, active = ?, updated_at = ? WHERE id = ? AND patient_id = ?",
                           (data.name, data.strength, data.dosage, data.since, data.prescribed_by, data.notes,
                            int(data.active), stamp(), med_id, user["id"]))
        if not cur.rowcount:
            raise HTTPException(status_code=404, detail="Medicine not found")
        link_catalogue(conn, med_id)
        return medicine_view(conn.execute("SELECT * FROM patient_medicines WHERE id = ?", (med_id,)).fetchone())


@router.delete("/api/my/medicines/{med_id}")
def delete_my_medicine(med_id: int, user: dict = Depends(patient_user)):
    with get_db() as conn:
        conn.execute("DELETE FROM patient_medicines WHERE id = ? AND patient_id = ?", (med_id, user["id"]))
    return {"ok": True}


# ---------- Doctor's view of a patient's records ----------
@router.get("/api/doctor/patients/{patient_id}/records")
def patient_records_for_doctor(patient_id: int, user: dict = Depends(doctor_user)):
    with get_db() as conn:
        if not doctor_sees_patient(conn, user["id"], patient_id) and not conn.execute(
                "SELECT 1 FROM health_records WHERE patient_id = ? AND doctor_id = ?", (patient_id, user["id"])).fetchone():
            raise HTTPException(status_code=404, detail="Patient not found")
        rows = conn.execute(RECORD_SELECT + " WHERE h.patient_id = ? AND h.deleted_at IS NULL "
                            "ORDER BY h.record_date DESC, h.id DESC", (patient_id,)).fetchall()
        visible = [record_view(conn, r) for r in rows if can_view(conn, user, r)]
        meds = [medicine_view(r) for r in conn.execute(
            "SELECT * FROM patient_medicines WHERE patient_id = ? AND active = 1 ORDER BY name", (patient_id,))] \
            if doctor_sees_patient(conn, user["id"], patient_id) else []
        return {"records": visible, "trends": trends_for(conn, patient_id, doctor_id=user["id"]), "medicines": meds}


# Imported last, so slices can depend on each other in any order
from features.health_records.logic import can_view, create_record, doctor_sees_patient, link_catalogue, medicine_view, own_record, RECORD_SELECT, record_view, remove_file, store_file, trends_for, upload_root  # noqa: E402
from features.family.logic import member_for  # noqa: E402

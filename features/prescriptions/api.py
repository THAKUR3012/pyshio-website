"""Prescriptions: doctors write them (medicines, tests, diagnoses, refills); patients and pharmacies read them.

This file: HTTP endpoints and request models.
"""
import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Literal, Optional

from shared import auth, settings, validators as v
from shared.auth import doctor_user, patient_user
from shared.common import stamp
from shared.db import get_db, write_txn


from features.follow_ups.logic import check_follow_up_date

router = APIRouter()


class RxItemIn(BaseModel):
    medicine_id: Optional[int] = None
    name: Optional[str] = None
    generic_name: Optional[str] = None
    strength: Optional[str] = None
    form: Optional[str] = None
    dosage: Optional[str] = Field(default=None, max_length=100)
    duration: Optional[str] = Field(default=None, max_length=100)
    quantity: int = Field(ge=1, le=1000)
    instructions: Optional[str] = Field(default=None, max_length=300)
    allow_substitute: bool = True
    duration_days: Optional[int] = Field(default=None, ge=1, le=365)
    daily_units: Optional[float] = Field(default=None, gt=0, le=24)
    is_long_term: bool = False

    _texts = field_validator("name", "generic_name", "strength", "form", "dosage", "duration", "instructions")(v.optional_text)

    @model_validator(mode="after")
    def _named(self):
        if not (self.medicine_id or self.name):
            raise ValueError("Each medicine needs a name")
        # Work the course length out from the usual shorthand when the doctor didn't fill it in
        self.daily_units = self.daily_units or daily_units_from(self.dosage)
        self.duration_days = self.duration_days or days_from(self.duration)
        return self


class RxTestIn(BaseModel):
    test_id: Optional[int] = None
    name: Optional[str] = Field(default=None, max_length=120)
    instructions: Optional[str] = Field(default=None, max_length=300)

    _texts = field_validator("name", "instructions")(v.optional_text)

    @model_validator(mode="after")
    def _named(self):
        if not (self.test_id or self.name):
            raise ValueError("Each test needs a name")
        return self


class PrescriptionIn(BaseModel):
    patient_age: Optional[int] = Field(default=None, ge=0, le=120)
    patient_sex: Optional[Literal["M", "F", "O"]] = None
    diagnosis: Optional[str] = Field(default=None, max_length=1000)
    advice: Optional[str] = Field(default=None, max_length=2000)
    condition_ids: List[int] = Field(default=[], max_length=10)
    refills_allowed: int = Field(default=0, ge=0, le=settings.MAX_REFILLS)
    items: List[RxItemIn] = Field(default=[], max_length=30)
    tests: List[RxTestIn] = Field(default=[], max_length=20)
    follow_up_on: Optional[str] = None  # date the doctor wants to see the patient again (features/follow_ups)
    pregnancy: Optional[Literal["pregnant", "breastfeeding"]] = None  # for the safety check (features/rx_safety)
    acknowledged_alerts: List[str] = Field(default=[], max_length=50)  # codes of major safety alerts the doctor reviewed

    _texts = field_validator("diagnosis", "advice")(v.optional_text)
    _follow_up = field_validator("follow_up_on")(check_follow_up_date)

    @model_validator(mode="after")
    def _refills(self):
        if not self.items and not self.tests:
            raise ValueError("Add at least one medicine or test")
        self.condition_ids = list(dict.fromkeys(self.condition_ids))
        if self.refills_allowed and not any(i.is_long_term for i in self.items):
            raise ValueError("Mark which medicines are long-term to allow refills")
        return self


@router.get("/api/conditions")
def list_conditions():
    """The diagnosis list doctors pick from, grouped by care area on screen."""
    with get_db() as conn:
        return [dict(r) for r in conn.execute("SELECT id, code, name, care_area FROM conditions ORDER BY care_area, name")]


@router.post("/api/appointments/{appt_id}/prescriptions")
def issue_prescription(appt_id: str, data: PrescriptionIn, background: BackgroundTasks,
                       user: dict = Depends(doctor_user)):
    with get_db() as conn:
        doctor = conn.execute("SELECT * FROM doctors WHERE user_id = ?", (user["id"],)).fetchone()
        if doctor["category"] not in settings.PRESCRIBING_CATEGORIES:
            raise HTTPException(status_code=403, detail="Your doctor category can't issue medicine prescriptions on this platform")
        if doctor["status"] != "approved":
            raise HTTPException(status_code=403, detail="Your profile must be approved before you can prescribe")
        appt = conn.execute("SELECT * FROM appointments WHERE id = ? AND doctor_id = ?", (appt_id, user["id"])).fetchone()
        if not appt:
            raise HTTPException(status_code=404, detail="Appointment not found")
        if appt["status"] not in ("confirmed", "completed"):
            raise HTTPException(status_code=400, detail="Prescriptions can only be written for confirmed or completed appointments")
        patient = conn.execute("SELECT * FROM users WHERE id = ?", (appt["patient_id"],)).fetchone()

        # Safety check (interactions, duplicates, allergies, age, pregnancy, dose): major alerts need acknowledging
        profile = profile_for(conn, patient["id"], appt["family_member_id"])
        safety = rx_safety.check(conn, items=[i.model_dump() for i in data.items], patient_id=patient["id"],
                                 family_member_id=appt["family_member_id"],
                                 age=data.patient_age if data.patient_age is not None else profile["age"],
                                 pregnancy=rx_safety.pregnancy_from(conn, data.condition_ids, data.pregnancy),
                                 allergies=profile["allergies"], who=profile["name"])
        blocking = rx_safety.unacknowledged(safety["alerts"], data.acknowledged_alerts)
        if blocking:
            raise HTTPException(status_code=409, detail={
                "message": "Please review the safety alerts before issuing this prescription.", "alerts": safety["alerts"]})

        # Prefer the patient's home; fall back to the clinic so pharmacies near the clinic still help
        if patient["home_lat"] is not None:
            lat, lng, source = patient["home_lat"], patient["home_lng"], "home"
        elif doctor["lat"] is not None:
            lat, lng, source = doctor["lat"], doctor["lng"], "clinic"
        else:
            lat = lng = source = None

        with write_txn(conn):
            rx_id = create_prescription(conn, appt_id, user["id"], patient["id"], data, lat, lng, source)
            if safety["alerts"] or data.pregnancy:
                conn.execute("UPDATE prescriptions SET pregnancy = ?, safety_alerts = ?, safety_ack_at = ? WHERE id = ?",
                             (data.pregnancy, json.dumps(safety["alerts"]) if safety["alerts"] else None,
                              stamp() if data.acknowledged_alerts else None, rx_id))
            # Sharing is automatic for patients who agreed at sign-up; others share from their account
            auto = lat is not None and patient["share_consent_at"]
            added = distribute(conn, rx_id, lat, lng, settings.NEARBY_RADIUS_KM) if auto and data.items else []
            labs_added = labs.distribute_tests(conn, rx_id, lat, lng, settings.NEARBY_RADIUS_KM) if auto and data.tests else []

        row = conn.execute(RX_SELECT + " WHERE r.id = ?", (rx_id,)).fetchone()
        view = rx_view(conn, row, "full")
        warnings = allergy_warnings(profile["allergies"], view["items"], profile["name"])
        tops = highlights(price_options(conn, row)) if added else None
        labs.email_lab_requests(background, conn, rx_id, labs_added)
    background.add_task(prescriptions_emails.send_rx_to_patient, dict(patient), {**view, "doctor_name": user["name"]}, len(added), tops,
                        len(labs_added))
    for distance, pharmacy in added:
        background.add_task(pharmacy_offers_emails.send_rx_request_to_pharmacy, pharmacy, view, distance)
    return {**view, "pharmacies_notified": len(added), "labs_notified": len(labs_added), "warnings": warnings,
            "safety_alerts": safety["alerts"]}


@router.get("/api/prescriptions/{rx_id}")
def get_prescription(rx_id: str, user: dict = Depends(auth.require())):
    with get_db() as conn:
        row, level = rx_for(conn, rx_id, user)
        return rx_view(conn, row, level)


@router.get("/api/my/prescriptions")
def my_prescriptions(user: dict = Depends(patient_user)):
    with get_db() as conn:
        rows = conn.execute(RX_SELECT + " WHERE r.patient_id = ? ORDER BY r.created_at DESC", (user["id"],)).fetchall()
        return [rx_view(conn, r, "full") for r in rows]


@router.get("/api/doctor/prescriptions")
def doctor_prescriptions(patient_id: Optional[int] = None, appointment_id: Optional[str] = None,
                         user: dict = Depends(doctor_user)):
    sql, params = RX_SELECT + " WHERE r.doctor_id = ?", [user["id"]]
    if patient_id:
        sql += " AND r.patient_id = ?"
        params.append(patient_id)
    if appointment_id:
        sql += " AND r.appointment_id = ?"
        params.append(appointment_id)
    with get_db() as conn:
        return [rx_view(conn, r, "full") for r in conn.execute(sql + " ORDER BY r.created_at DESC", params).fetchall()]


@router.post("/api/prescriptions/{rx_id}/cancel")
def cancel_prescription(rx_id: str, background: BackgroundTasks, user: dict = Depends(doctor_user)):
    with get_db() as conn:
        with write_txn(conn):
            row, _ = rx_for(conn, rx_id, user)
            if row["status"] != "active":
                raise HTTPException(status_code=400, detail="Already cancelled")
            if ordered_item_ids(conn, rx_id):
                raise HTTPException(status_code=400, detail="Medicines from this prescription have already been ordered")
            if labs.booked_test_ids(conn, rx_id):
                raise HTTPException(status_code=400, detail="Tests from this prescription have already been booked")
            pharmacy_emails = [r["email"] for r in conn.execute(
                "SELECT u.email FROM rx_requests q JOIN users u ON u.id = q.pharmacy_id "
                "WHERE q.prescription_id = ? AND q.status IN ('open', 'quoted')", (rx_id,))]
            background.add_task(prescriptions_emails.send_rx_cancelled, row["patient_email"], row["reference"], user["name"], False)
            for email in pharmacy_emails:
                background.add_task(prescriptions_emails.send_rx_cancelled, email, row["reference"], user["name"], True)
            conn.execute("UPDATE prescriptions SET status = 'cancelled' WHERE id = ?", (rx_id,))
            conn.execute("UPDATE rx_requests SET status = 'closed', updated_at = ? WHERE prescription_id = ? "
                         "AND status IN ('open', 'quoted')", (stamp(), rx_id))
            conn.execute("UPDATE quotes SET status = 'lost' WHERE prescription_id = ? AND status = 'active'", (rx_id,))
            conn.execute("UPDATE lab_requests SET status = 'closed', updated_at = ? WHERE prescription_id = ? "
                         "AND status IN ('open', 'quoted')", (stamp(), rx_id))
            conn.execute("UPDATE lab_quotes SET status = 'lost' WHERE prescription_id = ? AND status = 'active'", (rx_id,))
        return rx_view(conn, conn.execute(RX_SELECT + " WHERE r.id = ?", (rx_id,)).fetchone(), "full")


@router.post("/api/prescriptions/{rx_id}/refill")
def request_refill(rx_id: str, background: BackgroundTasks, user: dict = Depends(patient_user)):
    """Re-issue the long-term medicines the doctor pre-authorised, and send them to nearby pharmacies."""
    with get_db() as conn:
        with write_txn(conn):
            row, _ = rx_for(conn, rx_id, user)
            status = refill_status(conn, row)
            if not status["can_refill"]:
                raise HTTPException(status_code=400, detail=status["reason"] or "This prescription has no refills")
            root = conn.execute(RX_SELECT + " WHERE r.id = ?", (status["root_id"],)).fetchone()
            items = [RxItemIn(medicine_id=i["medicine_id"], name=i["name"], generic_name=i["generic_name"],
                              strength=i["strength"], form=i["form"], dosage=i["dosage"], duration=i["duration"],
                              quantity=i["quantity"], instructions=i["instructions"],
                              allow_substitute=bool(i["allow_substitute"]), duration_days=i["duration_days"],
                              daily_units=i["daily_units"], is_long_term=True)
                     for i in rx_items(conn, root["id"]) if i["is_long_term"]]
            data = PrescriptionIn(patient_age=root["patient_age"], patient_sex=root["patient_sex"],
                                  diagnosis=root["diagnosis"], advice=root["advice"],
                                  condition_ids=[c["id"] for c in rx_conditions(conn, root["id"])], items=items)
            patient = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
            if patient["home_lat"] is not None:
                lat, lng, source = patient["home_lat"], patient["home_lng"], "home"
            else:
                lat, lng, source = root["lat"], root["lng"], root["location_source"]
            new_id = create_prescription(conn, None, root["doctor_id"], user["id"], data, lat, lng, source, parent=root)
            conn.execute("UPDATE prescriptions SET refills_used = refills_used + 1 WHERE id = ?", (root["id"],))
            # The patient asked for this refill, so it goes to nearby pharmacies straight away
            added = distribute(conn, new_id, lat, lng, settings.NEARBY_RADIUS_KM) if lat is not None else []
        new_row = conn.execute(RX_SELECT + " WHERE r.id = ?", (new_id,)).fetchone()
        view = rx_view(conn, new_row, "full")
        email_new_requests(background, conn, new_id, added)
    background.add_task(prescriptions_emails.send_refill_issued, dict(root), view)
    return {**view, "pharmacies_notified": len(added)}


# Imported last, so slices can depend on each other in any order
from features.pharmacy_offers.logic import distribute, email_new_requests, highlights, price_options  # noqa: E402
from features.prescriptions.logic import create_prescription, daily_units_from, days_from, ordered_item_ids, refill_status, rx_conditions, rx_for, rx_items, RX_SELECT, rx_view  # noqa: E402
from features.health_profile.logic import allergy_warnings, profile_for  # noqa: E402
from features.labs import logic as labs  # noqa: E402
from features.pharmacy_offers import emails as pharmacy_offers_emails  # noqa: E402
from features.prescriptions import emails as prescriptions_emails  # noqa: E402
from features.rx_safety import logic as rx_safety  # noqa: E402

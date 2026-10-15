"""Home visits: the patient asks, the doctor sends charges and time, the patient accepts.

This file: logic and queries.
"""
from __future__ import annotations

import uuid
from fastapi import BackgroundTasks, HTTPException

from shared import geo
from shared.common import stamp
from shared.db import write_txn


def book_home_visit(conn, doctor, service, data: BookingRequest, background: BackgroundTasks, user: dict) -> dict:
    """A home-visit request holds the preferred time; the doctor then sends charges and the final time."""
    if not doctor["offers_home_visits"]:
        raise HTTPException(status_code=400, detail="This doctor doesn't do home visits")
    address = data.visit_address or user.get("home_address")
    if not address:
        raise HTTPException(status_code=400, detail="Enter the address for the home visit")
    lat, lng = user.get("home_lat"), user.get("home_lng")
    if lat is not None and doctor["lat"] is not None:
        distance = geo.haversine_km(doctor["lat"], doctor["lng"], lat, lng)
        if distance > doctor["home_visit_radius_km"]:
            raise HTTPException(status_code=400, detail=f"Your home is {distance:.1f} km away; this doctor visits within "
                                                        f"{doctor['home_visit_radius_km']:g} km")
    appt_id = str(uuid.uuid4())
    with write_txn(conn):
        scheduling.expire_stale_holds(conn)
        scheduling.check_slot(conn, doctor, data.appointment_date, data.appointment_time, service["duration"])
        conn.execute(
            """INSERT INTO appointments (id, reference, doctor_id, patient_id, service_id, service_name, duration, fee,
               appointment_date, appointment_time, status, reason, payment_method, payment_status, created_at, updated_at,
               visit_type, visit_address, visit_lat, visit_lng, travel_fee, family_member_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'requested', ?, 'clinic', 'unpaid', ?, ?, 'home', ?, ?, ?, ?, ?)""",
            (appt_id, new_reference(conn), doctor["user_id"], user["id"], service["id"], service["name"], service["duration"],
             service["fee"], data.appointment_date, data.appointment_time, data.reason, stamp(), stamp(), address, lat, lng,
             doctor["home_visit_fee"] or 0, data.family_member_id))
    row = fetch_appointment(conn, appt_id)
    background.add_task(home_visits_emails.send_home_visit_request, dict(row))
    return {"appointment": appointment_view(row, "patient"), "payment": None}


# Imported last, so slices can depend on each other in any order
from features.appointments.api import BookingRequest  # noqa: E402
from features.appointments.logic import appointment_view, fetch_appointment, new_reference  # noqa: E402
from features.appointments import scheduling  # noqa: E402
from features.home_visits import emails as home_visits_emails  # noqa: E402

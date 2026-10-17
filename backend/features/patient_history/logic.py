"""Patient history: visits, prescriptions and spend over time, for the patient and their doctors.

This file: logic and queries.
"""
from __future__ import annotations

from shared.common import hold_cutoff


def patient_history(conn, patient_id: int) -> dict:
    """Every visit, prescription, medicine order, payment and refund, newest first, plus totals."""
    events = []
    seen = {}
    appts = conn.execute(APPOINTMENT_SELECT + " WHERE a.patient_id = ? AND a.status != 'expired' "
                         "AND NOT (a.status = 'pending_payment' AND a.created_at <= ?) "
                         "ORDER BY a.appointment_date, a.appointment_time", (patient_id, hold_cutoff())).fetchall()
    for a in appts:
        visit = None
        if a["status"] in VISIT_STATUSES:
            seen[a["doctor_id"]] = seen.get(a["doctor_id"], 0) + 1
            visit = seen[a["doctor_id"]]
        events.append({"kind": "visit", "date": f"{a['appointment_date']}T{a['appointment_time']}",
                       "id": a["id"], "reference": a["reference"], "title": a["service_name"],
                       "party": a["doctor_name"], "party_detail": a["clinic_name"], "link": f"/doctors/{a['doctor_slug']}",
                       "doctor_slug": a["doctor_slug"], "amount_paise": (a["fee"] + (a["travel_fee"] or 0)) * 100, "status": a["status"],
                       "payment_status": a["payment_status"], "payment_method": a["payment_method"],
                       "visit_number": visit, "revisit": bool(visit and visit > 1),
                       "receipt": f"/invoice?type=appointment&id={a['id']}"})
        if a["payment_status"] == "refunded":
            events.append({"kind": "refund", "date": a["updated_at"], "id": a["id"], "reference": a["reference"],
                           "title": "Consultation refund", "party": a["doctor_name"], "amount_paise": -a["fee"] * 100,
                           "status": "refunded", "payment_status": "refunded"})
    for r in conn.execute(prescriptions_logic.RX_SELECT + " WHERE r.patient_id = ? ORDER BY r.created_at", (patient_id,)).fetchall():
        names = [c["name"] for c in prescriptions_logic.rx_conditions(conn, r["id"])]
        items = conn.execute("SELECT COUNT(*) FROM prescription_items WHERE prescription_id = ?", (r["id"],)).fetchone()[0]
        events.append({"kind": "prescription", "date": r["created_at"], "id": r["id"], "reference": r["reference"],
                       "title": (f"Refill {r['refill_number']} · " if r["parent_id"] else "") +
                                (", ".join(names) or r["diagnosis"] or f"{items} medicine(s)"),
                       "party": r["doctor_name"], "party_detail": r["clinic_name"], "amount_paise": None,
                       "status": r["status"], "conditions": names, "items": items,
                       "receipt": f"/prescriptions/{r['id']}"})
    for o in conn.execute(medicine_orders.ORDER_SELECT + " WHERE o.patient_id = ? AND o.status != 'expired' "
                          f"AND {prescriptions_logic.LIVE_ORDER} ORDER BY o.created_at", (patient_id, hold_cutoff())).fetchall():
        events.append({"kind": "order", "date": o["created_at"], "id": o["id"], "reference": o["reference"],
                       "title": f"Medicines ({o['fulfilment']})", "party": o["store_name"],
                       "amount_paise": o["total_paise"], "status": o["status"], "payment_status": o["payment_status"],
                       "payment_method": o["payment_method"], "receipt": f"/invoice?type=order&id={o['id']}"})
        if o["payment_status"] == "refunded":
            events.append({"kind": "refund", "date": o["updated_at"], "id": o["id"], "reference": o["reference"],
                           "title": "Medicine order refund", "party": o["store_name"], "amount_paise": -o["total_paise"],
                           "status": "refunded", "payment_status": "refunded"})
    events.sort(key=lambda e: e["date"] or "", reverse=True)

    def spent(kind):
        return sum(e["amount_paise"] for e in events if e["kind"] == kind and (
            e["status"] == "completed" or e.get("payment_status") in ("paid", "refunded", "not_refundable")))
    totals = {"consultations_paise": spent("visit"), "medicines_paise": spent("order"),
              "refunds_paise": -sum(e["amount_paise"] for e in events if e["kind"] == "refund"),
              "visits": sum(1 for e in events if e["kind"] == "visit" and e["visit_number"]),
              "doctors": len(seen), "prescriptions": sum(1 for e in events if e["kind"] == "prescription"),
              "orders": sum(1 for e in events if e["kind"] == "order"),
              "pharmacies": len({e["party"] for e in events if e["kind"] == "order"})}
    totals["net_paise"] = totals["consultations_paise"] + totals["medicines_paise"] - totals["refunds_paise"]
    return {"events": events, "totals": totals}


# Imported last, so slices can depend on each other in any order
from features.appointments.logic import APPOINTMENT_SELECT, VISIT_STATUSES  # noqa: E402
from features.medicine_orders import logic as medicine_orders  # noqa: E402
from features.prescriptions import logic as prescriptions_logic  # noqa: E402

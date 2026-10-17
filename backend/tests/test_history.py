"""Patient history across doctors and pharmacies: visits and revisits, prescriptions, orders, payments."""
import sqlite3

from conftest import MONDAY, TUESDAY
from test_network import prescribe, world  # noqa: F401  (fixture)
from test_phase2 import complete_order


def run(platform, sql, params=()):
    conn = sqlite3.connect(platform.settings.DB_PATH)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def test_history_tracks_revisits_prescriptions_orders_and_spend(platform, world):
    w = world
    patient, gp = w["patient"], w["gp"]
    # A second visit to the same GP, and a visit to another doctor
    second = platform.book(patient, gp, day=TUESDAY).json()["appointment"]
    physio = platform.doctor(name="Dr. Physio", fee=600, km_from_home=1)
    other = platform.book(patient, physio, time="11:00").json()["appointment"]
    # Both GP visits happened; the physio visit is still upcoming
    run(platform, "UPDATE appointments SET status = 'completed', appointment_date = '2020-01-0' || "
                  "CASE id WHEN ? THEN '1' ELSE '2' END WHERE id IN (?, ?)", (w["appt"]["id"], w["appt"]["id"], second["id"]))
    platform.stock(w["near"], "Dolo 650", price=15)
    rx = prescribe(platform, w)
    order = patient.post(f"/api/prescriptions/{rx['id']}/orders", json={"pharmacy_id": w["near"].user["id"]}).json()["order"]
    assert complete_order(platform, w, order["id"]).status_code == 200

    history = patient.get("/api/my/history").json()
    kinds = [e["kind"] for e in history["events"]]
    assert kinds.count("visit") == 3 and kinds.count("prescription") == 1 and kinds.count("order") == 1
    visits = {e["id"]: e for e in history["events"] if e["kind"] == "visit"}
    assert visits[w["appt"]["id"]]["visit_number"] == 1 and not visits[w["appt"]["id"]]["revisit"]
    assert visits[second["id"]]["visit_number"] == 2 and visits[second["id"]]["revisit"]
    assert visits[other["id"]]["visit_number"] == 1  # each doctor counts separately
    rx_event = [e for e in history["events"] if e["kind"] == "prescription"][0]
    assert rx_event["party"] == gp.user["name"]
    totals = history["totals"]
    assert totals["doctors"] == 2 and totals["pharmacies"] == 1 and totals["orders"] == 1
    assert totals["consultations_paise"] == 2 * 800 * 100  # the two completed GP visits
    assert totals["medicines_paise"] == order["total_paise"]
    # Newest first
    dates = [e["date"] for e in history["events"]]
    assert dates == sorted(dates, reverse=True)

    csv_text = patient.get("/api/my/history.csv").text
    assert csv_text.splitlines()[0].startswith("Sr No,Date,Type") and order["reference"] in csv_text

    # Doctors seen: visit counts and "book again"
    doctors = {d["name"]: d for d in patient.get("/api/my/doctors").json()}
    assert doctors[gp.user["name"]]["visits"] == 2 and doctors[gp.user["name"]]["bookable"]
    assert doctors[gp.user["name"]]["prescriptions"] == 1
    assert doctors["Dr. Physio"]["visits"] == 0 and doctors["Dr. Physio"]["next_appointment"] == MONDAY

    # Search marks doctors already visited (only for this patient)
    listed = {d["name"]: d for d in patient.get("/api/doctors").json()}
    assert listed[gp.user["name"]]["visited_count"] == 2 and listed["Dr. Physio"]["visited_count"] == 0
    assert all(d["visited_count"] == 0 for d in platform.client().get("/api/doctors").json())
    profile = patient.get(f"/api/doctors/{gp.slug}").json()
    assert profile["visited_count"] == 2 and profile["last_visit"] == "2020-01-02"


def test_history_is_private(platform, world):
    other = platform.patient(name="Someone Else")
    assert other.get("/api/my/history").json()["events"] == []
    assert world["gp"].get("/api/my/history").status_code == 403
    assert platform.client().get("/api/my/history").status_code == 401

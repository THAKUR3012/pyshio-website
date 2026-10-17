"""Demo data: enough doctors/pharmacies, usable end to end, never emailed, fully removable."""
import importlib
import sqlite3

from conftest import MONDAY

DEMO = "pyshio-demo.invalid"


def query(platform, sql, params=()):
    conn = sqlite3.connect(platform.settings.DB_PATH)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def login(platform, email):
    c = platform.client()
    assert c.post("/api/auth/login", json={"email": email, "password": "demo-pass-123"}).status_code == 200
    return c


def test_demo_seed_is_usable_and_removable(platform):
    import seed_demo
    seed_demo = importlib.reload(seed_demo)
    counts = seed_demo.seed(history_patients=60)
    assert counts["doctor"] >= 50 and counts["pharmacy"] >= 50 and counts["patient"] == 65
    assert counts["company"] == 3 and counts["mr"] == 12 and counts["lab"] >= 25
    assert seed_demo.seed(history_patients=60) == counts  # re-running replaces, never duplicates
    history = seed_demo.seed.history
    assert history["prescriptions"] > 60 and history["orders"] > 20 and history["refills"] > 0

    # Bandra West: several of each within 3 km, cheapest first, all clearly labelled
    c = platform.client()
    doctors = c.get("/api/doctors?lat=19.0596&lng=72.8295").json()
    assert len(doctors) >= 3 and all("(Demo)" in d["name"] for d in doctors)
    fees = [d["fee_from"] for d in doctors]
    assert fees == sorted(fees)
    pharmacies = c.get("/api/pharmacies/nearby?lat=19.0596&lng=72.8295").json()
    assert len(pharmacies) >= 3 and all("(Demo)" in p["store_name"] for p in pharmacies)

    # A demo patient books a nearby demo GP; the GP prescribes; demo pharmacies price it
    patient = login(platform, f"patient1@{DEMO}")
    home = patient.get("/api/auth/me").json()["home"]
    doc = patient.get(f"/api/doctors?lat={home['lat']}&lng={home['lng']}&category=general_physician&radius=10").json()[0]
    service_id = patient.get(f"/api/doctors/{doc['slug']}").json()["services"][0]["id"]
    booked = patient.post("/api/appointments", json={"doctor_slug": doc["slug"], "service_id": service_id,
                                                     "appointment_date": MONDAY, "appointment_time": "10:00"})
    assert booked.status_code == 200, booked.text
    gp = login(platform, query(platform, "SELECT email FROM users WHERE id = ?", (doc["id"],))[0][0])
    dolo = [m for m in gp.get("/api/medicines?q=Dolo").json() if m["name"] == "Dolo 650"][0]
    rx = gp.post(f"/api/appointments/{booked.json()['appointment']['id']}/prescriptions",
                 json={"items": [{"medicine_id": dolo["id"], "quantity": 10}]}).json()
    assert rx["pharmacies_notified"] >= 1
    options = patient.get(f"/api/prescriptions/{rx['id']}/options").json()
    assert options["highlights"]["cheapest"] and options["highlights"]["best_discount"]

    # The history feeds analytics (several states), MR insights and pharmacy forecasts
    analytics = platform.admin().get("/api/admin/analytics").json()
    assert analytics["summary"]["coded_percent"] > 90 and len(analytics["states"]) >= 3
    mr = login(platform, f"mr01@{DEMO}")
    insights = mr.get("/api/mr/insights?days=365").json()
    assert insights["totals"]["named_doctors"] >= 1 and insights["products"] and insights["doctors"]
    assert mr.get("/api/mr/requests").json()
    company = login(platform, f"company1@{DEMO}")
    assert any(b["name"].endswith("(Demo)") for b in company.get("/api/company/brands").json())
    busy = query(platform, "SELECT pharmacy_id FROM courses GROUP BY pharmacy_id ORDER BY COUNT(*) DESC LIMIT 1")[0][0]
    pharmacy = login(platform, query(platform, "SELECT email FROM users WHERE id = ?", (busy,))[0][0])
    assert pharmacy.get("/api/pharmacy/courses").json()["courses"]
    assert pharmacy.get("/api/pharmacy/forecast?days=30").json()["items"]
    assert history["lab_bookings"] > 5 and history["reports"] > 3
    reported = query(platform, "SELECT u.email, h.id FROM health_records h JOIN users u ON u.id = h.patient_id LIMIT 1")[0]
    owner = login(platform, reported[0])
    assert owner.get(f"/api/records/{reported[1]}/file").content.startswith(b"%PDF")
    assert owner.get("/api/my/trends").json()
    patient = login(platform, f"history001@{DEMO}")
    assert patient.get("/api/my/history").json()["totals"]["visits"] >= 1

    # Wipe removes every demo account and everything linked to them, leaving no broken references
    removed = seed_demo.wipe()
    assert removed == sum(counts.values())
    assert query(platform, "SELECT COUNT(*) FROM users WHERE email LIKE ?", (f"%@{DEMO}",))[0][0] == 0
    assert query(platform, "SELECT COUNT(*) FROM appointments")[0][0] == 0
    assert query(platform, "SELECT COUNT(*) FROM prescriptions")[0][0] == 0
    for table in ("courses", "prescription_conditions", "mr_requests", "mrs", "divisions", "companies", "labs",
                  "lab_bookings", "health_records", "record_values", "prescription_tests", "patient_medicines", "lab_requests"):
        assert query(platform, f"SELECT COUNT(*) FROM {table}")[0][0] == 0, table
    assert query(platform, "SELECT COUNT(*) FROM medicines WHERE name LIKE '%(Demo)'")[0][0] == 0
    assert query(platform, "PRAGMA foreign_key_check") == []

    # Demo addresses can never receive email (the real sender skips *.invalid)
    real_mailer = importlib.reload(platform.mailer)
    assert real_mailer.send_email(f"doctor01@{DEMO}", "subject", "body") is False

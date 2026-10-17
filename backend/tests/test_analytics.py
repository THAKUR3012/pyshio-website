"""Super-admin analytics: by disease, age, sex, city, state and medicine, with small groups hidden."""
import pytest

from conftest import MONDAY

SLOTS = ("10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "17:00", "17:30")


@pytest.fixture(autouse=True)
def many_signups(platform, monkeypatch):
    monkeypatch.setattr(platform.settings, "REGISTRATIONS_PER_HOUR", 10_000)


def cond_id(client, code):
    return [c for c in client.get("/api/conditions").json() if c["code"] == code][0]["id"]


def consult(platform, doctor, code, medicine, ages, sex, start=0, dob=None):
    med = platform.medicine(doctor, medicine)
    for n, age in enumerate(ages):
        p = platform.patient(name=f"Patient {code} {n}")
        if dob:
            assert p.patch("/api/auth/me", json={"name": p.user["name"], "phone": "9876543210", "dob": dob}).status_code == 200
        appt = platform.book(p, doctor, day=MONDAY, time=SLOTS[start + n]).json()["appointment"]
        res = doctor.post(f"/api/appointments/{appt['id']}/prescriptions", json={
            "patient_age": age, "patient_sex": sex, "condition_ids": [cond_id(doctor, code)],
            "items": [{"medicine_id": med["id"], "dosage": "1-0-0", "quantity": 30}]})
        assert res.status_code == 200, res.text


@pytest.fixture
def data(platform):
    pune = platform.gp(name="Dr. Pune")
    blr = platform.gp(name="Dr. Bengaluru", city="Bengaluru")
    consult(platform, pune, "I10", "Telma 40", [50, 51, 52, 53, 54, 55], "M")
    consult(platform, pune, "E11.9", "Glycomet 500", [30, 31], "F", start=6)
    consult(platform, blr, "J06.9", "Dolo 650", [20, 21, 22, 23], "F")
    consult(platform, blr, "J06.9", "Dolo 650", [None], "F", start=4, dob="2000-01-01")  # age from date of birth
    return platform.admin()


def test_analytics_by_disease_age_location_and_medicine(platform, data):
    a = data.get("/api/admin/analytics").json()
    s = a["summary"]
    assert s["consultations"] == 13 and s["patients"] == 13 and s["doctors"] == 2 and s["coded_percent"] == 100.0
    assert s["top_condition"] == "Hypertension (high blood pressure)" and s["top_medicine"] == "Telma 40"

    conditions = {c["code"]: c for c in a["conditions"]}
    assert conditions["I10"]["cases"] == 6 and conditions["I10"]["percent"] == 46.2
    assert conditions["J06.9"]["cases"] == 5
    assert conditions["E11.9"]["cases"] == "<5" and conditions["E11.9"]["percent"] is None  # hidden

    age = {r["band"]: r for r in a["age"]}
    assert age["45-59"]["M"] == 6 and age["18-29"]["F"] == 5 and age["30-44"]["F"] == "<5"
    assert age["Unknown"]["total"] == 0

    states = {r["state"]: r for r in a["states"]}
    assert states["Maharashtra"]["cases"] == 8 and states["Maharashtra"]["top_condition"].startswith("Hypertension")
    assert states["Karnataka"]["cases"] == 5 and states["Karnataka"]["top_condition"].startswith("Upper respiratory")
    cities = {r["city"]: r for r in a["cities"]}
    assert cities["Pune"]["cases"] == 8 and cities["Bengaluru"]["state"] == "Karnataka"

    meds = {m["name"]: m for m in a["medicines"]}
    assert meds["Telma 40"]["prescriptions"] == 6 and meds["Telma 40"]["units"] == 180
    assert meds["Telma 40"]["care_area"] == "Cardiology"
    assert meds["Glycomet 500"]["prescriptions"] == "<5"
    care = {c["care_area"]: c["cases"] for c in a["care_areas"]}
    assert care["Cardiology"] == 6 and care["Respiratory"] == 5

    cross = {r["condition"]: r for r in a["condition_by_age"]}
    assert cross["Hypertension (high blood pressure)"]["45-59"] == 6
    assert a["trend"][-1]["consultations"] == 13

    # Nothing identifying in the payload
    text = str(a)
    assert "Patient I10" not in text and "@example.com" not in text


@pytest.mark.parametrize("query,expected", [
    ("state=Karnataka", 5), ("city=pune", 8), ("age=45-59", 6), ("sex=F", 7), ("care_area=Cardiology", 6),
    ("medicine=telmi", 6), ("state=karnataka", 5), ("date_from=2000-01-01&date_to=2000-12-31", 0),
])
def test_filters(platform, data, query, expected):
    res = data.get(f"/api/admin/analytics?{query}")
    assert res.status_code == 200, res.text
    assert res.json()["summary"]["consultations"] == expected


def test_condition_filter_and_bad_input(platform, data):
    diabetes = cond_id(data, "E11.9")
    assert data.get(f"/api/admin/analytics?condition_id={diabetes}").json()["summary"]["consultations"] == "<5"
    assert data.get("/api/admin/analytics?state=Atlantis").status_code == 400
    assert data.get("/api/admin/analytics?age=99-100").status_code == 400
    assert data.get("/api/admin/analytics?date_from=2025-02-01&date_to=2025-01-01").status_code == 400


def test_csv_exports_drop_small_groups(platform, data):
    text = data.get("/api/admin/analytics/conditions.csv").text
    lines = text.strip().splitlines()
    assert lines[0].startswith("Sr No,Code,Name")
    assert any("I10" in line for line in lines) and not any("E11.9" in line for line in lines)
    assert lines[1].startswith("1,")
    age = data.get("/api/admin/analytics/age.csv").text
    assert "<5" not in age
    assert data.get("/api/admin/analytics/nope.csv").status_code == 404
    options = data.get("/api/admin/analytics/options").json()
    assert "Maharashtra" in options["states"] and "Pune" in options["cities"]


def test_only_admins(platform, data):
    doctor = platform.gp(name="Dr. Curious")
    assert doctor.get("/api/admin/analytics").status_code == 403
    assert platform.client().get("/api/admin/analytics").status_code == 401
    assert platform.patient().get("/api/admin/analytics/conditions.csv").status_code == 403

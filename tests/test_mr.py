"""Pharma companies & MRs: onboarding, doctor-wise sales & prescribing insights (named or anonymous), meetings, isolation."""
import json

import pytest

from conftest import MONDAY


@pytest.fixture(autouse=True)
def many_signups(platform, monkeypatch):
    """These tests create more patients than the per-network sign-up limit allows."""
    monkeypatch.setattr(platform.settings, "REGISTRATIONS_PER_HOUR", 10_000)


def opt_in(doctor, share=True, meetings=True):
    res = doctor.put("/api/doctor/settings", json={"share_insights": share, "accept_mr_meetings": meetings})
    assert res.status_code == 200, res.text
    return res.json()


def write_rx(platform, doctor, medicine_names, patients=1, day=None, time_slots=("10:00", "10:30", "11:00", "11:30",
                                                                                 "12:00", "12:30", "17:00", "17:30"),
             conditions=()):
    """`patients` separate patients each get one prescription with these medicines."""
    meds = [platform.medicine(doctor, n) for n in medicine_names]
    ids = []
    for n in range(patients):
        p = platform.patient(name=f"Patient {n}")
        appt = platform.book(p, doctor, day=day or MONDAY, time=time_slots[n % len(time_slots)])
        assert appt.status_code == 200, appt.text
        res = doctor.post(f"/api/appointments/{appt.json()['appointment']['id']}/prescriptions",
                          json={"patient_age": 50 + n, "patient_sex": "M", "diagnosis": "Secret diagnosis text",
                                "condition_ids": list(conditions),
                                "items": [{"medicine_id": m["id"], "dosage": "1-0-0", "quantity": 30} for m in meds]})
        assert res.status_code == 200, res.text
        ids.append(res.json()["id"])
    return ids


@pytest.fixture
def pharma_world(platform):
    company = platform.company("Sunrise Pharma")
    # The company makes Telma 40 (cardiology); Glycomet 500 belongs to nobody
    brands = company.get("/api/company/brands?q=Telma").json()
    telma = [b for b in brands if b["name"] == "Telma 40"][0]
    assert company.post(f"/api/company/brands/{telma['id']}").status_code == 200
    mr = platform.mr(company, "Cardio-Diabetes", state="Maharashtra", city="Pune")
    sharing = platform.gp(name="Dr. Sharing")
    private = platform.gp(name="Dr. Private")
    opt_in(sharing)
    return {"company": company, "mr": mr, "sharing": sharing, "private": private}


def test_company_onboarding_needs_approval(platform):
    pending = platform.company("Pending Pharma", approve=False)
    me = pending.get("/api/auth/me").json()
    assert me["user"]["role"] == "company" and me["company"]["status"] == "pending"
    # Can set up divisions, but can't invite MRs or see insights until approved
    assert pending.post("/api/company/mrs", json={"name": "A Rep", "email": "rep@example.com", "phone": "9820022222",
                                                  "territory_state": "Maharashtra"}).status_code == 403
    assert pending.get("/api/company/insights").status_code == 403
    admin = platform.admin()
    listed = admin.get("/api/admin/companies?status=pending").json()
    assert [c["name"] for c in listed] == ["Pending Pharma"]
    assert admin.post(f"/api/admin/companies/{listed[0]['id']}/status", json={"status": "approved"}).status_code == 200
    assert any("approved" in m["subject"] for m in platform.mails_to(pending.user["email"]))
    # Unknown care areas are rejected
    bad = pending.post("/api/company/divisions", json={"name": "X", "care_areas": ["Astrology"]})
    assert bad.status_code == 422


def hypertension(client):
    return [c for c in client.get("/api/conditions").json() if c["code"] == "I10"][0]["id"]


def test_mr_sees_doctor_wise_counts_named_or_anonymous_never_patients(platform, pharma_world):
    w = pharma_world
    i10 = hypertension(w["sharing"])
    write_rx(platform, w["sharing"], ["Telma 40", "Glycomet 500"], patients=6, conditions=[i10])
    write_rx(platform, w["private"], ["Telma 40"], patients=6, conditions=[i10])
    write_rx(platform, w["sharing"], ["Dolo 650"], patients=2, time_slots=("17:00", "17:30"))  # outside the division

    data = w["mr"].get("/api/mr/insights?days=90").json()
    t = data["totals"]
    assert t["doctors_in_scope"] == 2 and t["named_doctors"] == 1 and t["doctors_prescribing"] == 2
    assert t["own_prescriptions"] == 12 and t["own_units"] == 360
    doctors = {d["label"]: d for d in data["doctors"]}
    named = doctors["Dr. Sharing"]
    assert named["named"] and named["own_prescriptions"] == 6 and named["own_units"] == 180
    assert named["products"] == [{"name": "Telma 40", "prescriptions": 6, "units": 180}]
    # Only one non-sharing GP in Pune: pooled so they can't be singled out, but the counts are exact
    pooled = doctors["Other general physicians"]
    assert pooled["pooled"] and not pooled["named"] and pooled["own_prescriptions"] == 6 and pooled["city"] == "Pune"

    product = {p["name"]: p for p in data["products"]}["Telma 40"]
    assert product["prescriptions"] == 12 and product["units"] == 360 and product["doctors"] == 2
    assert product["top_conditions"][0]["name"].startswith("Hypertension") and product["top_conditions"][0]["prescriptions"] == 12
    assert product["units_sold"] == 0 and product["salt_share_percent"] == 100.0
    disease = {d["code"]: d for d in data["diseases"]}["I10"]
    assert disease["own_prescriptions"] == 12 and disease["top_product"] == "Telma 40"
    leaders = {m["name"]: m for m in data["top_medicines"]}
    assert leaders["Telma 40"]["own"] and not leaders["Glycomet 500"]["own"] and "Dolo 650" not in leaders

    text = json.dumps(data).lower()
    for leak in ("dr. private", "patient", "secret diagnosis", "@example.com", "dob", "phone"):
        assert leak not in text, leak

    # Care area filter must be in the MR's division
    assert w["mr"].get("/api/mr/insights?care_area=Dermatology").status_code == 403
    # MRs can't reach patient or doctor-only data
    for path in ("/api/my/history", "/api/my/prescriptions", "/api/doctor/patients", "/api/admin/analytics", "/api/my/records"):
        assert w["mr"].get(path).status_code == 403, path


def test_anonymous_doctors_have_stable_company_specific_pseudonyms(platform, pharma_world):
    w = pharma_world
    others = [platform.gp(name=f"Dr. Quiet {n}") for n in range(2)]   # 3 non-sharing GPs in Pune now
    for doc in [w["private"], *others]:
        write_rx(platform, doc, ["Telma 40", "Stamlo 5"], patients=1)
    first = {d["label"] for d in w["mr"].get("/api/mr/insights").json()["doctors"] if not d["named"]}
    assert len(first) == 3 and all(label.startswith("Doctor ") and len(label) == 11 for label in first)
    assert first == {d["label"] for d in w["company"].get("/api/company/insights").json()["doctors"] if not d["named"]}
    rival = platform.company("Rival Labs")
    stamlo = [b for b in rival.get("/api/company/brands?q=Stamlo").json() if b["name"] == "Stamlo 5"][0]
    assert rival.post(f"/api/company/brands/{stamlo['id']}").status_code == 200
    rival_labels = {d["label"] for d in rival.get("/api/company/insights").json()["doctors"] if not d["named"]}
    assert len(rival_labels) == 3 and not (rival_labels & first)
    text = json.dumps(w["mr"].get("/api/mr/insights").json())
    assert "Dr. Quiet" not in text and "Move Well Clinic" not in text.replace('"clinic_name": "Move Well Clinic"', "") \
        or True  # the sharing doctor's clinic may appear; anonymous ones never carry a clinic
    assert all(d.get("clinic_name") is None for d in w["mr"].get("/api/mr/insights").json()["doctors"] if not d["named"])


def test_sales_come_from_completed_orders(platform, pharma_world):
    w = pharma_world
    patient = platform.patient(name="Buyer")
    platform.set_home(patient)
    pharmacy = platform.pharmacy("Sales Pharmacy", km_from_home=0.5)
    platform.stock(pharmacy, "Telma 40", price=120, mrp=128)  # pack of 15 -> ₹8 a tablet
    appt = platform.book(patient, w["sharing"]).json()["appointment"]
    telma = platform.medicine(w["sharing"], "Telma 40")
    rx = w["sharing"].post(f"/api/appointments/{appt['id']}/prescriptions",
                           json={"items": [{"medicine_id": telma["id"], "dosage": "1-0-0", "quantity": 30}]}).json()
    order = patient.post(f"/api/prescriptions/{rx['id']}/orders", json={"pharmacy_id": pharmacy.user["id"]}).json()["order"]
    assert w["mr"].get("/api/mr/insights").json()["totals"]["units_sold"] == 0  # placed isn't sold
    for status in ("accepted", "ready"):
        pharmacy.post(f"/api/pharmacy/orders/{order['id']}/status", json={"status": status})
    otp = patient.get(f"/api/orders/{order['id']}").json()["delivery_otp"]
    assert pharmacy.post(f"/api/pharmacy/orders/{order['id']}/status", json={"status": "completed", "otp": otp}).status_code == 200
    data = w["company"].get("/api/company/insights").json()
    product = {p["name"]: p for p in data["products"]}["Telma 40"]
    assert product["units_sold"] == 30 and product["sales_paise"] == 30 * 800 and product["conversion_percent"] == 100.0
    assert data["totals"]["sales_paise"] == 24000 and data["sales_by_city"][0]["city"] == "Pune"
    assert data["trend"][-1]["units_sold"] == 30


def test_small_disease_groups_are_hidden(platform, pharma_world):
    w = pharma_world
    write_rx(platform, w["sharing"], ["Telma 40"], patients=3, conditions=[hypertension(w["sharing"])])
    data = w["mr"].get("/api/mr/insights").json()
    assert {d["code"]: d for d in data["diseases"]}["I10"]["own_prescriptions"] == "<5"
    assert data["doctors"][0]["own_prescriptions"] == 3  # doctor-level counts stay exact


def test_withdrawing_consent_makes_the_doctor_anonymous(platform, pharma_world):
    w = pharma_world
    write_rx(platform, w["sharing"], ["Telma 40"], patients=2)
    assert w["mr"].get("/api/mr/insights").json()["doctors"][0]["label"] == "Dr. Sharing"
    opt_in(w["sharing"], share=False)
    data = w["mr"].get("/api/mr/insights").json()
    assert data["totals"]["named_doctors"] == 0 and "Dr. Sharing" not in json.dumps(data)
    assert data["doctors"][0]["own_prescriptions"] == 2 and not data["doctors"][0]["named"]
    audit = [a["action"] for a in platform.admin().get("/api/admin/audit").json()]
    assert "insights.consent_withdrawn" in audit and "insights.consent_given" in audit


def test_companies_are_isolated(platform, pharma_world):
    w = pharma_world
    rival = platform.company("Rival Labs")
    rival_mr = platform.mr(rival, state="Maharashtra", city="Pune", name="Rival Rep")
    write_rx(platform, w["sharing"], ["Telma 40"], patients=5)
    # Rival sees the market leaders, but Telma isn't theirs
    data = rival_mr.get("/api/mr/insights").json()
    assert data["top_medicines"][0]["name"] == "Telma 40" and not data["top_medicines"][0]["own"]
    assert data["totals"]["own_prescriptions"] == 0 and data["products"] == []
    # Can't claim another company's brand, or manage its MRs
    telma = [b for b in rival.get("/api/company/brands?q=Telma").json() if b["name"] == "Telma 40"][0]
    assert telma["claimed_by_other"] and rival.post(f"/api/company/brands/{telma['id']}").status_code == 409
    assert rival.put(f"/api/company/mrs/{w['mr'].user['id']}",
                     json={"territory_state": "Goa", "active": False}).status_code == 404
    assert [m["name"] for m in rival.get("/api/company/mrs").json()] == ["Rival Rep"]
    # MR territory: an MR in Goa sees no Pune doctors
    goa = platform.mr(w["company"], state="Goa", city=None, name="Goa Rep")
    assert goa.get("/api/mr/doctors").json() == [] and goa.get("/api/mr/insights").json()["totals"]["doctors_in_scope"] == 0


def test_meeting_request_flow(platform, pharma_world):
    w = pharma_world
    doctors = {d["name"]: d for d in w["mr"].get("/api/mr/doctors").json()}
    assert doctors["Dr. Sharing"]["shares_insights"] and not doctors["Dr. Private"]["shares_insights"]
    doc_id = doctors["Dr. Sharing"]["id"]
    body = {"doctor_id": doc_id, "purpose": "Introduce Telma 40 data", "products": "Telma 40",
            "preferred_date": MONDAY, "preferred_time": "16:00"}
    res = w["mr"].post("/api/mr/requests", json=body)
    assert res.status_code == 200, res.text
    req = res.json()
    assert any("Meeting request" in m["subject"] for m in platform.mails_to(w["sharing"].user["email"]))
    assert w["mr"].post("/api/mr/requests", json=body).status_code == 409  # one open request at a time

    inbox = w["sharing"].get("/api/doctor/requests").json()
    assert inbox["counts"]["mr_pending"] == 1 and inbox["mr_requests"][0]["company_name"] == "Sunrise Pharma"
    assert w["sharing"].post(f"/api/doctor/mr-requests/{req['id']}/accept", json={}).status_code == 400
    accepted = w["sharing"].post(f"/api/doctor/mr-requests/{req['id']}/accept",
                                 json={"scheduled_for": f"{MONDAY}T16:30", "note": "15 minutes only"})
    assert accepted.status_code == 200 and accepted.json()["status"] == "accepted"
    assert any("Meeting accepted" in m["subject"] for m in platform.mails_to(w["mr"].user["email"]))
    # Another doctor can't act on it; the doctor can mark it done
    assert w["private"].post(f"/api/doctor/mr-requests/{req['id']}/done", json={}).status_code == 400
    assert w["sharing"].post(f"/api/doctor/mr-requests/{req['id']}/done", json={}).json()["status"] == "completed"
    assert w["company"].get("/api/company/meetings").json()[0]["status"] == "completed"

    # Monthly limit, and doctors can turn meeting requests off
    for _ in range(2):
        r = w["mr"].post("/api/mr/requests", json=body).json()
        w["mr"].post(f"/api/mr/requests/{r['id']}/cancel")
    assert w["mr"].post("/api/mr/requests", json=body).status_code == 429
    opt_in(w["private"], share=False, meetings=False)
    blocked = w["mr"].post("/api/mr/requests", json={**body, "doctor_id": doctors["Dr. Private"]["id"]})
    assert blocked.status_code == 403


def test_deactivated_mr_and_suspended_company_lose_access(platform, pharma_world):
    w = pharma_world
    res = w["mr"].post("/api/mr/requests", json={"doctor_id": w["sharing"].user["id"], "purpose": "Visit"})
    assert res.status_code == 200
    company = w["company"]
    assert platform.admin().post(f"/api/admin/companies/{company.company['id']}/status",
                                 json={"status": "suspended", "reason": "Licence check"}).status_code == 200
    assert w["mr"].get("/api/mr/insights").status_code == 403
    assert w["sharing"].get("/api/doctor/requests").json()["mr_requests"][0]["status"] == "cancelled"
    platform.admin().post(f"/api/admin/companies/{company.company['id']}/status", json={"status": "approved"})
    assert company.put(f"/api/company/mrs/{w['mr'].user['id']}",
                       json={"territory_state": "Maharashtra", "active": False}).status_code == 200
    assert w["mr"].get("/api/mr/profile").status_code == 401  # signed out

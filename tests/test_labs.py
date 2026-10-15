"""Labs: tests from prescriptions go to nearby labs, cheapest / fastest comparison, quotes, bookings, reports."""
import json

import pytest

from test_network import world  # noqa: F401  (fixture)

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def cond(client, code):
    return [c for c in client.get("/api/conditions").json() if c["code"] == code][0]["id"]


@pytest.fixture
def lab_world(platform, world):
    w = world
    w["near_lab"] = platform.lab("Near Diagnostics", km_from_home=0.6, collection_fee=50, report_hours=24)
    w["fast_lab"] = platform.lab("Fast Labs", km_from_home=1.5, home_collection=False, report_hours=6)
    w["far_lab"] = platform.lab("Far Labs", km_from_home=6)
    w["hba1c"] = platform.lab_price(w["near_lab"], "HbA1c (glycated haemoglobin)", 400, mrp=550)
    w["fbs"] = platform.lab_price(w["near_lab"], "Fasting Blood Sugar (FBS)", 80, mrp=100)
    platform.lab_price(w["fast_lab"], "HbA1c (glycated haemoglobin)", 500, mrp=550, report_hours=6)
    platform.lab_price(w["fast_lab"], "Fasting Blood Sugar (FBS)", 100, report_hours=6)
    platform.lab_price(w["far_lab"], "HbA1c (glycated haemoglobin)", 100)
    platform.outbox.clear()
    res = w["gp"].post(f"/api/appointments/{w['appt']['id']}/prescriptions", json={
        "condition_ids": [cond(w["gp"], "E11.9")],
        "tests": [{"test_id": w["hba1c"]["id"], "instructions": "Fasting not needed"}, {"test_id": w["fbs"]["id"]}]})
    assert res.status_code == 200, res.text
    w["rx"] = res.json()
    return w


def test_tests_reach_nearby_labs_only(platform, lab_world):
    w = lab_world
    assert w["rx"]["labs_notified"] == 2 and w["rx"]["pharmacies_notified"] == 0
    assert [t["name"] for t in w["rx"]["tests"]] == ["HbA1c (glycated haemoglobin)", "Fasting Blood Sugar (FBS)"]
    assert any("Test request" in m["subject"] for m in platform.mails_to(w["near_lab"].user["email"]))
    assert not platform.mails_to(w["far_lab"].user["email"])
    assert w["far_lab"].get("/api/lab/requests").json() == []
    req = w["near_lab"].get("/api/lab/requests").json()[0]
    assert [t["name"] for t in req["tests"]] == ["HbA1c (glycated haemoglobin)", "Fasting Blood Sugar (FBS)"]
    assert "Asha" not in json.dumps(req) and req["suggested"][0]["price_paise"] == 40000
    # A prescription needs a medicine or a test
    empty = w["gp"].post(f"/api/appointments/{w['appt']['id']}/prescriptions", json={"diagnosis": "x"})
    assert empty.status_code == 422


def test_cheapest_fastest_and_home_collection(platform, lab_world):
    w = lab_world
    data = w["patient"].get(f"/api/prescriptions/{w['rx']['id']}/lab-options").json()
    assert [o["lab_name"] for o in data["options"]] == ["Near Diagnostics", "Fast Labs"]
    h = data["highlights"]
    assert h["cheapest"]["lab_name"] == "Near Diagnostics" and h["cheapest"]["subtotal_paise"] == 48000
    assert h["fastest"]["lab_name"] == "Fast Labs" and h["fastest"]["report_hours"] == 6
    assert h["home_collection"]["lab_name"] == "Near Diagnostics" and h["home_collection"]["collection_fee_paise"] == 5000
    fastest = w["patient"].get(f"/api/prescriptions/{w['rx']['id']}/lab-options?sort=fastest").json()
    assert fastest["options"][0]["lab_name"] == "Fast Labs"
    # A quote beats the price list
    req = w["fast_lab"].get("/api/lab/requests").json()[0]
    items = [{"prescription_test_id": t["id"], "available": True, "price": 150} for t in req["tests"]]
    assert w["fast_lab"].post(f"/api/lab/requests/{req['id']}/quote", json={"items": items, "report_hours": 4}).status_code == 200
    data = w["patient"].get(f"/api/prescriptions/{w['rx']['id']}/lab-options").json()
    assert data["options"][0]["lab_name"] == "Fast Labs" and data["options"][0]["source"] == "quote"
    assert data["options"][0]["subtotal_paise"] == 30000 and data["highlights"]["fastest"]["report_hours"] == 4
    # Home collection only where the lab offers it
    bad = w["patient"].post(f"/api/prescriptions/{w['rx']['id']}/lab-bookings", json={"lab_id": w["fast_lab"].user["id"],
                                                                                       "collection": "home"})
    assert bad.status_code == 400


def test_booking_report_and_access(platform, lab_world):
    w = lab_world
    rx_id = w["rx"]["id"]
    res = w["patient"].post(f"/api/prescriptions/{rx_id}/lab-bookings",
                            json={"lab_id": w["near_lab"].user["id"], "collection": "home", "preferred_time": "08:00"})
    assert res.status_code == 200, res.text
    booking = res.json()
    assert booking["total_paise"] == 53000 and booking["status"] == "booked" and len(booking["items"]) == 2
    assert booking["address"] == "Flat 4, Shivajinagar, Pune"
    assert any("New test booking" in m["subject"] for m in platform.mails_to(w["near_lab"].user["email"]))
    # Everything booked: the other lab stops getting it, and the patient can't book twice
    assert w["fast_lab"].get("/api/lab/requests").json() == []
    assert w["patient"].post(f"/api/prescriptions/{rx_id}/lab-bookings", json={"lab_id": w["fast_lab"].user["id"]}).status_code == 400
    assert w["patient"].get(f"/api/prescriptions/{rx_id}").json()["remaining_test_ids"] == []
    # The lab sees who to visit
    lab_view = w["near_lab"].get("/api/lab/bookings").json()[0]
    assert lab_view["patient_name"] == "Asha Rao" and lab_view["address"]

    def status(s, **kw):
        return w["near_lab"].post(f"/api/lab/bookings/{booking['id']}/status", json={"status": s, **kw})

    def upload(content, values="[]", name="report.pdf"):
        return w["near_lab"].post(f"/api/lab/bookings/{booking['id']}/report", files={"file": (name, content)},
                                  data={"values": values})

    assert status("accepted").status_code == 200
    assert upload(PDF).status_code == 400            # sample not collected yet
    assert status("sample_collected").status_code == 200
    assert w["patient"].post(f"/api/lab-bookings/{booking['id']}/cancel", json={}).status_code == 400
    assert upload(b"MZ\x90\x00 not a pdf", name="x.pdf").status_code == 400
    values = json.dumps([{"test_id": w["hba1c"]["id"], "name": "HbA1c", "value": "7.2"},
                         {"test_id": w["fbs"]["id"], "name": "Fasting glucose", "value": "96"}])
    platform.outbox.clear()
    res = upload(PDF, values)
    assert res.status_code == 200, res.text
    record_id = res.json()["record_id"]
    assert any("report ready" in m["subject"] for m in platform.mails_to(w["patient"].user["email"]))
    assert any("report ready" in m["subject"] for m in platform.mails_to(w["gp"].user["email"]))

    records = w["patient"].get("/api/my/records").json()
    report = records[0]
    assert report["kind"] == "lab_report" and report["lab_name"] == "Near Diagnostics"
    flags = {x["name"]: (x["value_num"], x["unit"], x["flag"]) for x in report["values"]}
    assert flags["HbA1c"] == (7.2, "%", "high") and flags["Fasting glucose"] == (96, "mg/dL", "normal")
    trends = w["patient"].get("/api/my/trends").json()
    assert {t["name"] for t in trends} == {"HbA1c", "Fasting glucose"}

    url = f"/api/records/{record_id}/file"
    for who in ("patient", "gp", "near_lab"):
        res = w[who].get(url)
        assert res.status_code == 200 and res.content == PDF and res.headers["x-content-type-options"] == "nosniff", who
    for who in ("fast_lab", "near"):
        assert w[who].get(url).status_code == 404, who
    assert platform.gp(name="Dr. Stranger").get(url).status_code == 404
    assert platform.admin().get(url).status_code == 404
    doctor_view = w["gp"].get(f"/api/doctor/patients/{w['patient'].user['id']}/records").json()
    assert doctor_view["records"][0]["id"] == record_id and doctor_view["trends"]

    assert status("completed").status_code == 200
    done = w["patient"].get(f"/api/lab-bookings/{booking['id']}").json()
    assert done["status"] == "completed" and done["payment_status"] == "paid" and done["record_id"] == record_id
    # The lab report is a medical record: the patient can hide it from other doctors but not delete it
    assert w["patient"].delete(f"/api/my/records/{record_id}").status_code == 400


def test_cancel_and_reject_reopen_the_tests(platform, lab_world):
    w = lab_world
    rx_id = w["rx"]["id"]
    booking = w["patient"].post(f"/api/prescriptions/{rx_id}/lab-bookings", json={"lab_id": w["near_lab"].user["id"]}).json()
    assert w["patient"].post(f"/api/lab-bookings/{booking['id']}/cancel", json={}).json()["status"] == "cancelled"
    assert len(w["patient"].get(f"/api/prescriptions/{rx_id}").json()["remaining_test_ids"]) == 2
    assert w["fast_lab"].get("/api/lab/requests").json()  # open again for the other lab
    booking = w["patient"].post(f"/api/prescriptions/{rx_id}/lab-bookings", json={"lab_id": w["fast_lab"].user["id"]}).json()
    rejected = w["fast_lab"].post(f"/api/lab/bookings/{booking['id']}/status", json={"status": "rejected"})
    assert rejected.status_code == 400  # needs a reason
    assert w["fast_lab"].post(f"/api/lab/bookings/{booking['id']}/status",
                              json={"status": "rejected", "reason": "Machine down"}).json()["status"] == "rejected"
    assert len(w["patient"].get(f"/api/prescriptions/{rx_id}").json()["remaining_test_ids"]) == 2
    # A doctor can't cancel a prescription whose tests are booked
    w["patient"].post(f"/api/prescriptions/{rx_id}/lab-bookings", json={"lab_id": w["near_lab"].user["id"]})
    assert w["gp"].post(f"/api/prescriptions/{rx_id}/cancel").status_code == 400


def test_lab_onboarding_and_admin(platform, world):
    pending = platform.lab("Pending Lab", approve=False)
    assert pending.get("/api/auth/me").json()["lab"]["status"] == "pending"
    assert [l["lab_name"] for l in platform.admin().get("/api/admin/labs?status=pending").json()] == ["Pending Lab"]
    # Unapproved labs aren't sent tests and can't add catalogue tests
    assert pending.post("/api/lab-tests", json={"name": "Brand new test"}).status_code == 403
    tests = [t for t in world["gp"].get("/api/lab-tests?q=cbc").json()]
    rx = world["gp"].post(f"/api/appointments/{world['appt']['id']}/prescriptions",
                          json={"tests": [{"test_id": tests[0]["id"]}]}).json()
    assert rx["labs_notified"] == 0
    assert platform.admin().post(f"/api/admin/labs/{pending.user['id']}/status", json={"status": "approved"}).status_code == 200
    assert pending.post("/api/lab-tests", json={"name": "Brand new test", "category": "Blood"}).status_code == 200
    assert world["patient"].post(f"/api/prescriptions/{rx['id']}/lab-share", json={}).json()["labs_notified"] == 1

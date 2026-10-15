"""Patient-uploaded prescriptions: sent to nearby pharmacies & labs, quotations sorted, patient books the best."""
import json

import pytest

from test_network import world  # noqa: F401  (fixture)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
PDF = b"%PDF-1.4\n%%EOF\n"


def upload(patient, medicines=True, tests=False, note="2 strips of each"):
    return patient.post("/api/my/uploads", files={"file": ("rx.jpg", PNG)},
                        data={"want_medicines": str(medicines).lower(), "want_tests": str(tests).lower(), "note": note})


def quote(client, req_id, lines, **extra):
    return client.post(f"/api/partner/uploads/{req_id}/quote", json={"lines": lines, **extra})


@pytest.fixture
def up(platform, world):
    w = world
    w["lab"] = platform.lab("Upload Labs", km_from_home=1.0)
    w["far_lab"] = platform.lab("Far Upload Labs", km_from_home=6)
    platform.outbox.clear()
    res = upload(w["patient"], medicines=True, tests=True)
    assert res.status_code == 200, res.text
    w["up"] = res.json()
    return w


def test_upload_goes_to_nearby_pharmacies_and_labs_only(platform, up):
    w = up
    assert w["up"]["pharmacies_notified"] == 2 and w["up"]["labs_notified"] == 1  # near + mid pharmacies, 1 lab in 3 km
    assert w["up"]["reference"].startswith("UP-") and w["up"]["valid_until"] > w["up"]["created_at"]
    assert any("Uploaded prescription" in m["subject"] for m in platform.mails_to(w["near"].user["email"]))
    assert not platform.mails_to(w["far"].user["email"]) and w["far_lab"].get("/api/partner/uploads").json() == []
    req = w["near"].get("/api/partner/uploads").json()[0]
    assert req["kind"] == "medicines" and req["note"] == "2 strips of each"
    # The pharmacy can open the image while the request is open; a far pharmacy can't
    assert w["near"].get(req["file_url"]).content == PNG
    assert w["far"].get(req["file_url"]).status_code == 404
    assert w["lab"].get("/api/partner/uploads").json()[0]["kind"] == "tests"
    # It's in the patient's health records too
    assert w["patient"].get("/api/my/records").json()[0]["kind"] == "prescription_copy"


def test_needs_home_and_a_valid_file(platform, world):
    newbie = platform.patient(name="No Home")
    assert upload(newbie).status_code == 400
    assert world["patient"].post("/api/my/uploads", files={"file": ("x.exe", b"MZ..")}).status_code == 400
    assert upload(world["patient"], medicines=False, tests=False).status_code == 422


def test_quotes_are_sorted_and_patient_books_the_best(platform, up):
    w = up
    near_req = w["near"].get("/api/partner/uploads").json()[0]
    mid_req = w["mid"].get("/api/partner/uploads").json()[0]
    lines = [{"name": "Dolo 650", "quantity": 15, "unit_price": 2, "mrp": 2.24}, {"name": "Pan 40", "quantity": 15, "unit_price": 9, "mrp": 10.3}]
    assert quote(w["near"], near_req["id"], lines, delivery_available=True, delivery_fee=30, ready_minutes=15, delivery_minutes=45).status_code == 200
    cheaper = [{"name": "Dolo 650", "quantity": 15, "unit_price": 1.8, "mrp": 2.24}, {"name": "Pan 40", "quantity": 15, "unit_price": 0, "available": False}]
    assert quote(w["mid"], mid_req["id"], cheaper, ready_minutes=10).status_code == 200
    assert any("new quotation" in m["subject"].lower() for m in platform.mails_to(w["patient"].user["email"]))
    # Bad quotes are refused
    assert quote(w["near"], near_req["id"], [{"name": "X", "quantity": 1, "unit_price": 0}]).status_code == 422
    assert quote(w["near"], near_req["id"], [{"name": "X", "quantity": 1, "unit_price": 5, "mrp": 4}]).status_code == 422

    d = w["patient"].get(f"/api/my/uploads/{w['up']['id']}").json()
    meds = d["medicines"]
    # Cheapest puts the quote with all items first, even though the other is cheaper for fewer items
    assert [o["partner_name"] for o in meds["options"]] == ["Near Pharmacy", "Mid Pharmacy"]
    assert meds["options"][0]["subtotal_paise"] == 15 * 200 + 15 * 900 and meds["options"][0]["complete"]
    assert meds["highlights"]["cheapest"]["partner_name"] == "Near Pharmacy"
    assert meds["highlights"]["best_discount"]["discount_percent"] > 0
    fastest = w["patient"].get(f"/api/my/uploads/{w['up']['id']}?sort=fastest").json()["medicines"]["options"]
    assert fastest[0]["partner_name"] == "Mid Pharmacy"  # ready in 10 min
    assert w["patient"].get("/api/my/uploads").json()[0]["quotes"] == 2

    q = meds["options"][0]
    assert w["patient"].post(f"/api/my/uploads/{w['up']['id']}/orders", json={"quote_id": q["id"], "fulfilment": "visit"}).status_code == 400
    res = w["patient"].post(f"/api/my/uploads/{w['up']['id']}/orders", json={"quote_id": q["id"], "fulfilment": "delivery"})
    assert res.status_code == 200, res.text
    order = res.json()
    assert order["total_paise"] == q["subtotal_paise"] + 3000 and order["status"] == "placed"
    # The other pharmacy stops getting it, and the patient can't book medicines twice
    assert w["mid"].get("/api/partner/uploads").json() == []
    assert w["mid"].get(mid_req["file_url"]).status_code == 404
    assert w["patient"].post(f"/api/my/uploads/{w['up']['id']}/orders",
                             json={"quote_id": meds["options"][1]["id"], "fulfilment": "pickup"}).status_code == 400

    def st(s, **kw):
        return w["near"].post(f"/api/partner/upload-orders/{order['id']}/status", json={"status": s, **kw})
    assert st("ready").status_code == 400
    assert st("accepted").status_code == 200 and st("ready").status_code == 200
    code = w["patient"].get("/api/my/upload-orders").json()[0]["handover_code"]
    assert st("completed", code="0000" if code != "0000" else "1111").status_code == 400
    done = st("completed", code=code).json()
    assert done["status"] == "completed" and done["payment_status"] == "paid" and done["patient_name"] == "Asha Rao"


def test_lab_quote_booking_and_report(platform, up):
    w = up
    req = w["lab"].get("/api/partner/uploads").json()[0]
    assert quote(w["lab"], req["id"], [{"name": "HbA1c", "quantity": 1, "unit_price": 400, "mrp": 550}],
                 delivery_available=True, delivery_fee=50, ready_minutes=720).status_code == 200
    q = w["patient"].get(f"/api/my/uploads/{w['up']['id']}").json()["tests"]["options"][0]
    assert q["speed_minutes"] == 720 and q["discount_percent"] == 27.3
    order = w["patient"].post(f"/api/my/uploads/{w['up']['id']}/orders",
                              json={"quote_id": q["id"], "fulfilment": "home", "preferred_time": "08:00"}).json()
    assert order["total_paise"] == 45000

    def st(s, **kw):
        return w["lab"].post(f"/api/partner/upload-orders/{order['id']}/status", json={"status": s, **kw})
    assert st("accepted").status_code == 200 and st("sample_collected").status_code == 200
    res = w["lab"].post(f"/api/partner/upload-orders/{order['id']}/report", files={"file": ("r.pdf", PDF)},
                        data={"values": json.dumps([{"name": "HbA1c", "value": "7.1", "unit": "%", "ref_low": 4, "ref_high": 5.6}])})
    assert res.status_code == 200, res.text
    rec = [r for r in w["patient"].get("/api/my/records").json() if r["kind"] == "lab_report"][0]
    assert rec["values"][0]["flag"] == "high" and rec["lab_name"] == "Upload Labs"
    assert w["patient"].get("/api/my/upload-orders").json()[0]["report_record_id"] == rec["id"]
    assert st("completed").json()["status"] == "completed"


def test_cancel_reject_and_widen(platform, up):
    w = up
    req = w["near"].get("/api/partner/uploads").json()[0]
    quote(w["near"], req["id"], [{"name": "Dolo 650", "quantity": 10, "unit_price": 2}])
    q = w["patient"].get(f"/api/my/uploads/{w['up']['id']}").json()["medicines"]["options"][0]
    order = w["patient"].post(f"/api/my/uploads/{w['up']['id']}/orders", json={"quote_id": q["id"], "fulfilment": "pickup"}).json()
    assert w["mid"].get("/api/partner/uploads").json() == []
    assert w["patient"].post(f"/api/my/upload-orders/{order['id']}/cancel", json={}).json()["status"] == "cancelled"
    assert w["mid"].get("/api/partner/uploads").json()  # open again for others
    # Widen to 10 km reaches the far pharmacy
    res = w["patient"].post(f"/api/my/uploads/{w['up']['id']}/widen", json={"radius": 10}).json()
    assert res["newly_notified"] >= 2 and w["far"].get("/api/partner/uploads").json()
    # Closed upload: nobody can quote or book
    w["patient"].post(f"/api/my/uploads/{w['up']['id']}/cancel")
    assert w["near"].get("/api/partner/uploads").json() == []
    assert quote(w["near"], req["id"], [{"name": "Dolo 650", "quantity": 10, "unit_price": 2}]).status_code == 400
    # Another patient can't see or book it
    assert platform.patient(name="Someone Else").get(f"/api/my/uploads/{w['up']['id']}").status_code == 404

"""The nearby network: doctors / pharmacies within 3 km, prescriptions, price comparison, quotes, orders."""
import hashlib
import hmac
import json
import sqlite3
from datetime import datetime

import pytest

from conftest import HOME, MONDAY, WEBHOOK_SECRET, north_of_home


def rows(platform, sql, params=()):
    conn = sqlite3.connect(platform.settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


@pytest.fixture
def world(platform):
    """A patient at HOME, a GP 1 km away with a booking, and pharmacies at 0.5, 2 and 5 km."""
    gp = platform.gp(km_from_home=1)
    patient = platform.patient(name="Asha Rao")
    platform.set_home(patient)
    appt = platform.book(patient, gp).json()["appointment"]
    near = platform.pharmacy("Near Pharmacy", km_from_home=0.5)
    mid = platform.pharmacy("Mid Pharmacy", km_from_home=2, delivery_radius_km=1)
    far = platform.pharmacy("Far Pharmacy", km_from_home=5)
    platform.outbox.clear()
    return {"gp": gp, "patient": patient, "appt": appt, "near": near, "mid": mid, "far": far}


def prescribe(platform, w, items=None, **extra):
    dolo = platform.medicine(w["gp"], "Dolo 650")
    body = {"patient_age": 34, "patient_sex": "F", "diagnosis": "Viral fever", "advice": "Rest, fluids",
            "items": items or [
                {"medicine_id": dolo["id"], "dosage": "1-0-1", "duration": "5 days", "quantity": 10},
                {"name": "Vitamin D3 60K", "dosage": "Once a week", "duration": "4 weeks", "quantity": 4,
                 "form": "capsule"},
            ], **extra}
    res = w["gp"].post(f"/api/appointments/{w['appt']['id']}/prescriptions", json=body)
    assert res.status_code == 200, res.text
    return res.json()


# ---------- Finding doctors & pharmacies nearby ----------
def test_cheapest_doctors_within_3km(platform):
    platform.doctor(name="Dr. Two Km", fee=500, km_from_home=2)
    platform.doctor(name="Dr. One Km", fee=300, km_from_home=1)
    platform.doctor(name="Dr. Far Cheap", fee=200, km_from_home=8)
    platform.doctor(name="Dr. No Location", fee=100)
    platform.gp(name="Dr. GP", fee=250, km_from_home=0.5)
    lat, lng = HOME
    c = platform.client()
    near = c.get(f"/api/doctors?lat={lat}&lng={lng}").json()
    assert [d["name"] for d in near] == ["Dr. GP", "Dr. One Km", "Dr. Two Km"]  # cheapest first, 3 km only
    assert near[0]["distance_km"] == pytest.approx(0.5, abs=0.05)
    nearest = c.get(f"/api/doctors?lat={lat}&lng={lng}&sort=nearest").json()
    assert [d["name"] for d in nearest] == ["Dr. GP", "Dr. One Km", "Dr. Two Km"]
    physios = c.get(f"/api/doctors?lat={lat}&lng={lng}&category=physiotherapist").json()
    assert [d["name"] for d in physios] == ["Dr. One Km", "Dr. Two Km"]
    wide = c.get(f"/api/doctors?lat={lat}&lng={lng}&radius=10").json()
    assert wide[0]["name"] == "Dr. Far Cheap"
    assert len(c.get("/api/doctors").json()) == 5  # no location -> everyone


def test_category_specialities_must_match(platform):
    c = platform.client()
    res = c.post("/api/auth/register-doctor", json={
        "name": "Dr. X", "email": "x@example.com", "phone": "9876543210", "password": "doctor-pass",
        "category": "dentist", "qualification": "BDS", "registration_no": "D-1", "specialities": ["Cardiology"],
        "clinic_name": "Smile", "address": "1 Road", "city": "Pune", "consultation_fee": 300})
    assert res.status_code == 422 and "not a Dentist speciality" in res.json()["detail"]


def test_pharmacy_needs_approval_and_is_found_by_distance(platform):
    pending = platform.pharmacy("Pending Pharmacy", km_from_home=1, approve=False)
    lat, lng = HOME
    assert platform.client().get(f"/api/pharmacies/nearby?lat={lat}&lng={lng}").json() == []
    assert any("approval needed" in m["subject"] for m in platform.outbox)
    platform.admin().post(f"/api/admin/pharmacies/{pending.user['id']}/status", json={"status": "approved"})
    platform.pharmacy("Far", km_from_home=4)
    found = platform.client().get(f"/api/pharmacies/nearby?lat={lat}&lng={lng}").json()
    assert [p["store_name"] for p in found] == ["Pending Pharmacy"]
    assert "drug_licence_no" not in found[0] and "email" not in found[0]
    assert pending.get("/api/auth/me").json()["pharmacy"]["status"] == "approved"


def test_patient_signup_requires_consent(platform):
    res = platform.client().post("/api/auth/register", json={
        "name": "No Consent", "email": "nc@example.com", "phone": "9876543210", "password": "patient-pass"})
    assert res.status_code == 422 and "agree" in res.json()["detail"]


# ---------- Prescriptions go to pharmacies within 3 km ----------
def test_prescription_is_shared_with_pharmacies_within_3km(platform, world):
    rx = prescribe(platform, world)
    assert rx["reference"].startswith("RX-") and rx["pharmacies_notified"] == 2
    requests = rows(platform, "SELECT pharmacy_id, distance_km FROM rx_requests ORDER BY distance_km")
    assert [r["pharmacy_id"] for r in requests] == [world["near"].user["id"], world["mid"].user["id"]]

    to_patient = platform.mails_to(world["patient"].user["email"])
    assert to_patient and "compare prices" in to_patient[0]["subject"].lower()
    pharmacy_mails = platform.mails_to(world["near"].user["email"]) + platform.mails_to(world["mid"].user["email"])
    assert len(pharmacy_mails) == 2
    assert all("Asha" not in m["text"] for m in pharmacy_mails)  # no patient identity before an order
    assert platform.mails_to(world["far"].user["email"]) == []


def test_pharmacies_see_medicines_but_not_the_patient(platform, world):
    rx = prescribe(platform, world)
    reqs = world["near"].get("/api/pharmacy/requests").json()
    assert len(reqs) == 1 and reqs[0]["distance_km"] == pytest.approx(0.5, abs=0.05)
    seen = reqs[0]["prescription"]
    assert "patient" not in seen and [i["name"] for i in seen["items"]] == ["Dolo 650", "Vitamin D3 60K"]
    assert seen["doctor"]["registration_no"] and seen["doctor"]["qualification"] == "MBBS"
    assert "patient" not in world["near"].get(f"/api/prescriptions/{rx['id']}").json()
    assert world["far"].get(f"/api/prescriptions/{rx['id']}").status_code == 404
    other_patient = platform.patient()
    assert other_patient.get(f"/api/prescriptions/{rx['id']}").status_code == 404
    other_doctor = platform.gp(name="Dr. Other")
    assert other_doctor.get(f"/api/prescriptions/{rx['id']}").status_code == 404
    assert world["patient"].get(f"/api/prescriptions/{rx['id']}").json()["patient"]["name"] == "Asha Rao"


def test_prescribing_can_be_restricted_by_category(platform, monkeypatch):
    physio = platform.doctor(km_from_home=1)
    patient = platform.patient()
    appt = platform.book(patient, physio).json()["appointment"]
    monkeypatch.setattr(platform.settings, "PRESCRIBING_CATEGORIES", ["general_physician", "specialist"])
    res = physio.post(f"/api/appointments/{appt['id']}/prescriptions", json={"items": [{"name": "Paracetamol", "quantity": 10}]})
    assert res.status_code == 403
    assert platform.client().get("/api/config").json()["prescribing_categories"] == ["general_physician", "specialist"]


def test_only_the_treating_doctor_can_prescribe_for_an_appointment(platform, world):
    other = platform.gp(name="Dr. Other")
    res = other.post(f"/api/appointments/{world['appt']['id']}/prescriptions", json={"items": [{"name": "X", "quantity": 1}]})
    assert res.status_code == 404


# ---------- Cheapest price: price lists, generic substitutes, quotes ----------
def test_price_lists_pick_cheapest_and_use_generic_substitutes(platform, world):
    platform.stock(world["near"], "Calpol 650", price=20.00, mrp=30.40)   # same salt, ₹2.00 per tablet (strip of 15 -> ₹1.33)
    platform.stock(world["mid"], "Dolo 650", price=30.40)                 # exact brand, strip of 15
    rx = prescribe(platform, world)
    options = world["patient"].get(f"/api/prescriptions/{rx['id']}/options").json()["options"]
    first = options[0]
    assert first["pharmacy"]["store_name"] == "Near Pharmacy"
    line = first["best"]["lines"][0]
    assert line["name"] == "Calpol 650" and line["substitute"] is True
    assert line["unit_price_paise"] == 133 and line["line_paise"] == 1330
    assert first["best"]["source"] == "price_list" and first["best"]["available"] == 1


def test_no_substitute_when_doctor_says_so(platform, world):
    platform.stock(world["near"], "Calpol 650", price=20.00)
    dolo = platform.medicine(world["gp"], "Dolo 650")
    rx = prescribe(platform, world, items=[{"medicine_id": dolo["id"], "quantity": 10, "allow_substitute": False}])
    options = world["patient"].get(f"/api/prescriptions/{rx['id']}/options").json()["options"]
    assert all(o["best"] is None for o in options)  # Calpol not offered for brand-only Dolo


def test_quotes_compete_and_patient_hears_about_cheaper_price(platform, world):
    platform.stock(world["near"], "Dolo 650", price=30.00)  # covers only 1 of 2 items
    rx = prescribe(platform, world)
    item_ids = [i["id"] for i in rx["items"]]
    platform.outbox.clear()
    req = world["mid"].get("/api/pharmacy/requests").json()[0]
    res = world["mid"].post(f"/api/pharmacy/requests/{req['request_id']}/quote", json={
        "items": [{"prescription_item_id": item_ids[0], "available": True, "supplied_name": "Paracetamol 650 (generic)", "unit_price": 1.10},
                  {"prescription_item_id": item_ids[1], "available": True, "unit_price": 30}],
        "delivery_available": False, "ready_in_minutes": 20, "note": "Jan Aushadhi generic"})
    assert res.status_code == 200, res.text

    options = world["patient"].get(f"/api/prescriptions/{rx['id']}/options").json()["options"]
    assert [o["pharmacy"]["store_name"] for o in options] == ["Mid Pharmacy", "Near Pharmacy"]  # complete offer first
    assert options[0]["best"]["source"] == "quote" and options[0]["best"]["subtotal_paise"] == 10 * 110 + 4 * 3000
    cheaper = platform.mails_to(world["patient"].user["email"])
    assert cheaper and "Cheapest price" in cheaper[-1]["subject"]
    assert world["mid"].get("/api/pharmacy/requests?status=quoted").json()[0]["my_quote"]["available"] == 2

    bad = world["near"].post(f"/api/pharmacy/requests/{req['request_id']}/quote", json={
        "items": [{"prescription_item_id": item_ids[0], "available": True, "unit_price": 1}]})
    assert bad.status_code == 404  # another pharmacy's request


def test_declined_pharmacy_drops_out(platform, world):
    platform.stock(world["near"], "Dolo 650", price=30.00)
    rx = prescribe(platform, world)
    req = world["near"].get("/api/pharmacy/requests").json()[0]
    assert world["near"].post(f"/api/pharmacy/requests/{req['request_id']}/decline").status_code == 200
    options = world["patient"].get(f"/api/prescriptions/{rx['id']}/options").json()["options"]
    assert [o["pharmacy"]["store_name"] for o in options] == ["Mid Pharmacy"]


def test_widening_the_radius_reaches_more_pharmacies(platform, world):
    rx = prescribe(platform, world)
    platform.outbox.clear()
    res = world["patient"].post(f"/api/prescriptions/{rx['id']}/share", json={"radius": 10}).json()
    assert res["radius_km"] == 10 and len(res["options"]) == 3 and res["pharmacies_notified"] == 1
    assert len(platform.mails_to(world["far"].user["email"])) == 1
    assert world["patient"].post(f"/api/prescriptions/{rx['id']}/share", json={"radius": 7}).status_code == 400


def test_patient_without_consent_shares_when_they_choose(platform, world):
    conn = sqlite3.connect(platform.settings.DB_PATH)
    conn.execute("UPDATE users SET share_consent_at = NULL WHERE id = ?", (world["patient"].user["id"],))
    conn.commit()
    conn.close()
    rx = prescribe(platform, world)
    assert rx["pharmacies_notified"] == 0
    seen = world["patient"].get(f"/api/prescriptions/{rx['id']}/options").json()
    assert seen["shared"] is False and rows(platform, "SELECT * FROM rx_requests") == []  # viewing never shares
    world["patient"].post(f"/api/prescriptions/{rx['id']}/share", json={})
    assert len(rows(platform, "SELECT * FROM rx_requests")) == 2


# ---------- Orders ----------
def test_pickup_order_flow_closes_other_requests(platform, world):
    platform.stock(world["near"], "Dolo 650", price=30.00)
    rx = prescribe(platform, world)
    item_ids = [i["id"] for i in rx["items"]]
    req = world["mid"].get("/api/pharmacy/requests").json()[0]
    world["mid"].post(f"/api/pharmacy/requests/{req['request_id']}/quote", json={
        "items": [{"prescription_item_id": i, "available": True, "unit_price": 2} for i in item_ids]})
    platform.outbox.clear()

    res = world["patient"].post(f"/api/prescriptions/{rx['id']}/orders", json={
        "pharmacy_id": world["mid"].user["id"], "fulfilment": "pickup", "payment_method": "pharmacy"})
    assert res.status_code == 200, res.text
    order = res.json()["order"]
    assert order["status"] == "placed" and order["total_paise"] == 2 * 100 * (10 + 4)
    to_pharmacy = platform.mails_to(world["mid"].user["email"])[-1]
    assert "Asha Rao" in to_pharmacy["text"] and to_pharmacy["reply_to"] == world["patient"].user["email"]
    assert world["mid"].get(f"/api/prescriptions/{rx['id']}").json()["patient"]["name"] == "Asha Rao"  # now visible

    assert world["near"].get("/api/pharmacy/requests").json() == []  # everything ordered -> closed for others
    assert world["patient"].get(f"/api/prescriptions/{rx['id']}/options").json()["options"] == []

    oid = order["id"]
    mid = world["mid"]
    assert mid.post(f"/api/pharmacy/orders/{oid}/status", json={"status": "completed"}).status_code == 400
    assert mid.post(f"/api/pharmacy/orders/{oid}/status", json={"status": "accepted"}).status_code == 200
    assert mid.post(f"/api/pharmacy/orders/{oid}/status", json={"status": "out_for_delivery"}).status_code == 400
    assert mid.post(f"/api/pharmacy/orders/{oid}/status", json={"status": "ready"}).status_code == 200
    assert mid.post(f"/api/pharmacy/orders/{oid}/status", json={"status": "completed", "otp": "0000x"}).status_code == 400
    otp = world["patient"].get(f"/api/orders/{oid}").json()["delivery_otp"]
    assert otp and len(otp) == 4
    done = mid.post(f"/api/pharmacy/orders/{oid}/status", json={"status": "completed", "otp": otp}).json()
    assert done["status"] == "completed" and done["payment_status"] == "paid"
    subjects = [m["subject"] for m in platform.mails_to(world["patient"].user["email"])]
    assert any("accepted" in s for s in subjects) and any("ready for pickup" in s for s in subjects)
    assert world["near"].post(f"/api/pharmacy/orders/{oid}/status", json={"status": "accepted"}).status_code == 404
    assert world["gp"].get(f"/api/prescriptions/{rx['id']}").json()["orders"][0]["status"] == "completed"


def test_partial_orders_from_two_pharmacies(platform, world):
    platform.stock(world["near"], "Dolo 650", price=15.00)
    rx = prescribe(platform, world)
    p = world["patient"]
    first = p.post(f"/api/prescriptions/{rx['id']}/orders", json={"pharmacy_id": world["near"].user["id"]})
    assert first.status_code == 200 and [i["name"] for i in first.json()["order"]["items"]] == ["Dolo 650"]
    view = p.get(f"/api/prescriptions/{rx['id']}").json()
    assert len(view["remaining_item_ids"]) == 1
    assert p.post(f"/api/prescriptions/{rx['id']}/orders", json={"pharmacy_id": world["near"].user["id"]}).status_code == 400

    req = world["mid"].get("/api/pharmacy/requests").json()[0]
    assert req["open_item_ids"] == view["remaining_item_ids"]
    world["mid"].post(f"/api/pharmacy/requests/{req['request_id']}/quote", json={
        "items": [{"prescription_item_id": view["remaining_item_ids"][0], "available": True, "unit_price": 25}]})
    second = p.post(f"/api/prescriptions/{rx['id']}/orders", json={"pharmacy_id": world["mid"].user["id"]})
    assert second.status_code == 200 and second.json()["order"]["total_paise"] == 4 * 2500


def test_delivery_only_within_pharmacy_delivery_radius(platform, world):
    platform.stock(world["near"], "Dolo 650", price=15.00)
    platform.stock(world["mid"], "Dolo 650", price=15.00)
    rx = prescribe(platform, world)
    p = world["patient"]
    too_far = p.post(f"/api/prescriptions/{rx['id']}/orders", json={"pharmacy_id": world["mid"].user["id"],
                                                                      "fulfilment": "delivery"})
    assert too_far.status_code == 400  # mid delivers within 1 km, patient is 2 km away
    ok = p.post(f"/api/prescriptions/{rx['id']}/orders", json={"pharmacy_id": world["near"].user["id"], "fulfilment": "delivery"})
    order = ok.json()["order"]
    assert order["delivery_fee_paise"] == 3000 and order["delivery_address"] == "Flat 4, Shivajinagar, Pune"
    assert world["near"].post(f"/api/pharmacy/orders/{order['id']}/status", json={"status": "accepted"}).status_code == 200
    assert world["near"].post(f"/api/pharmacy/orders/{order['id']}/status", json={"status": "out_for_delivery"}).status_code == 200


def test_online_payment_order_and_refund_on_rejection(platform, world):
    platform.stock(world["near"], "Dolo 650", price=15.00)
    rx = prescribe(platform, world)
    p = world["patient"]
    res = p.post(f"/api/prescriptions/{rx['id']}/orders", json={"pharmacy_id": world["near"].user["id"],
                                                                  "payment_method": "online"}).json()
    order, pay = res["order"], res["payment"]
    assert order["status"] == "pending_payment" and pay["amount"] == 1000  # 10 tablets × ₹1.00
    assert world["near"].get("/api/pharmacy/orders").json() == []  # not visible until paid

    body = json.dumps({"event": "payment.captured",
                       "payload": {"payment": {"entity": {"id": "pay_med", "order_id": pay["order_id"]}}}}).encode()
    sig = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert platform.client().post("/api/payments/webhook", content=body, headers={"X-Razorpay-Signature": sig}).status_code == 200
    placed = rows(platform, "SELECT status, payment_status FROM medicine_orders")[0]
    assert placed == {"status": "placed", "payment_status": "paid"}

    res = world["near"].post(f"/api/pharmacy/orders/{order['id']}/status", json={"status": "rejected", "reason": "Out of stock"})
    assert res.status_code == 200 and res.json()["payment_status"] == "refunded"
    assert platform.razorpay_calls[-1] == ("POST", "/payments/pay_med/refund", {"amount": 1000})
    mail = platform.mails_to(world["patient"].user["email"])[-1]
    assert "Out of stock" in mail["text"] and "another nearby pharmacy" in mail["text"]


def test_patient_cancels_paid_order_gets_refund(platform, world):
    platform.stock(world["near"], "Dolo 650", price=15.00)
    rx = prescribe(platform, world)
    p = world["patient"]
    res = p.post(f"/api/prescriptions/{rx['id']}/orders", json={"pharmacy_id": world["near"].user["id"],
                                                                  "payment_method": "online"}).json()
    oid, pay = res["order"]["id"], res["payment"]
    verify = p.post(f"/api/orders/{oid}/verify-payment", json={
        "razorpay_order_id": pay["order_id"], "razorpay_payment_id": "pay_c", "razorpay_signature": platform.sign(pay["order_id"], "pay_c")})
    assert verify.json()["outcome"] == "placed"
    res = p.post(f"/api/orders/{oid}/cancel", json={"reason": "Bought elsewhere"}).json()
    assert res["order"]["status"] == "cancelled" and res["order"]["payment_status"] == "refunded"
    assert p.get(f"/api/prescriptions/{rx['id']}").json()["remaining_item_ids"]  # items free again


# ---------- Pharmacy tools ----------
def test_price_list_csv_import_and_export(platform, world):
    csv_text = ("name,generic_name,strength,form,pack_size,mrp,price,in_stock\n"
                "Dolo 650,Paracetamol,650 mg,tablet,15,33.60,30.00,yes\n"
                "New Brand 650,Paracetamol,650 mg,tablet,10,20,18,yes\n"
                "Broken,,,tablet,10,10,12,yes\n"
                "Bad Form,,,lozenge,1,5,5,yes\n")
    res = world["near"].post("/api/pharmacy/stock/import", json={"csv": csv_text}).json()
    assert res["imported"] == 2 and [e["line"] for e in res["errors"]] == [4, 5]
    stock = world["near"].get("/api/pharmacy/stock").json()
    assert {s["name"] for s in stock} == {"Dolo 650", "New Brand 650"}
    exported = world["near"].get("/api/pharmacy/stock.csv").text.splitlines()
    assert exported[0] == "name,generic_name,strength,form,pack_size,mrp,price,in_stock" and len(exported) == 3
    assert world["near"].post("/api/pharmacy/stock/import", json={"csv": "foo,bar\n1,2"}).status_code == 400


def test_pharmacy_licence_change_needs_reverification(platform, world):
    prof = world["near"].get("/api/pharmacy/profile").json()
    body = {k: prof[k] for k in ("name", "phone", "store_name", "pharmacist_name", "pharmacist_reg_no", "gstin", "address",
                                 "city", "pincode", "lat", "lng", "opening_hours", "offers_delivery", "delivery_radius_km")}
    body.update(delivery_fee=prof["delivery_fee_paise"] / 100, accepts_online_payment=prof["accepts_online_payment_setting"])
    res = world["near"].put("/api/pharmacy/profile", json={**body, "drug_licence_no": "NEW-LIC"}).json()
    assert res["status"] == "pending" and res["reverification_required"]
    lat, lng = HOME
    assert "Near Pharmacy" not in [p["store_name"] for p in platform.client().get(f"/api/pharmacies/nearby?lat={lat}&lng={lng}").json()]


def test_doctor_can_cancel_unordered_prescription_only(platform, world):
    platform.stock(world["near"], "Dolo 650", price=15.00)
    rx = prescribe(platform, world)
    rx2 = prescribe(platform, world)
    world["patient"].post(f"/api/prescriptions/{rx2['id']}/orders", json={"pharmacy_id": world["near"].user["id"]})
    assert world["gp"].post(f"/api/prescriptions/{rx2['id']}/cancel").status_code == 400
    assert world["gp"].post(f"/api/prescriptions/{rx['id']}/cancel").json()["status"] == "cancelled"
    assert world["patient"].get(f"/api/prescriptions/{rx['id']}/options").status_code == 400
    assert all(r["prescription"]["id"] != rx["id"] for r in world["mid"].get("/api/pharmacy/requests?status=all").json())


# ---------- Money: settlements ----------
def test_settlements_for_pharmacies_and_doctors(platform, world, monkeypatch):
    monkeypatch.setattr(platform.settings, "COMMISSION_PERCENT", 10.0)
    platform.stock(world["near"], "Dolo 650", price=15.00)
    rx = prescribe(platform, world)
    p = world["patient"]
    res = p.post(f"/api/prescriptions/{rx['id']}/orders", json={"pharmacy_id": world["near"].user["id"], "payment_method": "online"}).json()
    oid, pay = res["order"]["id"], res["payment"]
    p.post(f"/api/orders/{oid}/verify-payment", json={"razorpay_order_id": pay["order_id"], "razorpay_payment_id": "pay_s",
                                                     "razorpay_signature": platform.sign(pay["order_id"], "pay_s")})
    for status in ("accepted", "ready"):
        world["near"].post(f"/api/pharmacy/orders/{oid}/status", json={"status": status})
    otp = p.get(f"/api/orders/{oid}").json()["delivery_otp"]
    world["near"].post(f"/api/pharmacy/orders/{oid}/status", json={"status": "completed", "otp": otp})

    earnings = world["near"].get("/api/pharmacy/earnings").json()
    assert (earnings["earned_paise"], earnings["commission_paise"], earnings["due_paise"]) == (1000, 100, 900)

    admin = platform.admin()
    settle = {(s["payee_role"], s["name"]): s for s in admin.get("/api/admin/settlements").json()}
    assert settle[("pharmacy", "Near Pharmacy")]["due_paise"] == 900
    too_much = admin.post("/api/admin/payouts", json={"payee_role": "pharmacy", "payee_id": world["near"].user["id"], "amount": 10})
    assert too_much.status_code == 400
    paid = admin.post("/api/admin/payouts", json={"payee_role": "pharmacy", "payee_id": world["near"].user["id"],
                                                  "amount": 9, "reference": "UTR123"}).json()
    assert paid["due_paise"] == 0
    assert world["near"].get("/api/pharmacy/earnings").json()["payouts"][0]["reference"] == "UTR123"

    # Doctor: online-paid appointment that took place
    online = platform.book(platform.patient(), world["gp"], time="11:00", payment_method="online").json()
    a, apay = online["appointment"], online["payment"]
    conn = sqlite3.connect(platform.settings.DB_PATH)
    conn.execute("UPDATE appointments SET status = 'completed', payment_status = 'paid' WHERE id = ?", (a["id"],))
    conn.commit()
    conn.close()
    doc = world["gp"].get("/api/doctor/earnings").json()
    assert doc["earned_paise"] == 80000 and doc["due_paise"] == 72000


def test_admin_network_overview(platform, world):
    prescribe(platform, world)
    admin = platform.admin()
    stats = admin.get("/api/admin/network-stats").json()
    assert stats["pharmacies"]["approved"] == 3 and stats["prescriptions_month"] == 1 and stats["catalogue_size"] >= 20
    assert admin.get("/api/admin/medicines?q=paracetamol").json()
    assert len(admin.get("/api/admin/pharmacies?status=approved").json()) == 3
    assert world["patient"].get("/api/admin/network-stats").status_code == 403


# ---------- Database upgrade ----------
def test_existing_platform_database_upgrades_in_place(tmp_path, monkeypatch, platform):
    db_file = tmp_path / "v0.db"
    conn = sqlite3.connect(db_file)
    conn.executescript(platform.db.SCHEMA)
    conn.execute("INSERT INTO users (role, name, email, password_hash, created_at) VALUES ('patient', 'Old', 'old@x.com', 'h', 'now')")
    conn.execute("INSERT INTO sessions VALUES ('tok', 1, 'now', '2999-01-01')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(platform.settings, "DB_PATH", str(db_file))
    platform.db.init_db()
    out = rows(platform, "SELECT id, name, home_lat FROM users")
    assert out == [{"id": 1, "name": "Old", "home_lat": None}]
    assert rows(platform, "SELECT user_id FROM sessions") == [{"user_id": 1}]
    assert rows(platform, "PRAGMA user_version")[0]["user_version"] == len(platform.db.MIGRATIONS)
    conn = sqlite3.connect(db_file)
    conn.execute("INSERT INTO users (role, name, email, password_hash, created_at) VALUES ('pharmacy', 'P', 'p@x.com', 'h', 'now')")
    conn.commit()
    conn.close()


# ---------- Best discount & fastest delivery ----------
def quote_all(pharmacy, rx, prices):
    """prices: list of (unit_price, mrp) per prescription item, in order."""
    req = [r for r in pharmacy.get("/api/pharmacy/requests?status=all").json() if r["prescription"]["id"] == rx["id"]][0]
    res = pharmacy.post(f"/api/pharmacy/requests/{req['request_id']}/quote", json={
        "items": [{"prescription_item_id": i["id"], "available": True, "unit_price": p, "mrp": m}
                  for i, (p, m) in zip(rx["items"], prices)],
        "delivery_available": True, "ready_in_minutes": 10})
    assert res.status_code == 200, res.text


def test_flat_discount_prices_from_catalogue_mrp(platform, world):
    admin = platform.admin()
    dolo = platform.medicine(world["gp"], "Dolo 650")
    admin.put(f"/api/admin/medicines/{dolo['id']}", json={**{k: dolo[k] for k in ("name", "generic_name", "strength", "form",
                                                                                     "pack_size", "rx_required")}, "mrp": 33.60})
    quick = platform.pharmacy("Quick Pharmacy", km_from_home=1, flat_discount_percent=20, delivery_minutes=20)
    rx = prescribe(platform, world, items=[{"medicine_id": dolo["id"], "quantity": 10, "allow_substitute": False}])
    options = world["patient"].get(f"/api/prescriptions/{rx['id']}/options").json()
    quick_offer = next(o for o in options["options"] if o["pharmacy"]["id"] == quick.user["id"])
    line = quick_offer["best"]["lines"][0]
    # MRP ₹33.60 / 15 = ₹2.24 per tablet, 20% off = ₹1.79
    assert (line["mrp_unit_paise"], line["unit_price_paise"], line["price_source"]) == (224, 179, "mrp_discount")
    assert quick_offer["best"]["discount_percent"] == pytest.approx(20.1, abs=0.2)
    assert quick_offer["delivery_minutes"] == 20
    nearby = platform.client().get(f"/api/pharmacies/nearby?lat={HOME[0]}&lng={HOME[1]}").json()
    assert next(p for p in nearby if p["store_name"] == "Quick Pharmacy")["flat_discount_percent"] == 20


def test_cheapest_best_discount_and_fastest_are_highlighted(platform, world):
    quick = platform.pharmacy("Quick Pharmacy", km_from_home=1, delivery_minutes=15)
    disc = platform.pharmacy("Discount Pharmacy", km_from_home=1.5, delivery_minutes=90)
    rx = prescribe(platform, world)
    p = world["patient"]

    quote_all(world["near"], rx, [(2.00, 2.24), (25, 30)])   # total ₹120.00, 15.7% off
    platform.outbox.clear()
    quote_all(quick, rx, [(2.20, 2.24), (27, 30)])           # total ₹130.00, 8.7% off, delivers in 15 min
    assert "Fastest delivery" in platform.mails_to(world["patient"].user["email"])[-1]["subject"]
    platform.outbox.clear()
    quote_all(disc, rx, [(1.50, 2.24), (29, 40)])            # total ₹131.00, 28.2% off
    assert "Best discount" in platform.mails_to(world["patient"].user["email"])[-1]["subject"]

    res = p.get(f"/api/prescriptions/{rx['id']}/options").json()
    h = res["highlights"]
    assert h["cheapest"]["store_name"] == "Near Pharmacy" and h["cheapest"]["total_paise"] == 12000
    assert h["best_discount"]["store_name"] == "Discount Pharmacy" and h["best_discount"]["discount_percent"] == pytest.approx(28.2, abs=0.1)
    assert h["fastest_delivery"]["store_name"] == "Quick Pharmacy" and h["fastest_delivery"]["delivery_minutes"] == 15
    tags = {o["pharmacy"]["store_name"]: o["tags"] for o in res["options"]}
    assert tags["Near Pharmacy"] == ["cheapest"] and tags["Discount Pharmacy"] == ["best_discount"]
    assert tags["Quick Pharmacy"] == ["fastest_delivery"]

    def first(sort):
        return p.get(f"/api/prescriptions/{rx['id']}/options?sort={sort}").json()["options"][0]["pharmacy"]["store_name"]
    assert first("cheapest") == "Near Pharmacy"
    assert first("discount") == "Discount Pharmacy"
    assert first("fastest") == "Quick Pharmacy"
    assert first("nearest") == "Near Pharmacy"
    assert p.get(f"/api/prescriptions/{rx['id']}/options?sort=random").status_code == 422


def test_quote_price_cannot_exceed_mrp(platform, world):
    rx = prescribe(platform, world)
    req = world["near"].get("/api/pharmacy/requests").json()[0]
    res = world["near"].post(f"/api/pharmacy/requests/{req['request_id']}/quote", json={
        "items": [{"prescription_item_id": rx["items"][0]["id"], "available": True, "unit_price": 3, "mrp": 2.24}]})
    assert res.status_code == 400 and "above MRP" in res.json()["detail"]


def test_prescription_email_shows_best_offers(platform, world):
    platform.stock(world["near"], "Dolo 650", price=25.00, mrp=33.60)
    platform.stock(world["mid"], "Dolo 650", price=30.00, mrp=33.60)
    platform.outbox.clear()
    prescribe(platform, world)
    mail = platform.mails_to(world["patient"].user["email"])[0]
    assert "Cheapest" in mail["text"] and "Best discount" in mail["text"] and "Fastest delivery" in mail["text"]
    assert "Near Pharmacy" in mail["text"]

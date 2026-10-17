"""Phase 1 audit fixes: privacy, abandoned holds, late payments, refunds, webhooks, auth hardening, wind-downs."""
import hashlib
import hmac
import json
import sqlite3
from datetime import datetime, timedelta

from conftest import ADMIN_EMAIL, MONDAY, WEBHOOK_SECRET
from test_network import prescribe, world  # noqa: F401  (fixture)


def rows(platform, sql, params=()):
    conn = sqlite3.connect(platform.settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def execute(platform, sql, params=()):
    conn = sqlite3.connect(platform.settings.DB_PATH)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def travel(platform, monkeypatch, delta):
    real = datetime.now()
    monkeypatch.setattr(platform.settings, "now", lambda: real + delta)


def webhook(platform, event):
    body = json.dumps(event).encode()
    sig = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return platform.client().post("/api/payments/webhook", content=body, headers={"X-Razorpay-Signature": sig})


def online_order(platform, w, pharmacy="near"):
    return w["patient"].post(f"/api/prescriptions/{w['rx']['id']}/orders",
                             json={"pharmacy_id": w[pharmacy].user["id"], "payment_method": "online"}).json()


# ---------- Privacy ----------
def test_pharmacies_never_see_diagnosis_before_an_order(platform, world):
    rx = prescribe(platform, world)
    request_view = world["near"].get("/api/pharmacy/requests").json()[0]["prescription"]
    assert "diagnosis" not in request_view and "advice" not in request_view
    assert "diagnosis" not in world["near"].get(f"/api/prescriptions/{rx['id']}").json()
    platform.stock(world["near"], "Dolo 650", price=15)
    world["patient"].post(f"/api/prescriptions/{rx['id']}/orders", json={"pharmacy_id": world["near"].user["id"]})
    assert world["near"].get(f"/api/prescriptions/{rx['id']}").json()["diagnosis"] == "Viral fever"


def test_declined_or_cancelled_prescriptions_are_no_longer_visible_to_pharmacy(platform, world):
    rx = prescribe(platform, world)
    req = world["mid"].get("/api/pharmacy/requests").json()[0]
    world["mid"].post(f"/api/pharmacy/requests/{req['request_id']}/decline")
    assert world["mid"].get(f"/api/prescriptions/{rx['id']}").status_code == 404
    world["gp"].post(f"/api/prescriptions/{rx['id']}/cancel")
    assert world["near"].get(f"/api/prescriptions/{rx['id']}").status_code == 404


# ---------- Abandoned holds & late payments ----------
def test_abandoned_online_order_stops_blocking_after_payment_window(platform, world, monkeypatch):
    platform.stock(world["near"], "Dolo 650", price=15)
    world["rx"] = prescribe(platform, world)
    online_order(platform, world)
    p = world["patient"]
    assert len(p.get(f"/api/prescriptions/{world['rx']['id']}").json()["remaining_item_ids"]) == 1
    travel(platform, monkeypatch, timedelta(minutes=20))
    view = p.get(f"/api/prescriptions/{world['rx']['id']}").json()
    assert len(view["remaining_item_ids"]) == 2 and view["orders"] == []
    assert p.get("/api/my/orders").json() == []
    assert p.get(f"/api/prescriptions/{world['rx']['id']}/options").json()["options"]  # offers visible again
    assert world["gp"].post(f"/api/prescriptions/{world['rx']['id']}/cancel").status_code == 200


def test_late_payment_is_refunded_if_medicines_were_ordered_elsewhere(platform, world, monkeypatch):
    platform.stock(world["near"], "Dolo 650", price=15)
    platform.stock(world["mid"], "Dolo 650", price=16)
    world["rx"] = prescribe(platform, world)
    slow = online_order(platform, world)
    travel(platform, monkeypatch, timedelta(minutes=20))
    p = world["patient"]
    assert p.post(f"/api/prescriptions/{world['rx']['id']}/orders", json={"pharmacy_id": world["mid"].user["id"]}).status_code == 200
    late = p.post(f"/api/orders/{slow['order']['id']}/verify-payment", json={
        "razorpay_order_id": slow["payment"]["order_id"], "razorpay_payment_id": "pay_late",
        "razorpay_signature": platform.sign(slow["payment"]["order_id"], "pay_late")}).json()
    assert late["outcome"] == "refunded"
    assert rows(platform, "SELECT status, payment_status FROM medicine_orders WHERE id = ?", (slow["order"]["id"],))[0] == \
        {"status": "cancelled", "payment_status": "refunded"}


def test_cancelled_order_reopens_other_pharmacies_quotes(platform, world):
    rx = prescribe(platform, world)
    for name, price in (("near", 2), ("mid", 3)):
        req = world[name].get("/api/pharmacy/requests").json()[0]
        world[name].post(f"/api/pharmacy/requests/{req['request_id']}/quote", json={
            "items": [{"prescription_item_id": i["id"], "available": True, "unit_price": price} for i in rx["items"]]})
    p = world["patient"]
    order = p.post(f"/api/prescriptions/{rx['id']}/orders", json={"pharmacy_id": world["near"].user["id"]}).json()["order"]
    assert world["mid"].get("/api/pharmacy/requests?status=quoted").json() == []  # closed while ordered
    p.post(f"/api/orders/{order['id']}/cancel", json={})
    assert len(world["mid"].get("/api/pharmacy/requests?status=quoted").json()) == 1
    options = p.get(f"/api/prescriptions/{rx['id']}/options").json()["options"]
    assert {o["pharmacy"]["store_name"]: o["best"]["source"] for o in options} == \
        {"Near Pharmacy": "quote", "Mid Pharmacy": "quote"}


# ---------- Delivery radius ----------
def test_quote_cannot_promise_delivery_outside_radius(platform, world):
    rx = prescribe(platform, world)
    req = world["mid"].get("/api/pharmacy/requests").json()[0]  # 2 km away, delivers within 1 km
    res = world["mid"].post(f"/api/pharmacy/requests/{req['request_id']}/quote", json={
        "items": [{"prescription_item_id": rx["items"][0]["id"], "available": True, "unit_price": 2}],
        "delivery_available": True})
    assert res.status_code == 400 and "delivery radius" in res.json()["detail"]


# ---------- Webhook & refunds ----------
def test_webhook_rejects_wrong_amount_and_survives_bad_payloads(platform, world):
    platform.stock(world["near"], "Dolo 650", price=15)
    world["rx"] = prescribe(platform, world)
    order = online_order(platform, world)
    oid = order["payment"]["order_id"]
    assert webhook(platform, {"event": "payment.captured", "payload": {}}).status_code == 200
    assert webhook(platform, {"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": "pay_x", "order_id": oid, "amount": 1, "currency": "INR"}}}}).status_code == 200
    assert rows(platform, "SELECT payment_status FROM medicine_orders")[0]["payment_status"] == "pending"
    webhook(platform, {"event": "payment.failed", "payload": {"payment": {"entity": {"id": "pay_f", "order_id": oid}}}})
    webhook(platform, {"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": "pay_ok", "order_id": oid, "amount": order["payment"]["amount"], "currency": "INR"}}}})
    assert rows(platform, "SELECT status, payment_status FROM medicine_orders")[0] == {"status": "placed", "payment_status": "paid"}


def test_refund_webhook_events_update_status(platform, world, monkeypatch):
    platform.stock(world["near"], "Dolo 650", price=15)
    world["rx"] = prescribe(platform, world)
    o = online_order(platform, world)
    p = world["patient"]
    p.post(f"/api/orders/{o['order']['id']}/verify-payment", json={
        "razorpay_order_id": o["payment"]["order_id"], "razorpay_payment_id": "pay_r",
        "razorpay_signature": platform.sign(o["payment"]["order_id"], "pay_r")})
    webhook(platform, {"event": "refund.failed", "payload": {"refund": {"entity": {"id": "rfnd_1", "payment_id": "pay_r"}}}})
    assert rows(platform, "SELECT payment_status FROM medicine_orders")[0]["payment_status"] == "refund_failed"


def test_refund_runs_only_once(platform, world):
    platform.stock(world["near"], "Dolo 650", price=15)
    world["rx"] = prescribe(platform, world)
    o = online_order(platform, world)
    p = world["patient"]
    p.post(f"/api/orders/{o['order']['id']}/verify-payment", json={
        "razorpay_order_id": o["payment"]["order_id"], "razorpay_payment_id": "pay_once",
        "razorpay_signature": platform.sign(o["payment"]["order_id"], "pay_once")})
    p.post(f"/api/orders/{o['order']['id']}/cancel", json={})
    second = platform.medicine_orders.refund_order(o["order"]["id"])
    assert "already" in second
    assert sum(1 for c in platform.razorpay_calls if c[1].endswith("/refund")) == 1


# ---------- Account security ----------
def test_forgot_password_is_throttled_and_old_links_die(platform):
    platform.patient(email="f@example.com")
    c = platform.client()
    for _ in range(5):
        c.post("/api/auth/forgot", json={"email": "f@example.com"})
    mails = [m for m in platform.mails_to("f@example.com") if "Reset" in m["subject"]]
    assert len(mails) == 3
    first_token = mails[0]["text"].split("#reset=")[1].split()[0]
    assert c.post("/api/auth/reset", json={"token": first_token, "password": "new-password-1"}).status_code == 400
    last_token = mails[-1]["text"].split("#reset=")[1].split()[0]
    assert c.post("/api/auth/reset", json={"token": last_token, "password": "new-password-1"}).status_code == 200


def test_ip_lockout_and_unknown_emails(platform):
    c = platform.client()
    for i in range(30):
        c.post("/api/auth/login", json={"email": f"nobody{i}@example.com", "password": "x"})
    platform.patient(email="real@example.com")
    assert c.post("/api/auth/login", json={"email": "real@example.com", "password": "patient-pass"}).status_code == 429


def test_password_change_and_logout_all_sign_out_other_devices(platform):
    phone = platform.patient(email="multi@example.com")
    laptop = platform.client()
    laptop.post("/api/auth/login", json={"email": "multi@example.com", "password": "patient-pass"})
    tablet = platform.client()
    tablet.post("/api/auth/login", json={"email": "multi@example.com", "password": "patient-pass"})
    assert phone.post("/api/auth/logout-all").status_code == 200
    assert laptop.get("/api/auth/me").json()["user"] is None and phone.get("/api/auth/me").json()["user"]
    tablet.post("/api/auth/login", json={"email": "multi@example.com", "password": "patient-pass"})
    phone.post("/api/auth/change-password", json={"current_password": "patient-pass", "new_password": "brand-new-pass"})
    assert tablet.get("/api/auth/me").json()["user"] is None and phone.get("/api/auth/me").json()["user"]


def test_registration_is_rate_limited_per_network(platform):
    c = platform.client()
    codes = [c.post("/api/auth/register", json={"name": "Spam Bot", "email": f"s{i}@example.com", "phone": "9876543210",
                                                "password": "password-123", "share_consent": True}).status_code
             for i in range(12)]
    assert codes.count(200) == 10 and codes[-1] == 429


# ---------- Wind-downs & loopholes ----------
def test_suspending_doctor_cancels_and_refunds_upcoming_appointments(platform):
    doc = platform.doctor()
    pat = platform.patient()
    paid = platform.book(pat, doc, payment_method="online").json()
    pat.post(f"/api/appointments/{paid['appointment']['id']}/verify-payment", json={
        "razorpay_order_id": paid["payment"]["order_id"], "razorpay_payment_id": "pay_doc",
        "razorpay_signature": platform.sign(paid["payment"]["order_id"], "pay_doc")})
    clinic = platform.book(platform.patient(), doc, time="11:00").json()["appointment"]
    platform.outbox.clear()
    admin = platform.admin()
    admin.post(f"/api/admin/doctors/{doc.user['id']}/status", json={"status": "suspended", "reason": "Licence expired"})
    states = {r["id"]: (r["status"], r["payment_status"]) for r in rows(platform, "SELECT * FROM appointments")}
    assert states[paid["appointment"]["id"]] == ("cancelled", "refunded")
    assert states[clinic["id"]] == ("cancelled", "unpaid")
    assert any("cancelled" in m["subject"].lower() for m in platform.mails_to(pat.user["email"]))
    assert rows(platform, "SELECT action FROM audit_log ORDER BY id DESC")[0]["action"] == "doctor.suspended"


def test_suspending_pharmacy_cancels_its_open_orders(platform, world):
    platform.stock(world["near"], "Dolo 650", price=15)
    rx = prescribe(platform, world)
    order = world["patient"].post(f"/api/prescriptions/{rx['id']}/orders",
                                  json={"pharmacy_id": world["near"].user["id"]}).json()["order"]
    platform.admin().post(f"/api/admin/pharmacies/{world['near'].user['id']}/status",
                          json={"status": "suspended", "reason": "Licence lapsed"})
    assert rows(platform, "SELECT status FROM medicine_orders WHERE id = ?", (order["id"],))[0]["status"] == "cancelled"
    assert len(world["patient"].get(f"/api/prescriptions/{rx['id']}").json()["remaining_item_ids"]) == 2


def test_disabling_patient_cancels_their_bookings(platform):
    doc = platform.doctor()
    pat = platform.patient()
    platform.book(pat, doc)
    admin = platform.admin()
    assert admin.post(f"/api/admin/users/{pat.user['id']}/active?active=false").status_code == 200
    assert rows(platform, "SELECT status FROM appointments")[0]["status"] == "cancelled"
    assert any("disabled" in m["subject"] for m in platform.mails_to(pat.user["email"]))


def test_paid_appointment_cannot_be_moved_inside_refund_window(platform, monkeypatch):
    doc = platform.doctor()
    pat = platform.patient()
    res = platform.book(pat, doc, payment_method="online").json()
    a = res["appointment"]
    pat.post(f"/api/appointments/{a['id']}/verify-payment", json={
        "razorpay_order_id": res["payment"]["order_id"], "razorpay_payment_id": "pay_m",
        "razorpay_signature": platform.sign(res["payment"]["order_id"], "pay_m")})
    monkeypatch.setattr(platform.settings, "now", lambda: datetime.fromisoformat(f"{MONDAY}T08:00"))
    moved = pat.post(f"/api/appointments/{a['id']}/reschedule", json={"appointment_date": MONDAY, "appointment_time": "17:00"})
    assert moved.status_code == 400
    assert doc.post(f"/api/appointments/{a['id']}/reschedule",
                    json={"appointment_date": MONDAY, "appointment_time": "17:00"}).status_code == 200


def test_unapproved_accounts_cannot_add_catalogue_medicines(platform):
    pending = platform.pharmacy("New Store", approve=False)
    res = pending.post("/api/medicines", json={"name": "Fakeol 500", "generic_name": "Fake", "mrp": 999})
    assert res.status_code == 403
    dolo = platform.medicine(pending, "Dolo 650")
    pending.put("/api/pharmacy/stock", json={"medicine_id": dolo["id"], "mrp": 999, "price": 900})
    assert platform.medicine(pending, "Dolo 650")["mrp_paise"] is None  # untrusted MRP not used for the catalogue


def test_price_list_export_neutralises_formulas(platform, world):
    admin = platform.admin()
    admin.post("/api/medicines", json={"name": "=HYPERLINK(evil)", "pack_size": 10})
    med = platform.medicine(world["near"], "=HYPERLINK(evil)")
    world["near"].put("/api/pharmacy/stock", json={"medicine_id": med["id"], "mrp": 10, "price": 9})
    assert "'=HYPERLINK(evil)" in world["near"].get("/api/pharmacy/stock.csv").text

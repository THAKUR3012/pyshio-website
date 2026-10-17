"""Phase 2 features: jobs/SLA, moving home, privacy controls, profile, receipts, reviews, admin tools, walk-ins."""
import importlib
import json
import sqlite3
from datetime import datetime, timedelta

from conftest import ADMIN_EMAIL, HOME, MONDAY, north_of_home
from test_network import prescribe, world  # noqa: F401  (fixture)


def rows(platform, sql, params=()):
    conn = sqlite3.connect(platform.settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def jobs_module():
    import jobs
    return importlib.reload(jobs)


def travel(platform, monkeypatch, delta):
    real = datetime.now()
    monkeypatch.setattr(platform.settings, "now", lambda: real + delta)


def complete_order(platform, w, order_id, pharmacy="near"):
    for status in ("accepted", "ready"):
        w[pharmacy].post(f"/api/pharmacy/orders/{order_id}/status", json={"status": status})
    otp = w["patient"].get(f"/api/orders/{order_id}").json()["delivery_otp"]
    return w[pharmacy].post(f"/api/pharmacy/orders/{order_id}/status", json={"status": "completed", "otp": otp})


# ---------- Jobs: pharmacy SLA ----------
def test_pharmacy_is_reminded_then_order_auto_rejected_and_reopened(platform, world, monkeypatch):
    platform.stock(world["near"], "Dolo 650", price=15)
    rx = prescribe(platform, world)
    order = world["patient"].post(f"/api/prescriptions/{rx['id']}/orders",
                                  json={"pharmacy_id": world["near"].user["id"]}).json()["order"]
    jobs = jobs_module()
    platform.outbox.clear()
    travel(platform, monkeypatch, timedelta(minutes=12))
    assert jobs.chase_pharmacies() == {"reminded": 1, "auto_rejected": 0}
    assert "Action needed" in platform.mails_to(world["near"].user["email"])[-1]["subject"]
    assert jobs.chase_pharmacies() == {"reminded": 0, "auto_rejected": 0}  # once only
    travel(platform, monkeypatch, timedelta(minutes=35))
    assert jobs.chase_pharmacies()["auto_rejected"] == 1
    assert rows(platform, "SELECT status FROM medicine_orders WHERE id = ?", (order["id"],))[0]["status"] == "rejected"
    assert "next best pharmacy" in platform.mails_to(world["patient"].user["email"])[-1]["text"]
    assert len(world["patient"].get(f"/api/prescriptions/{rx['id']}").json()["remaining_item_ids"]) == 2


def test_jobs_release_holds_and_purge(platform, world, monkeypatch):
    platform.stock(world["near"], "Dolo 650", price=15)
    rx = prescribe(platform, world)
    world["patient"].post(f"/api/prescriptions/{rx['id']}/orders",
                          json={"pharmacy_id": world["near"].user["id"], "payment_method": "online"})
    jobs = jobs_module()
    travel(platform, monkeypatch, timedelta(days=2))
    summary = jobs.run_all()
    assert "auto_rejected" in summary
    assert rows(platform, "SELECT status FROM medicine_orders")[0]["status"] == "expired"
    assert rows(platform, "SELECT COUNT(*) AS n FROM rate_limits")[0]["n"] == 0


def test_expiring_prescription_nudge(platform, world, monkeypatch):
    prescribe(platform, world)
    jobs = jobs_module()
    travel(platform, monkeypatch, timedelta(days=28))
    platform.outbox.clear()
    assert jobs.nudge_expiring_prescriptions() == 1
    assert jobs.nudge_expiring_prescriptions() == 0


# ---------- Moving home ----------
def test_prescription_follows_the_patient_home(platform, world):
    rx = prescribe(platform, world)
    far_lat, far_lng = north_of_home(4.8)  # next to Far Pharmacy (5 km)
    platform.outbox.clear()
    res = world["patient"].put("/api/my/location", json={"lat": far_lat, "lng": far_lng})
    assert res.json()["prescriptions_moved"] == 1
    assert len(platform.mails_to(world["far"].user["email"])) == 1
    names = {o["pharmacy"]["store_name"] for o in world["patient"].get(f"/api/prescriptions/{rx['id']}/options").json()["options"]}
    assert "Far Pharmacy" in names and "Near Pharmacy" not in names


# ---------- Privacy controls ----------
def test_withdrawn_consent_stops_automatic_sharing(platform, world):
    p = world["patient"]
    assert p.put("/api/my/consent", json={"share": False}).json() == {"share_consent": False}
    assert p.get("/api/auth/me").json()["user"]["share_consent"] is False
    rx = prescribe(platform, world)
    assert rx["pharmacies_notified"] == 0
    p.put("/api/my/consent", json={"share": True})
    assert prescribe(platform, world)["pharmacies_notified"] == 2


def test_data_export_contains_my_records_only(platform, world):
    prescribe(platform, world)
    other = platform.patient(name="Someone Else")
    data = json.loads(world["patient"].get("/api/my/data").text)
    assert data["account"]["email"] == world["patient"].user["email"] and "password_hash" not in data["account"]
    assert len(data["appointments"]) == 1 and len(data["prescriptions"]) == 1
    assert json.loads(other.get("/api/my/data").text)["prescriptions"] == []


def test_delete_account_anonymises_and_cancels(platform, world):
    p = world["patient"]
    assert p.post("/api/my/delete", json={"password": "wrong"}).status_code == 400
    assert p.post("/api/my/delete", json={"password": "patient-pass"}).status_code == 200
    user = rows(platform, "SELECT * FROM users WHERE id = ?", (p.user["id"],))[0]
    assert user["name"] == "Deleted user" and user["email"].endswith(".invalid") and user["phone"] is None
    assert user["is_active"] == 0 and user["deleted_at"]
    assert rows(platform, "SELECT status FROM appointments")[0]["status"] == "cancelled"
    assert platform.client().post("/api/auth/login", json={"email": p.user["email"], "password": "patient-pass"}).status_code == 401


# ---------- Profile ----------
def test_date_of_birth_and_sex_reach_the_doctor(platform, world):
    p = world["patient"]
    assert p.patch("/api/auth/me", json={"name": "Asha Rao", "phone": "9876543210", "dob": "2099-01-01"}).status_code == 422
    p.patch("/api/auth/me", json={"name": "Asha Rao", "phone": "9876543210", "dob": "1990-05-17", "sex": "F"})
    seen = world["gp"].get(f"/api/appointments/{world['appt']['id']}").json()
    assert (seen["patient_dob"], seen["patient_sex"]) == ("1990-05-17", "F")


# ---------- Receipts & invoices ----------
def test_receipt_and_invoice(platform, world):
    p, gp = world["patient"], world["gp"]
    assert p.get(f"/api/appointments/{world['appt']['id']}/receipt").status_code == 400  # not paid yet
    gp.patch(f"/api/appointments/{world['appt']['id']}", json={"payment_status": "paid"})
    receipt = p.get(f"/api/appointments/{world['appt']['id']}/receipt").json()
    assert receipt["total_paise"] == 80000 and receipt["seller"]["name"] == "Dr. Arjun Mehta"

    platform.stock(world["near"], "Dolo 650", price=15, mrp=20)
    rx = prescribe(platform, world)
    order = p.post(f"/api/prescriptions/{rx['id']}/orders", json={"pharmacy_id": world["near"].user["id"]}).json()["order"]
    assert p.get(f"/api/orders/{order['id']}/invoice").status_code == 400
    complete_order(platform, world, order["id"])
    invoice = p.get(f"/api/orders/{order['id']}/invoice").json()
    assert invoice["total_paise"] == 1000 and invoice["lines"][0]["mrp_paise"] == 133
    assert world["mid"].get(f"/api/orders/{order['id']}/invoice").status_code == 404


# ---------- Reviews ----------
def test_reviews_only_after_completion_and_once(platform, world, monkeypatch):
    p = world["patient"]
    appt_id = world["appt"]["id"]
    assert p.post("/api/reviews", json={"appointment_id": appt_id, "rating": 5}).status_code == 400
    monkeypatch.setattr(platform.settings, "now", lambda: datetime.fromisoformat(f"{MONDAY}T11:00"))
    world["gp"].patch(f"/api/appointments/{appt_id}", json={"status": "completed"})
    assert p.post("/api/reviews", json={"appointment_id": appt_id, "rating": 4, "comment": "Very thorough"}).status_code == 200
    assert p.post("/api/reviews", json={"appointment_id": appt_id, "rating": 1}).status_code == 409
    assert platform.patient().post("/api/reviews", json={"appointment_id": appt_id, "rating": 1}).status_code == 404
    doc = platform.client().get(f"/api/doctors?lat={HOME[0]}&lng={HOME[1]}&sort=rated").json()[0]
    assert (doc["rating"], doc["rating_count"]) == (4.0, 1)
    listed = platform.client().get(f"/api/reviews?target_role=doctor&target_id={world['gp'].user['id']}").json()
    assert listed == [{"rating": 4, "comment": "Very thorough", "date": listed[0]["date"], "by": "Asha R."}]
    assert p.get("/api/my/appointments").json()[0]["my_rating"] == 4


# ---------- Admin ----------
def test_admin_users_audit_payouts_and_order_filters(platform, world):
    admin = platform.admin()
    users = admin.get("/api/admin/users?role=pharmacy").json()
    assert {u["name"] for u in users} == {"Ravi Shah"} and len(users) == 3
    assert len(admin.get("/api/admin/users?q=asha").json()) == 1
    admin.post(f"/api/admin/users/{world['far'].user['id']}/active?active=false")
    actions = [a["action"] for a in admin.get("/api/admin/audit").json()]
    assert "user.disable" in actions and "pharmacy.approved" in actions
    assert world["patient"].get("/api/admin/users").status_code == 403

    platform.stock(world["near"], "Dolo 650", price=15)
    rx = prescribe(platform, world)
    world["patient"].post(f"/api/prescriptions/{rx['id']}/orders", json={"pharmacy_id": world["near"].user["id"]})
    assert len(admin.get("/api/admin/orders?q=asha").json()) == 1
    assert admin.get("/api/admin/orders?q=nobody").json() == []
    assert admin.get("/api/admin/orders.csv").text.splitlines()[0].startswith("Sr No,reference")


def test_payout_emails_payee_and_appears_in_history(platform, world):
    platform.stock(world["near"], "Dolo 650", price=15)
    rx = prescribe(platform, world)
    res = world["patient"].post(f"/api/prescriptions/{rx['id']}/orders", json={
        "pharmacy_id": world["near"].user["id"], "payment_method": "online"}).json()
    oid = res["order"]["id"]
    world["patient"].post(f"/api/orders/{oid}/verify-payment", json={
        "razorpay_order_id": res["payment"]["order_id"], "razorpay_payment_id": "pay_po",
        "razorpay_signature": platform.sign(res["payment"]["order_id"], "pay_po")})
    complete_order(platform, world, oid)
    admin = platform.admin()
    admin.post("/api/admin/payouts", json={"payee_role": "pharmacy", "payee_id": world["near"].user["id"], "amount": 10,
                                           "reference": "UTR9"})
    assert "payout" in platform.mails_to(world["near"].user["email"])[-1]["subject"].lower()
    assert admin.get("/api/admin/payouts").json()[0]["reference"] == "UTR9"


# ---------- Doctor tools ----------
def test_walk_in_booking_creates_account_and_invites(platform, world):
    gp = world["gp"]
    platform.outbox.clear()
    res = gp.post("/api/doctor/appointments", json={
        "name": "Walk In", "phone": "9123456780", "email": "walkin@example.com", "service_id": gp.service_id,
        "appointment_date": MONDAY, "appointment_time": "11:30"})
    assert res.status_code == 200 and res.json()["account_created"]
    invite = [m for m in platform.mails_to("walkin@example.com") if "Your appointment with" in m["subject"]]
    assert invite and "#reset=" in invite[0]["text"]
    no_email = gp.post("/api/doctor/appointments", json={
        "name": "Phone Caller", "phone": "9123456781", "service_id": gp.service_id,
        "appointment_date": MONDAY, "appointment_time": "12:00"})
    assert no_email.status_code == 200
    clash = gp.post("/api/doctor/appointments", json={
        "name": "Late", "phone": "9123456782", "service_id": gp.service_id,
        "appointment_date": MONDAY, "appointment_time": "10:00"})
    assert clash.status_code == 409  # world's patient already has 10:00
    existing = gp.post("/api/doctor/appointments", json={
        "name": "Asha", "phone": "9876543210", "email": world["patient"].user["email"], "service_id": gp.service_id,
        "appointment_date": MONDAY, "appointment_time": "17:00"})
    assert existing.json()["appointment"]["patient_id"] == world["patient"].user["id"]


def test_time_off_can_cancel_clashing_bookings(platform, world):
    platform.outbox.clear()
    res = world["gp"].post("/api/doctor/blocks", json={"block_date": MONDAY, "reason": "Conference",
                                                       "cancel_conflicts": True}).json()
    assert len(res["conflicts"]) == 1 and res["cancelled"] == 1
    assert rows(platform, "SELECT status FROM appointments")[0]["status"] == "cancelled"
    assert any("cancelled" in m["subject"].lower() for m in platform.mails_to(world["patient"].user["email"]))


def test_cancelled_prescription_notifies_patient_and_pharmacies(platform, world):
    rx = prescribe(platform, world)
    platform.outbox.clear()
    world["gp"].post(f"/api/prescriptions/{rx['id']}/cancel")
    recipients = {m["to"] for m in platform.outbox}
    assert {world["patient"].user["email"], world["near"].user["email"], world["mid"].user["email"]} <= recipients


def test_rejected_partner_can_reapply(platform):
    doc = platform.doctor(approve=False)
    admin = platform.admin()
    admin.post(f"/api/admin/doctors/{doc.user['id']}/status", json={"status": "rejected", "reason": "Unreadable licence"})
    profile = doc.get("/api/doctor/profile").json()
    body = {k: profile[k] for k in ("name", "phone", "qualification", "registration_no", "registration_council",
                                    "experience_years", "specialities", "bio", "clinic_name", "address", "city",
                                    "pincode", "slot_minutes", "accepts_clinic_payment", "category", "lat", "lng")}
    body["accepts_online_payment"] = profile["accepts_online_payment_setting"]
    platform.outbox.clear()
    res = doc.put("/api/doctor/profile", json=body).json()
    assert res["status"] == "pending"
    assert any(m["to"] == ADMIN_EMAIL for m in platform.outbox)


def test_welcome_email(platform):
    p = platform.patient(email="hello@example.com")
    assert any("Welcome" in m["subject"] for m in platform.mails_to("hello@example.com"))

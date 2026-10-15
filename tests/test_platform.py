import importlib
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from conftest import ADMIN_EMAIL, MONDAY, SUNDAY, TUESDAY, WEBHOOK_SECRET


def rows(platform, sql, params=()):
    conn = sqlite3.connect(platform.settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def travel_to(platform, monkeypatch, when: datetime):
    monkeypatch.setattr(platform.settings, "now", lambda: when)


# ---------- Accounts ----------
def test_patient_register_login_logout(platform):
    c = platform.client()
    res = c.post("/api/auth/register", json={"name": "Ravi", "email": "Ravi@Example.com",
                                             "phone": "9876543210", "password": "secret-123", "share_consent": True})
    assert res.status_code == 200
    assert c.get("/api/auth/me").json()["user"]["email"] == "ravi@example.com"
    stored = rows(platform, "SELECT password_hash FROM users WHERE email = 'ravi@example.com'")[0]["password_hash"]
    assert "secret-123" not in stored and stored.startswith("pbkdf2_sha256$")

    c.post("/api/auth/logout")
    assert c.get("/api/auth/me").json()["user"] is None
    again = platform.client().post("/api/auth/register", json={"name": "Ravi", "email": "ravi@example.com",
                                                               "phone": "9876543210", "password": "secret-123", "share_consent": True})
    assert again.status_code == 409
    assert platform.client().post("/api/auth/login", json={"email": "ravi@example.com", "password": "nope"}).status_code == 401
    assert platform.client().post("/api/auth/login", json={"email": "RAVI@example.com", "password": "secret-123"}).status_code == 200


def test_registration_validation(platform):
    c = platform.client()
    for bad in [{"email": "x"}, {"phone": "12"}, {"password": "short"}, {"name": "A"}]:
        body = {"name": "Ravi Kumar", "email": "r@example.com", "phone": "9876543210", "password": "long-enough", **bad}
        res = c.post("/api/auth/register", json=body)
        assert res.status_code == 422 and isinstance(res.json()["detail"], str)


def test_login_lockout_after_repeated_failures(platform):
    p = platform.patient(email="lock@example.com")
    c = platform.client()
    for _ in range(8):
        c.post("/api/auth/login", json={"email": "lock@example.com", "password": "wrong"})
    assert c.post("/api/auth/login", json={"email": "lock@example.com", "password": "patient-pass"}).status_code == 429


def test_password_reset_flow(platform):
    p = platform.patient(email="forgot@example.com")
    res = platform.client().post("/api/auth/forgot", json={"email": "forgot@example.com"})
    assert res.status_code == 200
    unknown = platform.client().post("/api/auth/forgot", json={"email": "nobody@example.com"})
    assert unknown.json() == res.json()  # no hint which emails exist

    mail = platform.mails_to("forgot@example.com")[-1]
    token = mail["text"].split("#reset=")[1].split()[0]
    assert platform.client().post("/api/auth/reset", json={"token": token, "password": "brand-new-pass"}).status_code == 200
    assert p.get("/api/auth/me").json()["user"] is None  # old sessions logged out
    assert platform.client().post("/api/auth/reset", json={"token": token, "password": "another-pass"}).status_code == 400
    login = platform.client().post("/api/auth/login", json={"email": "forgot@example.com", "password": "brand-new-pass"})
    assert login.status_code == 200


def test_disabled_user_cannot_log_in(platform):
    p = platform.patient(email="bad@example.com")
    assert platform.admin().post(f"/api/admin/users/{p.user['id']}/active?active=false").status_code == 200
    assert p.get("/api/my/appointments").status_code == 401
    assert platform.client().post("/api/auth/login", json={"email": "bad@example.com", "password": "patient-pass"}).status_code == 403


# ---------- Doctor onboarding ----------
def test_doctor_must_be_approved_before_patients_see_them(platform):
    doc = platform.doctor(approve=False)
    assert doc.get("/api/auth/me").json()["doctor"]["status"] == "pending"
    assert platform.client().get("/api/doctors").json() == []
    assert platform.client().get(f"/api/doctors/{doc.slug}").status_code == 404
    assert doc.get(f"/api/doctors/{doc.slug}").status_code == 200  # owner preview
    patient = platform.patient()
    assert platform.book(patient, doc).status_code == 404

    subjects = [m["subject"] for m in platform.outbox]
    assert any("under review" in s for s in subjects)
    assert any(m["to"] == ADMIN_EMAIL and "approval needed" in m["subject"] for m in platform.outbox)

    admin = platform.admin()
    assert admin.post(f"/api/admin/doctors/{doc.user['id']}/status", json={"status": "rejected"}).status_code == 400
    assert admin.post(f"/api/admin/doctors/{doc.user['id']}/status", json={"status": "approved"}).status_code == 200
    listed = platform.client().get("/api/doctors").json()
    assert [d["slug"] for d in listed] == [doc.slug]
    assert listed[0]["fee_from"] == 800
    assert "registration_no" not in listed[0] and "email" not in listed[0]
    assert any("profile is live" in m["subject"] for m in platform.mails_to(doc.user["email"]))


def test_doctor_search_by_city_and_speciality(platform):
    platform.doctor(name="Dr. A", city="pune", specialities=["Neurological"])
    platform.doctor(name="Dr. B", city="Mumbai", specialities=["Sports injury"])
    c = platform.client()
    assert c.get("/api/cities").json() == ["Mumbai", "Pune"]
    assert [d["name"] for d in c.get("/api/doctors?city=Pune").json()] == ["Dr. A"]
    assert [d["name"] for d in c.get("/api/doctors?speciality=Sports injury").json()] == ["Dr. B"]
    assert [d["name"] for d in c.get("/api/doctors?q=dr. b").json()] == ["Dr. B"]


def test_changing_credentials_requires_reapproval(platform):
    doc = platform.doctor()
    profile = doc.get("/api/doctor/profile").json()
    body = {k: profile[k] for k in ("name", "phone", "qualification", "registration_council", "experience_years",
                                    "specialities", "bio", "clinic_name", "address", "city", "pincode", "slot_minutes",
                                    "accepts_clinic_payment")}
    body["accepts_online_payment"] = profile["accepts_online_payment_setting"]
    res = doc.put("/api/doctor/profile", json={**body, "registration_no": profile["registration_no"], "bio": "Updated"})
    assert res.json()["status"] == "approved"
    res = doc.put("/api/doctor/profile", json={**body, "registration_no": "NEW-123"})
    assert res.json()["status"] == "pending" and res.json()["reverification_required"]
    assert platform.client().get("/api/doctors").json() == []


def test_only_admin_can_use_admin_endpoints(platform):
    doc = platform.doctor(approve=False)
    patient = platform.patient()
    for c, code in [(platform.client(), 401), (patient, 403), (doc, 403)]:
        assert c.get("/api/admin/stats").status_code == code
        assert c.post(f"/api/admin/doctors/{doc.user['id']}/status", json={"status": "approved"}).status_code == code
    assert patient.get("/api/doctor/appointments").status_code == 403


# ---------- Doctor schedule ----------
def test_hours_services_and_time_off(platform):
    doc = platform.doctor()
    bad = doc.put("/api/doctor/hours", json=[{"weekday": 0, "start_time": "09:00", "end_time": "12:00"},
                                            {"weekday": 0, "start_time": "11:00", "end_time": "14:00"}])
    assert bad.status_code == 400
    doc.put("/api/doctor/hours", json=[{"weekday": 0, "start_time": "09:00", "end_time": "11:00"}])
    slots = platform.client().get(f"/api/doctors/{doc.slug}/slots?date={MONDAY}&service_id={doc.service_id}").json()
    assert slots["all_slots"] == ["09:00", "09:30", "10:00", "10:30"]
    closed = platform.client().get(f"/api/doctors/{doc.slug}/slots?date={TUESDAY}&service_id={doc.service_id}").json()
    assert closed["open"] is False

    long_service = doc.post("/api/doctor/services", json={"name": "Initial assessment", "duration": 60, "fee": 1200}).json()
    long_id = [s for s in long_service if s["duration"] == 60][0]["id"]
    long_slots = platform.client().get(f"/api/doctors/{doc.slug}/slots?date={MONDAY}&service_id={long_id}").json()
    assert long_slots["all_slots"] == ["09:00", "09:30", "10:00"]  # must finish by 11:00

    assert doc.put(f"/api/doctor/services/{doc.service_id}", json={"name": "Consultation", "duration": 30, "fee": 800, "active": False}).status_code == 200
    assert doc.put(f"/api/doctor/services/{long_id}", json={"name": "Initial", "duration": 60, "fee": 1200, "active": False}).status_code == 400

    doc.post("/api/doctor/blocks", json={"block_date": MONDAY, "start_time": "09:00", "end_time": "10:00", "reason": "Training"})
    free = platform.client().get(f"/api/doctors/{doc.slug}/slots?date={MONDAY}&service_id={long_id}").json()["slots"]
    assert free == ["10:00"]


def test_default_hours_and_sunday_closed(platform):
    doc = platform.doctor()
    sunday = platform.client().get(f"/api/doctors/{doc.slug}/slots?date={SUNDAY}&service_id={doc.service_id}").json()
    assert sunday["open"] is False
    monday = platform.client().get(f"/api/doctors/{doc.slug}/slots?date={MONDAY}&service_id={doc.service_id}").json()
    assert monday["all_slots"][0] == "10:00" and "12:30" in monday["all_slots"] and "13:00" not in monday["all_slots"]
    assert "17:00" in monday["all_slots"] and monday["all_slots"][-1] == "19:30"


# ---------- Booking ----------
def test_pay_at_clinic_booking_is_stored_and_notifies_both(platform):
    doc = platform.doctor()
    patient = platform.patient(name="Asha Rao")
    res = platform.book(patient, doc, reason="Knee pain after running")
    assert res.status_code == 200, res.text
    appt = res.json()["appointment"]
    assert appt["status"] == "confirmed" and appt["payment_status"] == "unpaid" and appt["reference"].startswith("PY-")

    row = rows(platform, "SELECT * FROM appointments")[0]
    assert (row["doctor_id"], row["patient_id"]) == (doc.user["id"], patient.user["id"])
    assert (row["appointment_date"], row["appointment_time"], row["duration"], row["fee"]) == (MONDAY, "10:00", 30, 800)
    assert row["reason"] == "Knee pain after running"

    to_patient = platform.mails_to(patient.user["email"])[-1]
    to_doctor = platform.mails_to(doc.user["email"])[-1]
    assert to_patient["subject"].startswith("Your appointment is confirmed") and to_patient["ics"]
    assert to_doctor["subject"].startswith("New appointment booked")
    assert "Knee pain after running" in to_doctor["text"] and "Asha Rao" in to_doctor["text"]
    assert to_doctor["reply_to"] == patient.user["email"]
    assert "₹800" in to_patient["text"]

    slots = platform.client().get(f"/api/doctors/{doc.slug}/slots?date={MONDAY}&service_id={doc.service_id}").json()
    assert "10:00" not in slots["slots"]


def test_booking_requires_patient_login(platform):
    doc = platform.doctor()
    assert platform.book(platform.client(), doc).status_code == 401
    assert platform.book(doc, doc).status_code == 403


def test_no_double_booking_even_concurrently(platform):
    doc = platform.doctor()
    patients = [platform.patient() for _ in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        codes = list(pool.map(lambda p: platform.book(p, doc).status_code, patients))
    assert sorted(codes) == [200] + [409] * 5
    assert len(rows(platform, "SELECT * FROM appointments")) == 1


def test_doctors_have_independent_diaries(platform):
    a, b = platform.doctor(name="Dr. A"), platform.doctor(name="Dr. B")
    p1, p2 = platform.patient(), platform.patient()
    assert platform.book(p1, a).status_code == 200
    assert platform.book(p2, b).status_code == 200


def test_invalid_booking_requests(platform):
    doc = platform.doctor()
    p = platform.patient()
    past = (datetime.now() - timedelta(days=1)).date().isoformat()
    assert platform.book(p, doc, day=past).status_code == 400
    assert platform.book(p, doc, day=SUNDAY).status_code == 400
    assert platform.book(p, doc, time="10:10").status_code == 400
    assert platform.book(p, doc, time="13:00").status_code == 400  # outside hours
    other = platform.doctor(name="Dr. Other")
    res = p.post("/api/appointments", json={"doctor_slug": doc.slug, "service_id": other.service_id,
                                            "appointment_date": MONDAY, "appointment_time": "10:00"})
    assert res.status_code == 404  # another doctor's service


# ---------- Privacy: everyone sees only their own ----------
def test_patients_and_doctors_only_see_their_own(platform):
    doc_a, doc_b = platform.doctor(name="Dr. A"), platform.doctor(name="Dr. B")
    pat_a, pat_b = platform.patient(name="Patient A"), platform.patient(name="Patient B")
    appt_a = platform.book(pat_a, doc_a).json()["appointment"]
    appt_b = platform.book(pat_b, doc_b).json()["appointment"]

    assert [a["id"] for a in pat_a.get("/api/my/appointments").json()] == [appt_a["id"]]
    assert [a["id"] for a in doc_a.get("/api/doctor/appointments").json()] == [appt_a["id"]]
    assert [p["name"] for p in doc_a.get("/api/doctor/patients").json()] == ["Patient A"]

    for outsider in (pat_b, doc_b):
        assert outsider.get(f"/api/appointments/{appt_a['id']}").status_code == 404
        assert outsider.post(f"/api/appointments/{appt_a['id']}/cancel", json={}).status_code == 404
        assert outsider.post(f"/api/appointments/{appt_a['id']}/reschedule",
                             json={"appointment_date": MONDAY, "appointment_time": "11:00"}).status_code == 404
        assert outsider.post(f"/api/appointments/{appt_a['id']}/messages", json={"body": "hi"}).status_code == 404
    assert doc_b.patch(f"/api/appointments/{appt_a['id']}", json={"doctor_notes": "x"}).status_code == 404
    assert doc_b.get(f"/api/doctor/patients/{pat_a.user['id']}").status_code == 404

    # Patients never see the doctor's private notes or contact email
    doc_a.patch(f"/api/appointments/{appt_a['id']}", json={"doctor_notes": "Grade 2 sprain"})
    patient_view = pat_a.get(f"/api/appointments/{appt_a['id']}").json()
    assert "doctor_notes" not in patient_view and "doctor_email" not in patient_view
    assert doc_a.get(f"/api/appointments/{appt_a['id']}").json()["doctor_notes"] == "Grade 2 sprain"
    assert len(platform.admin().get("/api/admin/appointments").json()) == 2


# ---------- Changes, messages, notifications ----------
def test_reschedule_cancel_and_messages_notify_the_other_side(platform):
    doc = platform.doctor()
    pat = platform.patient()
    appt = platform.book(pat, doc).json()["appointment"]
    platform.outbox.clear()

    res = pat.post(f"/api/appointments/{appt['id']}/reschedule",
                   json={"appointment_date": MONDAY, "appointment_time": "11:00", "message": "Running late from work"})
    assert res.status_code == 200
    doctor_mail = platform.mails_to(doc.user["email"])[-1]
    assert doctor_mail["subject"].startswith("Appointment rescheduled")
    assert "Was:" in doctor_mail["text"] and "Running late from work" in doctor_mail["text"]

    platform.outbox.clear()
    pat.post(f"/api/appointments/{appt['id']}/messages", json={"body": "Should I bring my MRI?"})
    assert [m["to"] for m in platform.outbox] == [doc.user["email"]]
    doc.post(f"/api/appointments/{appt['id']}/messages", json={"body": "Yes please"})
    assert platform.outbox[-1]["to"] == pat.user["email"] and "Yes please" in platform.outbox[-1]["text"]
    assert platform.admin().post(f"/api/appointments/{appt['id']}/messages", json={"body": "x"}).status_code == 403

    thread = pat.get(f"/api/appointments/{appt['id']}").json()["messages"]
    assert [m["body"] for m in thread] == ["Running late from work", "Should I bring my MRI?", "Yes please"]

    platform.outbox.clear()
    res = doc.post(f"/api/appointments/{appt['id']}/cancel", json={"message": "Clinic closed for emergency"})
    assert res.status_code == 200
    patient_mail = platform.mails_to(pat.user["email"])[-1]
    assert "Cancelled by the doctor" in patient_mail["text"] and "Clinic closed for emergency" in patient_mail["text"]
    assert pat.post(f"/api/appointments/{appt['id']}/cancel", json={}).status_code == 400


def test_doctor_marks_visit_only_after_it_happens(platform, monkeypatch):
    doc = platform.doctor()
    pat = platform.patient()
    appt = platform.book(pat, doc).json()["appointment"]
    assert doc.patch(f"/api/appointments/{appt['id']}", json={"status": "completed"}).status_code == 400
    assert doc.patch(f"/api/appointments/{appt['id']}", json={"payment_status": "paid"}).status_code == 200

    travel_to(platform, monkeypatch, datetime.fromisoformat(f"{MONDAY}T10:45"))
    res = doc.patch(f"/api/appointments/{appt['id']}", json={"status": "completed"})
    assert res.status_code == 200 and res.json()["status"] == "completed"
    assert pat.patch(f"/api/appointments/{appt['id']}", json={"status": "no_show"}).status_code == 403
    stats = doc.get("/api/doctor/stats").json()
    assert stats["patients"] == 1
    history = doc.get(f"/api/doctor/patients/{pat.user['id']}").json()
    assert history["appointments"][0]["status"] == "completed"


# ---------- Online payment ----------
def test_online_payment_holds_slot_then_confirms(platform):
    doc = platform.doctor()
    pat, other = platform.patient(), platform.patient()
    res = platform.book(pat, doc, payment_method="online")
    assert res.status_code == 200, res.text
    appt, pay = res.json()["appointment"], res.json()["payment"]
    assert appt["status"] == "pending_payment" and pay["amount"] == 80000 and pay["order_id"]
    assert platform.razorpay_calls[0][2]["amount"] == 80000
    assert platform.book(other, doc).status_code == 409  # slot is held
    assert platform.outbox == [] or all("confirmed" not in m["subject"] for m in platform.outbox[-2:])

    bad = pat.post(f"/api/appointments/{appt['id']}/verify-payment", json={
        "razorpay_order_id": pay["order_id"], "razorpay_payment_id": "pay_1", "razorpay_signature": "forged"})
    assert bad.status_code == 400

    good = pat.post(f"/api/appointments/{appt['id']}/verify-payment", json={
        "razorpay_order_id": pay["order_id"], "razorpay_payment_id": "pay_1",
        "razorpay_signature": platform.sign(pay["order_id"], "pay_1")})
    assert good.status_code == 200 and good.json()["outcome"] == "confirmed"
    row = rows(platform, "SELECT * FROM appointments")[0]
    assert (row["status"], row["payment_status"], row["razorpay_payment_id"]) == ("confirmed", "paid", "pay_1")
    assert platform.mails_to(doc.user["email"])[-1]["subject"].startswith("New appointment booked")


def test_webhook_confirms_when_browser_closed_and_is_idempotent(platform):
    doc = platform.doctor()
    pat = platform.patient()
    pay = platform.book(pat, doc, payment_method="online").json()["payment"]
    body = json.dumps({"event": "payment.captured",
                       "payload": {"payment": {"entity": {"id": "pay_9", "order_id": pay["order_id"]}}}}).encode()
    import hashlib
    import hmac
    sig = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    c = platform.client()
    assert c.post("/api/payments/webhook", content=body, headers={"X-Razorpay-Signature": "bad"}).status_code == 400
    assert c.post("/api/payments/webhook", content=body, headers={"X-Razorpay-Signature": sig}).status_code == 200
    sent = len(platform.outbox)
    assert c.post("/api/payments/webhook", content=body, headers={"X-Razorpay-Signature": sig}).status_code == 200
    assert len(platform.outbox) == sent  # no duplicate emails
    assert rows(platform, "SELECT status, payment_status FROM appointments")[0] == {"status": "confirmed", "payment_status": "paid"}


def test_unpaid_hold_expires_and_late_payment_is_refunded(platform, monkeypatch):
    doc = platform.doctor()
    slow, fast = platform.patient(), platform.patient()
    res = platform.book(slow, doc, payment_method="online").json()
    appt, pay = res["appointment"], res["payment"]

    travel_to(platform, monkeypatch, datetime.now() + timedelta(minutes=20))
    assert slow.get(f"/api/appointments/{appt['id']}/payment").status_code == 400
    assert platform.book(fast, doc).status_code == 200  # hold expired, slot free again

    late = slow.post(f"/api/appointments/{appt['id']}/verify-payment", json={
        "razorpay_order_id": pay["order_id"], "razorpay_payment_id": "pay_late",
        "razorpay_signature": platform.sign(pay["order_id"], "pay_late")})
    assert late.json()["outcome"] == "refunded"
    assert platform.razorpay_calls[-1][1] == "/payments/pay_late/refund"
    row = rows(platform, "SELECT status, payment_status FROM appointments WHERE id = ?", (appt["id"],))[0]
    assert row == {"status": "cancelled", "payment_status": "refunded"}
    assert "refund" in platform.mails_to(slow.user["email"])[-1]["text"].lower()


def test_refund_policy(platform, monkeypatch):
    doc = platform.doctor()

    def paid_booking(time):
        p = platform.patient()
        res = platform.book(p, doc, time=time, payment_method="online").json()
        a, pay = res["appointment"], res["payment"]
        p.post(f"/api/appointments/{a['id']}/verify-payment", json={
            "razorpay_order_id": pay["order_id"], "razorpay_payment_id": f"pay_{time}",
            "razorpay_signature": platform.sign(pay["order_id"], f"pay_{time}")})
        return p, a

    early_p, early = paid_booking("10:00")
    res = early_p.post(f"/api/appointments/{early['id']}/cancel", json={}).json()
    assert res["appointment"]["payment_status"] == "refunded" and "full refund" in res["refund_note"]

    late_p, late = paid_booking("11:00")
    by_doctor_p, by_doctor = paid_booking("12:00")
    travel_to(platform, monkeypatch, datetime.fromisoformat(f"{MONDAY}T08:00"))  # 3 hours before
    res = late_p.post(f"/api/appointments/{late['id']}/cancel", json={}).json()
    assert res["appointment"]["payment_status"] == "not_refundable"
    res = doc.post(f"/api/appointments/{by_doctor['id']}/cancel", json={}).json()
    assert res["appointment"]["payment_status"] == "refunded"  # doctor cancels -> always refunded


def test_failed_refund_is_flagged_and_can_be_retried(platform, monkeypatch):
    doc = platform.doctor()
    pat = platform.patient()
    res = platform.book(pat, doc, payment_method="online").json()
    a, pay = res["appointment"], res["payment"]
    pat.post(f"/api/appointments/{a['id']}/verify-payment", json={
        "razorpay_order_id": pay["order_id"], "razorpay_payment_id": "pay_x",
        "razorpay_signature": platform.sign(pay["order_id"], "pay_x")})

    def broken(*args, **kwargs):
        raise platform.payments.PaymentError("Gateway down")
    monkeypatch.setattr(platform.payments, "refund", broken)
    res = pat.post(f"/api/appointments/{a['id']}/cancel", json={}).json()
    assert res["appointment"]["payment_status"] == "refund_failed"
    admin = platform.admin()
    assert admin.get("/api/admin/stats").json()["refunds_failed"] == 1

    monkeypatch.undo()  # restores everything incl. real refund; re-stub the gateway
    monkeypatch.setattr(platform.payments, "_request", lambda m, p, payload=None: {"id": "rfnd_retry"})
    monkeypatch.setattr(platform.mailer, "send_email", lambda *a, **k: True)
    res = admin.post(f"/api/admin/appointments/{a['id']}/retry-refund")
    assert res.status_code == 200 and res.json()["appointment"]["payment_status"] == "refunded"


def test_online_payment_unavailable_without_keys(platform, monkeypatch):
    doc = platform.doctor()
    monkeypatch.setattr(platform.settings, "RAZORPAY_KEY_ID", "")
    assert platform.client().get("/api/config").json()["online_payments"] is False
    assert platform.client().get(f"/api/doctors/{doc.slug}").json()["accepts_online_payment"] is False
    assert platform.book(platform.patient(), doc, payment_method="online").status_code == 400


# ---------- Admin, exports, reminders ----------
def test_admin_stats_and_csv_export(platform):
    platform.doctor(approve=False)
    doc = platform.doctor()
    pat = platform.patient(name="=HYPERLINK(evil)")
    platform.book(pat, doc)
    admin = platform.admin()
    stats = admin.get("/api/admin/stats").json()
    assert stats["doctors"]["pending"] == 1 and stats["doctors"]["approved"] == 1 and stats["patients"] == 1
    csv_text = admin.get("/api/admin/export.csv").text.splitlines()
    assert csv_text[0].startswith("Sr No,reference") and "'=HYPERLINK(evil)" in csv_text[1]
    assert len(doc.get("/api/doctor/export.csv").text.splitlines()) == 2
    assert [d["status"] for d in admin.get("/api/admin/doctors?status=pending").json()] == ["pending"]


def test_reminders_sent_once_to_patient(platform, monkeypatch):
    doc = platform.doctor()
    pat = platform.patient()
    appt = platform.book(pat, doc).json()["appointment"]
    import send_reminders
    send_reminders = importlib.reload(send_reminders)

    assert send_reminders.send_due_reminders() == 0  # days away
    travel_to(platform, monkeypatch, datetime.fromisoformat(f"{MONDAY}T08:00"))
    platform.outbox.clear()
    assert send_reminders.send_due_reminders() == 1
    assert [m["to"] for m in platform.outbox] == [pat.user["email"]]
    assert platform.outbox[0]["subject"].startswith("Reminder")
    assert send_reminders.send_due_reminders() == 0


def test_emails_escape_user_text(platform):
    doc = platform.doctor()
    pat = platform.patient(name="<script>x</script> Rao")
    platform.book(pat, doc, reason="<b>pain</b>")
    html = platform.mails_to(doc.user["email"])[-1]["html"]
    assert "<script>" not in html and "&lt;script&gt;" in html and "&lt;b&gt;pain" in html


def test_rupee_formatting():
    from shared import mailer
    assert mailer.rupees(800) == "₹800"
    assert mailer.rupees(125000) == "₹1,25,000"
    assert mailer.rupees(10000000) == "₹1,00,00,000"


# ---------- Hosting ----------
def test_legacy_single_clinic_tables_are_kept_aside(tmp_path, monkeypatch, platform):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE appointments (id TEXT PRIMARY KEY, patient_name TEXT, appointment_date TEXT)")
    conn.execute("INSERT INTO appointments VALUES ('old1', 'Old Patient', '2026-10-07')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(platform.settings, "DB_PATH", str(db))
    platform.db.init_db()
    names = {r["name"] for r in rows(platform, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"legacy_appointments_v1", "appointments", "users", "doctors"} <= names
    assert rows(platform, "SELECT patient_name FROM legacy_appointments_v1") == [{"patient_name": "Old Patient"}]


def test_platform_timezone(monkeypatch, platform):
    from zoneinfo import ZoneInfo
    monkeypatch.setenv("PYSHIO_TIMEZONE", "America/Phoenix")
    s = importlib.reload(platform.settings)
    expected = datetime.now(ZoneInfo("America/Phoenix")).replace(tzinfo=None)
    assert abs((s.now() - expected).total_seconds()) < 5


def test_pages_are_served(platform):
    doc = platform.doctor()
    c = platform.client()
    for path in ["/", "/login", "/account", "/doctor", "/admin", "/pharmacy", f"/doctors/{doc.slug}", "/prescriptions/x", "/assets/app.js"]:
        assert c.get(path).status_code == 200, path


def test_passenger_wsgi_entry_point(platform):
    from wsgiref.util import setup_testing_defaults
    import passenger_wsgi
    passenger_wsgi = importlib.reload(passenger_wsgi)
    environ = {}
    setup_testing_defaults(environ)
    environ.update({"REQUEST_METHOD": "GET", "PATH_INFO": "/api/config", "QUERY_STRING": "",
                    "wsgi.input": io.BytesIO(b"")})
    status = []
    body = b"".join(passenger_wsgi.application(environ, lambda s, h, exc=None: status.append(s)))
    assert status[0].startswith("200") and json.loads(body)["platform_name"] == "Pyshio"

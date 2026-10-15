import hashlib
import hmac
import importlib
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RAZORPAY_SECRET = "test_secret"
WEBHOOK_SECRET = "whsec_test"
ADMIN_EMAIL = "admin@pyshio.test"
ADMIN_PASSWORD = "admin-pass-123"


def next_weekday(weekday: int, min_days_ahead: int = 3) -> str:
    """Next date with this weekday (Mon=0), far enough ahead to be refundable."""
    d = date.today() + timedelta(days=min_days_ahead)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d.isoformat()


HOME = (18.5204, 73.8567)  # Shivajinagar, Pune


def north_of_home(km: float):
    """A point `km` kilometres due north of HOME."""
    return round(HOME[0] + km / 111.195, 6), HOME[1]


MONDAY = next_weekday(0)
TUESDAY = next_weekday(1)
SUNDAY = next_weekday(6)


class Platform:
    """The reloaded app modules plus helpers to act as different users."""

    def __init__(self, modules, outbox, razorpay_calls):
        self.__dict__.update(modules)
        self.outbox = outbox
        self.razorpay_calls = razorpay_calls
        self._counter = 0

    def client(self) -> TestClient:
        return TestClient(self.server.app)

    def admin(self) -> TestClient:
        c = self.client()
        assert c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}).status_code == 200
        return c

    def patient(self, name="Asha Rao", email=None, phone="+91 98765 43210"):
        self._counter += 1
        c = self.client()
        email = email or f"patient{self._counter}@example.com"
        res = c.post("/api/auth/register", json={"name": name, "email": email, "phone": phone, "password": "patient-pass", "share_consent": True})
        assert res.status_code == 200, res.text
        c.user = res.json()["user"]
        return c

    def gp(self, name="Dr. Arjun Mehta", **kw):
        """A general physician (can prescribe medicines)."""
        return self.doctor(name=name, category="general_physician", qualification="MBBS",
                           specialities=["Family medicine"], **kw)

    def doctor(self, name="Dr. Meera Iyer", city="Pune", approve=True, fee=800, km_from_home=None, **overrides):
        if km_from_home is not None:
            overrides["lat"], overrides["lng"] = north_of_home(km_from_home)
        self._counter += 1
        c = self.client()
        data = {
            "name": name, "email": f"doctor{self._counter}@example.com", "phone": "98200 12345",
            "password": "doctor-pass", "qualification": "MPT (Ortho)", "registration_no": f"MH-PT-{self._counter:04d}",
            "registration_council": "Maharashtra State OT & PT Council", "experience_years": 8,
            "specialities": ["Orthopaedic", "Sports injury"], "bio": "Knee and shoulder rehab",
            "clinic_name": "Move Well Clinic", "address": "12 FC Road", "city": city, "pincode": "411004",
            "consultation_fee": fee, "accepts_clinic_payment": True, "accepts_online_payment": True,
        }
        data.update(overrides)
        res = c.post("/api/auth/register-doctor", json=data)
        assert res.status_code == 200, res.text
        c.user = res.json()["user"]
        c.slug = res.json()["doctor"]["slug"]
        if approve:
            res = self.admin().post(f"/api/admin/doctors/{c.user['id']}/status", json={"status": "approved"})
            assert res.status_code == 200, res.text
        c.service_id = c.get("/api/doctor/profile").json()["services"][0]["id"]
        return c

    def pharmacy(self, store="Care Pharmacy", km_from_home=0.5, approve=True, **overrides):
        self._counter += 1
        c = self.client()
        lat, lng = north_of_home(km_from_home)
        data = {
            "name": "Ravi Shah", "email": f"pharmacy{self._counter}@example.com", "phone": "98220 55555",
            "password": "pharmacy-pass", "store_name": store, "drug_licence_no": f"MH-PUN-{self._counter}/20B/21B",
            "pharmacist_name": "Ravi Shah", "pharmacist_reg_no": f"PH-{self._counter:04d}", "address": "5 JM Road",
            "city": "Pune", "pincode": "411005", "lat": lat, "lng": lng, "offers_delivery": True,
            "delivery_radius_km": 3, "delivery_fee": 30, "accepts_online_payment": True,
        }
        data.update(overrides)
        res = c.post("/api/auth/register-pharmacy", json=data)
        assert res.status_code == 200, res.text
        c.user = res.json()["user"]
        if approve:
            res = self.admin().post(f"/api/admin/pharmacies/{c.user['id']}/status", json={"status": "approved"})
            assert res.status_code == 200, res.text
        return c

    def set_home(self, patient, km_from_home=0.0):
        lat, lng = north_of_home(km_from_home)
        res = patient.put("/api/my/location", json={"lat": lat, "lng": lng, "address": "Flat 4, Shivajinagar, Pune"})
        assert res.status_code == 200, res.text

    def stock(self, pharmacy, medicine_name, price, mrp=None, in_stock=True):
        med = self.medicine(pharmacy, medicine_name)
        res = pharmacy.put("/api/pharmacy/stock", json={"medicine_id": med["id"], "mrp": mrp or price, "price": price,
                                                        "in_stock": in_stock})
        assert res.status_code == 200, res.text

    def medicine(self, client, name):
        found = [m for m in client.get(f"/api/medicines?q={name}").json() if m["name"] == name]
        assert found, name
        return found[0]

    def book(self, patient, doctor, day=None, time="10:00", **extra):
        body = {"doctor_slug": doctor.slug, "service_id": doctor.service_id,
                "appointment_date": day or MONDAY, "appointment_time": time, **extra}
        return patient.post("/api/appointments", json=body)

    def sign(self, order_id, payment_id):
        return hmac.new(RAZORPAY_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()

    def mails_to(self, address):
        return [m for m in self.outbox if m["to"] == address]

    def company(self, name="Sunrise Pharma", approve=True, divisions=(("Cardio-Diabetes", ["Cardiology", "Diabetes & Endocrine"]),)):
        """A pharma company admin; `c.divisions` maps division name -> id."""
        self._counter += 1
        c = self.client()
        res = c.post("/api/auth/register-company", json={
            "name": "Neha Kulkarni", "email": f"company{self._counter}@example.com", "phone": "98200 11111",
            "password": "company-pass", "company_name": name, "cin": f"U24230MH2010PTC{self._counter:06d}",
            "address": "Andheri East", "city": "Pune"})
        assert res.status_code == 200, res.text
        c.user = res.json()["user"]
        c.company = res.json()["company"]
        if approve:
            res = self.admin().post(f"/api/admin/companies/{c.company['id']}/status", json={"status": "approved"})
            assert res.status_code == 200, res.text
        c.divisions = {}
        for div_name, areas in divisions:
            res = c.post("/api/company/divisions", json={"name": div_name, "care_areas": areas})
            assert res.status_code == 200, res.text
            c.divisions[div_name] = res.json()["id"]
        return c

    def lab(self, name="City Diagnostics", km_from_home=0.6, approve=True, **overrides):
        self._counter += 1
        c = self.client()
        lat, lng = north_of_home(km_from_home)
        data = {"name": "Kiran Joshi", "email": f"lab{self._counter}@example.com", "phone": "98220 66666",
                "password": "lab-pass-123", "lab_name": name, "registration_no": f"MH-CEA-{self._counter:04d}",
                "nabl_no": f"MC-{self._counter}", "pathologist_name": "Dr. Kiran Joshi", "pathologist_reg_no": f"MMC-{self._counter}",
                "address": "8 FC Road", "city": "Pune", "pincode": "411004", "lat": lat, "lng": lng,
                "home_collection": True, "collection_fee": 50, "collection_radius_km": 5, "report_hours": 24}
        data.update(overrides)
        res = c.post("/api/auth/register-lab", json=data)
        assert res.status_code == 200, res.text
        c.user = res.json()["user"]
        if approve:
            res = self.admin().post(f"/api/admin/labs/{c.user['id']}/status", json={"status": "approved"})
            assert res.status_code == 200, res.text
        return c

    def lab_price(self, lab, test_name, price, mrp=None, report_hours=None):
        test = [t for t in lab.get(f"/api/lab-tests?q={test_name}").json() if t["name"] == test_name][0]
        res = lab.put("/api/lab/prices", json={"test_id": test["id"], "mrp": mrp or price, "price": price,
                                               "report_hours": report_hours})
        assert res.status_code == 200, res.text
        return test

    def mr(self, company, division=None, state="Maharashtra", city="Pune", name="Rahul Desai"):
        """An MR invited by `company`, who set a password from the invite email and logged in."""
        self._counter += 1
        email = f"mr{self._counter}@example.com"
        division_id = company.divisions[division] if division else next(iter(company.divisions.values()), None)
        res = company.post("/api/company/mrs", json={"name": name, "email": email, "phone": "98200 22222",
                                                     "division_id": division_id, "territory_state": state,
                                                     "territory_city": city, "employee_code": f"E{self._counter}"})
        assert res.status_code == 200, res.text
        invite = self.mails_to(email)[-1]
        token = invite["text"].split("#reset=")[1].split()[0]
        c = self.client()
        assert c.post("/api/auth/reset", json={"token": token, "password": "mr-pass-123"}).status_code == 200
        assert c.post("/api/auth/login", json={"email": email, "password": "mr-pass-123"}).status_code == 200
        c.user = res.json()
        return c


APP_PACKAGES = ("shared", "features", "server", "jobs", "send_reminders", "seed_demo", "passenger_wsgi")
# Slice modules tests reach into directly, as platform.<name>
SLICE_MODULES = {"assistant": "features.assistant.logic", "prescriptions": "features.prescriptions.logic",
                 "medicine_orders": "features.medicine_orders.logic", "notifications": "features.notifications.logic"}


def fresh_app() -> dict:
    """Forget every app module and import the app again, so settings are re-read from this test's environment."""
    for name in list(sys.modules):
        if name.split(".")[0] in APP_PACKAGES:
            del sys.modules[name]
    modules = {"server": importlib.import_module("server")}
    for name in ("settings", "db", "auth", "mailer", "payments", "ai", "salts"):
        modules[name] = importlib.import_module(f"shared.{name}")
    for name, path in SLICE_MODULES.items():
        modules[name] = importlib.import_module(path)
    return modules


@pytest.fixture
def platform(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSHIO_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("PYSHIO_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("PYSHIO_ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("PYSHIO_ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("PYSHIO_SITE_URL", "http://pyshio.test")
    monkeypatch.setenv("PYSHIO_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", RAZORPAY_SECRET)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PYSHIO_TIMEZONE", raising=False)

    modules = fresh_app()
    # Fast password hashing in tests (the hash records its own iteration count)
    monkeypatch.setattr(modules["auth"], "PBKDF2_ITERATIONS", 1000)

    outbox = []

    def fake_send(to, subject, text, html=None, ics=None, reply_to=None):
        outbox.append({"to": to, "subject": subject, "text": text, "html": html, "ics": ics, "reply_to": reply_to})
        return True

    monkeypatch.setattr(modules["mailer"], "_deliver", fake_send)

    calls = []

    def fake_razorpay(method, path, payload=None):
        calls.append((method, path, payload))
        if path == "/orders":
            return {"id": f"order_{len(calls)}", "amount": payload["amount"]}
        if path.endswith("/refund"):
            return {"id": f"rfnd_{len(calls)}"}
        raise AssertionError(path)

    monkeypatch.setattr(modules["payments"], "_request", fake_razorpay)
    return Platform(modules, outbox, calls)


class FakeClaude:
    """Stands in for the Claude API: answers from a scripted queue and records every payload sent."""

    def __init__(self):
        self.queue, self.payloads, self.down = [], [], False

    def __call__(self, payload, timeout=30):
        self.payloads.append(payload)
        if self.down:
            import urllib.error
            raise urllib.error.URLError("down")
        if not self.queue:
            raise AssertionError("Claude was called but no answer was scripted")
        answer = self.queue.pop(0)
        return answer(payload) if callable(answer) else answer

    def reply(self, text):
        self.queue.append({"content": [{"type": "text", "text": text}], "usage": {"input_tokens": 10, "output_tokens": 5}})

    def emit(self, data):
        """Answer a forced `emit` tool call (structured output)."""
        self.queue.append({"content": [{"type": "tool_use", "id": "toolu_1", "name": "emit", "input": data}],
                           "usage": {"input_tokens": 10, "output_tokens": 5}})

    def tool(self, name, args=None):
        self.queue.append({"content": [{"type": "tool_use", "id": f"toolu_{len(self.queue)}", "name": name,
                                        "input": args or {}}]})

    def fail(self):
        self.down = True

    def sent_text(self) -> str:
        import json
        return json.dumps(self.payloads)


@pytest.fixture
def fake_claude(platform, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake = FakeClaude()
    monkeypatch.setattr(platform.ai, "_post", fake)
    return fake


def assert_no_pii(fake, *values):
    sent = fake.sent_text().lower()
    for v in values:
        if v:
            assert str(v).lower() not in sent, f"{v!r} was sent to Claude"

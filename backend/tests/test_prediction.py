"""Pharmacy predictions: course tracking, refill reminders, pre-authorised refills, stock demand forecast."""
from datetime import timedelta

import pytest

from test_network import world  # noqa: F401  (fixture)
from test_phase2 import complete_order, jobs_module, travel


def condition(client, code):
    return [c for c in client.get("/api/conditions").json() if c["code"] == code][0]


def long_term_rx(platform, w, refills=2):
    telma = platform.medicine(w["gp"], "Telma 40")
    dolo = platform.medicine(w["gp"], "Dolo 650")
    res = w["gp"].post(f"/api/appointments/{w['appt']['id']}/prescriptions", json={
        "patient_age": 58, "patient_sex": "M", "condition_ids": [condition(w["gp"], "I10")["id"]],
        "refills_allowed": refills, "items": [
            {"medicine_id": telma["id"], "dosage": "1-0-0", "quantity": 30, "is_long_term": True},
            {"medicine_id": dolo["id"], "dosage": "1-0-1", "duration": "5 days", "quantity": 10}]})
    assert res.status_code == 200, res.text
    return res.json()


def relogin(platform, w):
    """Sessions last 30 days, so after a long time-jump everyone signs in again."""
    for key, password in (("patient", "patient-pass"), ("gp", "doctor-pass"), ("near", "pharmacy-pass"),
                          ("mid", "pharmacy-pass")):
        res = w[key].post("/api/auth/login", json={"email": w[key].user["email"], "password": password})
        assert res.status_code == 200, res.text


def order_and_complete(platform, w, rx_id, pharmacy="near"):
    res = w["patient"].post(f"/api/prescriptions/{rx_id}/orders", json={"pharmacy_id": w[pharmacy].user["id"]})
    assert res.status_code == 200, res.text
    assert complete_order(platform, w, res.json()["order"]["id"], pharmacy).status_code == 200
    return res.json()["order"]


def test_dosage_and_duration_shorthand(platform):
    n = platform.prescriptions
    assert n.daily_units_from("1-0-1") == 2 and n.daily_units_from("1-1-1") == 3
    assert n.daily_units_from("½-0-½") == 1 and n.daily_units_from("1/2-0-1/2") == 1
    assert n.daily_units_from("BD") == 2 and n.daily_units_from("SOS") is None
    assert n.days_from("5 days") == 5 and n.days_from("2 weeks") == 14 and n.days_from("3 months") == 90
    assert n.days_from("till review") is None


def test_refills_need_a_long_term_medicine(platform, world):
    dolo = platform.medicine(world["gp"], "Dolo 650")
    res = world["gp"].post(f"/api/appointments/{world['appt']['id']}/prescriptions", json={
        "refills_allowed": 1, "items": [{"medicine_id": dolo["id"], "quantity": 10}]})
    assert res.status_code == 422 and "long-term" in res.json()["detail"]
    bad = world["gp"].post(f"/api/appointments/{world['appt']['id']}/prescriptions", json={
        "condition_ids": [999999], "items": [{"medicine_id": dolo["id"], "quantity": 10}]})
    assert bad.status_code == 400


def test_courses_reminders_and_refills(platform, world, monkeypatch):
    w = world
    platform.stock(w["near"], "Telma 40", price=8)
    platform.stock(w["near"], "Dolo 650", price=15)
    rx = long_term_rx(platform, w)
    assert [c["code"] for c in rx["conditions"]] == ["I10"]
    telma_item = [i for i in rx["items"] if i["name"] == "Telma 40"][0]
    assert telma_item["is_long_term"] and telma_item["daily_units"] == 1
    assert rx["refills"]["allowed"] == 2 and rx["refills"]["left"] == 2 and not rx["refills"]["can_refill"]
    # Too early: the next refill opens a week before the 30-day supply runs out
    early = w["patient"].post(f"/api/prescriptions/{rx['id']}/refill")
    assert early.status_code == 400 and "opens on" in early.json()["detail"]

    order_and_complete(platform, w, rx["id"])
    courses = {c["name"]: c for c in w["near"].get("/api/pharmacy/courses").json()["courses"]}
    assert courses["Telma 40"]["status"] == "active" and courses["Telma 40"]["is_long_term"]
    assert courses["Dolo 650"]["status"] == "ending"  # 10 tablets at 2 a day: 5 days
    assert courses["Telma 40"]["refills_left"] == 2 and courses["Telma 40"]["patient_name"] == "Asha Rao"
    assert w["mid"].get("/api/pharmacy/courses").json()["courses"] == []  # only the pharmacy's own customers
    mine = {c["name"]: c for c in w["patient"].get("/api/my/courses").json()}
    assert "patient_name" not in mine["Telma 40"]

    # Day 28: refill reminder goes out once
    jobs = jobs_module()
    travel(platform, monkeypatch, timedelta(days=28))
    platform.outbox.clear()
    assert jobs.remind_refills() == 1
    assert any("Refill due" in m["subject"] for m in platform.mails_to(w["patient"].user["email"]))
    assert jobs.remind_refills() == 0

    # Day 35: the course has run out without a refill
    travel(platform, monkeypatch, timedelta(days=35))
    relogin(platform, w)
    courses = {c["name"]: c for c in w["near"].get("/api/pharmacy/courses").json()["courses"]}
    assert courses["Telma 40"]["status"] == "overdue" and courses["Dolo 650"]["status"] == "completed"
    assert w["near"].get("/api/pharmacy/courses?status=overdue").json()["counts"]["overdue"] == 1

    platform.outbox.clear()
    res = w["patient"].post(f"/api/prescriptions/{rx['id']}/refill")
    assert res.status_code == 200, res.text
    refill = res.json()
    assert refill["refill_number"] == 1 and refill["refill_of"] == rx["reference"]
    assert [i["name"] for i in refill["items"]] == ["Telma 40"]  # only the long-term medicine
    assert [c["code"] for c in refill["conditions"]] == ["I10"]
    assert refill["pharmacies_notified"] == 2  # near & mid are within 3 km of home
    assert any("Refill used" in m["subject"] for m in platform.mails_to(w["gp"].user["email"]))
    again = w["patient"].get(f"/api/prescriptions/{rx['id']}").json()["refills"]
    assert again["used"] == 1 and again["left"] == 1 and not again["can_refill"]
    assert w["patient"].post(f"/api/prescriptions/{refill['id']}/refill").status_code == 400  # too soon again
    # The doctor sees it, and another patient can't use it
    assert w["gp"].get("/api/doctor/requests").json()["refills"][0]["refill_of"] == rx["reference"]
    assert platform.patient(name="Other").post(f"/api/prescriptions/{rx['id']}/refill").status_code == 404

    # Once the refill is dispensed, the old course counts as refilled
    order_and_complete(platform, w, refill["id"])
    courses = w["near"].get("/api/pharmacy/courses").json()["courses"]
    statuses = sorted((c["name"], c["status"]) for c in courses if c["name"] == "Telma 40")
    assert statuses == [("Telma 40", "active"), ("Telma 40", "refilled")]


def test_stock_forecast(platform, world):
    w = world
    platform.stock(w["near"], "Dolo 650", price=15)
    platform.stock(w["near"], "Telma 40", price=8, in_stock=False)
    rx = long_term_rx(platform, w)

    week = w["near"].get("/api/pharmacy/forecast?days=7").json()
    items = {i["generic_name"]: i for i in week["items"]}
    assert items["Telmisartan"]["expected_units"] == 8  # 30 units over 28 days -> 7.5 a week, rounded up
    assert items["Telmisartan"]["stock_status"] == "out_of_stock" and items["Telmisartan"]["stock_up"]
    assert items["Paracetamol"]["expected_units"] == 3 and items["Paracetamol"]["stock_status"] == "in_stock"
    assert not items["Paracetamol"]["stock_up"] and week["stock_up"] == 1
    assert sum(x["units"] for x in week["weekly_units"]) == 40
    assert w["far"].get("/api/pharmacy/forecast").json()["items"] == []  # 5 km away: not its area

    # A long-term course running out inside the window adds its refill to the demand
    platform.stock(w["near"], "Telma 40", price=8)
    order_and_complete(platform, w, rx["id"])
    month = w["near"].get("/api/pharmacy/forecast?days=30").json()
    items = {i["generic_name"]: i for i in month["items"]}
    assert items["Telmisartan"]["refill_units"] == 30
    assert items["Telmisartan"]["expected_units"] == 63  # ceil(30 / 28 * 30) + 30
    assert w["patient"].get("/api/pharmacy/forecast").status_code == 403


@pytest.mark.parametrize("days", [0, 91])
def test_forecast_window_is_bounded(platform, world, days):
    assert world["near"].get(f"/api/pharmacy/forecast?days={days}").status_code == 422


# ---------- Forecast v2: trend, weekday pattern, safety stock, reorder ----------
def test_smart_estimate_follows_trend_and_weekdays(platform):
    from datetime import date
    from features.medicine_courses.logic import smart_estimate, weekday_factors
    flat = [1.0] * 7
    rising = smart_estimate([0, 0, 2, 4, 6, 8, 10, 12], 7, date(2026, 1, 5), flat)
    steady = smart_estimate([7] * 8, 7, date(2026, 1, 5), flat)
    assert rising["forecast"] > 10 and rising["trend_pct"] > 0
    assert round(steady["forecast"]) == 7 and steady["safety"] == 0 and steady["confidence"] == "good"
    assert smart_estimate([0, 0, 0, 0, 0, 0, 0, 3], 7, date(2026, 1, 5), flat)["confidence"] == "low"
    factors = weekday_factors([2, 2, 2, 2, 2, 30, 2])
    assert max(range(7), key=lambda d: factors[d]) == 5 and abs(sum(factors) - 7) < 1e-9
    assert weekday_factors([0] * 7) == [1.0] * 7


def test_forecast_reorder_uses_on_hand(platform, world):
    w = world
    platform.stock(w["near"], "Telma 40", price=8, in_stock=False)
    long_term_rx(platform, w)
    item = {i["generic_name"]: i for i in w["near"].get("/api/pharmacy/forecast?days=7").json()["items"]}["Telmisartan"]
    assert item["forecast_units"] >= 1 and item["reorder_units"] >= item["forecast_units"] and item["reorder_packs"] >= 1
    telma = platform.medicine(w["near"], "Telma 40")
    assert w["near"].put(f"/api/pharmacy/stock/{telma['id']}/on-hand", json={"on_hand": 500}).json()["on_hand"] == 500
    item = {i["generic_name"]: i for i in w["near"].get("/api/pharmacy/forecast?days=7").json()["items"]}["Telmisartan"]
    assert item["on_hand"] == 500 and item["reorder_units"] == 0 and item["reorder_packs"] == 0
    dolo = platform.medicine(w["near"], "Dolo 650")
    assert w["near"].put(f"/api/pharmacy/stock/{dolo['id']}/on-hand", json={"on_hand": 5}).status_code == 404  # not listed

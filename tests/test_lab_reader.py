"""Lab report reader (values suggested from the file, flags recomputed) and the plain-language explainer, plus trends."""
import json

from conftest import assert_no_pii
from test_labs import PDF, lab_world  # noqa: F401  (fixture)
from test_network import world  # noqa: F401  (fixture)


def collected_booking(w):
    res = w["patient"].post(f"/api/prescriptions/{w['rx']['id']}/lab-bookings",
                            json={"lab_id": w["near_lab"].user["id"], "collection": "visit"})
    assert res.status_code == 200, res.text
    booking = res.json()
    for s in ("accepted", "sample_collected"):
        assert w["near_lab"].post(f"/api/lab/bookings/{booking['id']}/status", json={"status": s}).status_code == 200
    return booking


def read(w, booking):
    return w["near_lab"].post(f"/api/lab/bookings/{booking['id']}/report/read", files={"file": ("r.pdf", PDF)})


def submit(w, booking, values):
    res = w["near_lab"].post(f"/api/lab/bookings/{booking['id']}/report", files={"file": ("r.pdf", PDF)},
                             data={"values": json.dumps(values)})
    assert res.status_code == 200, res.text
    return res.json()["record_id"]


def test_builtin_prefills_booked_tests(platform, lab_world):
    w = lab_world
    booking = collected_booking(w)
    res = read(w, booking)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["engine"] == "built-in"
    names = {v["name"]: v for v in out["values"]}
    assert names["HbA1c (glycated haemoglobin)"]["unit"] == "%" and names["HbA1c (glycated haemoglobin)"]["value"] == ""


def test_claude_values_are_matched_and_flags_recomputed(platform, lab_world, fake_claude):
    w = lab_world
    booking = collected_booking(w)
    fake_claude.emit({"values": [
        {"name": "Glycated Haemoglobin", "value": "7.8", "unit": "%"},                 # catalogue range 4.0-5.6 -> high
        {"name": "FBS", "value": "6.1", "unit": "mmol/L"},                              # wrong unit -> check, no flag
        {"name": "Mystery marker", "value": "12", "unit": "U", "ref_low": 0, "ref_high": 10}]})
    out = read(w, booking).json()
    assert out["engine"] == "claude"
    hba1c, fbs, other = out["values"]
    assert hba1c["test_id"] == w["hba1c"]["id"] and hba1c["flag"] == "high"
    assert fbs["check_unit"] and fbs["flag"] is None
    assert other["test_id"] is None and other["flag"] == "high"
    assert fake_claude.payloads[0]["messages"][0]["content"][0]["type"] == "document"


def test_other_labs_cannot_read(platform, lab_world):
    w = lab_world
    booking = collected_booking(w)
    assert w["fast_lab"].post(f"/api/lab/bookings/{booking['id']}/report/read", files={"file": ("r.pdf", PDF)}).status_code == 404


def test_trend_direction_and_explainer(platform, lab_world, monkeypatch):
    w = lab_world
    booking = collected_booking(w)
    first = submit(w, booking, [{"test_id": w["hba1c"]["id"], "name": "HbA1c", "value": "8.4"}])
    with platform.db.get_db() as conn:  # an older and a middle report for the trend
        conn.execute("UPDATE health_records SET record_date = date('now', '-120 days') WHERE id = ?", (first,))
    second = submit(w, booking, [{"test_id": w["hba1c"]["id"], "name": "HbA1c (glycated haemoglobin)", "value": "7.1"}])
    with platform.db.get_db() as conn:
        conn.execute("UPDATE health_records SET deleted_at = NULL WHERE id = ?", (first,))  # keep both (a correction hid it)
    trends = w["patient"].get("/api/my/trends").json()
    hba1c = [t for t in trends if t["test_id"] == w["hba1c"]["id"]][0]
    assert len(hba1c["points"]) == 2  # one series even though the lab named it differently
    assert hba1c["direction"] == "down" and hba1c["toward_normal"] is True and hba1c["slope_per_month"] < 0

    ex = w["patient"].get(f"/api/records/{second}/explain").json()
    assert ex["engine"] == "built-in" and ex["abnormal"] == 1
    assert "average blood sugar" in ex["lines"][0]["text"] and "toward the usual range" in ex["lines"][0]["text"]
    assert "not a diagnosis" in ex["disclaimer"]
    hi = w["patient"].get(f"/api/records/{second}/explain?lang=hi").json()
    assert "mahine" in hi["lines"][0]["text"]
    # The doctor may see it too; another patient may not
    assert w["gp"].get(f"/api/records/{second}/explain").status_code == 200
    assert platform.patient(name="Other").get(f"/api/records/{second}/explain").status_code == 404


def test_explainer_with_claude_and_dosage_filter(platform, lab_world, fake_claude):
    w = lab_world
    booking = collected_booking(w)
    rec = submit(w, booking, [{"test_id": w["hba1c"]["id"], "name": "HbA1c", "value": "7.4"}])
    fake_claude.reply("Your HbA1c is above the usual range, so your sugar has been high lately. Please discuss it with your doctor.")
    ex = w["patient"].get(f"/api/records/{rec}/explain").json()
    assert ex["engine"] == "claude" and ex["summary"].startswith("Your HbA1c") and ex["disclaimer"]
    assert_no_pii(fake_claude, "Asha", w["patient"].user["email"])
    fake_claude.reply("Take metformin 500 mg twice daily.")   # never passed on
    ex = w["patient"].get(f"/api/records/{rec}/explain").json()
    assert ex["engine"] == "built-in" and ex["summary"] is None

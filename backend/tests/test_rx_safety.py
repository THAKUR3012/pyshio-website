"""Prescription safety engine: interactions, duplicate salts, current medicines, allergies, age, pregnancy, dose."""
from test_network import prescribe, world  # noqa: F401  (fixture)


def check(w, items, **extra):
    res = w["gp"].post("/api/doctor/rx-check", json={"appointment_id": w["appt"]["id"], "items": items, **extra})
    assert res.status_code == 200, res.text
    return res.json()


def kinds(result, severity=None):
    return {a["kind"] for a in result["alerts"] if severity is None or a["severity"] == severity}


def issue(w, items, **extra):
    return w["gp"].post(f"/api/appointments/{w['appt']['id']}/prescriptions",
                        json={"patient_age": 40, "items": items, **extra})


WARFARIN = {"name": "Warf 5", "generic_name": "Warfarin", "strength": "5 mg", "dosage": "0-0-1", "quantity": 30}


def test_salt_normalisation(platform):
    from shared import salts
    assert salts.salt_key("Amoxycillin + Clavulanic acid") == "amoxicillin+clavulanate"
    assert salts.salt_key("Paracetamol IP") == salts.salt_key("paracetamol") == "paracetamol"
    assert salts.salt_key("Sulfamethoxazole + Trimethoprim") == "cotrimoxazole"
    assert salts.strength_key("500 mg") == salts.strength_key("500mg") == "500mg"
    assert salts.strength_mg("1 g") == 1000 and salts.strength_mg("50 mcg") == 0.05


def test_duplicate_salt_and_combo(platform, world):
    dolo, calpol = platform.medicine(world["gp"], "Dolo 650"), platform.medicine(world["gp"], "Calpol 650")
    res = check(world, [{"medicine_id": dolo["id"]}, {"medicine_id": calpol["id"]}])
    assert "duplicate" in kinds(res, "major")
    augmentin = platform.medicine(world["gp"], "Augmentin 625 Duo")
    res = check(world, [{"medicine_id": augmentin["id"]}, {"name": "Mox 500", "generic_name": "Amoxicillin", "strength": "500 mg"}])
    assert "duplicate" in kinds(res, "major")


def test_major_interaction_blocks_until_acknowledged(platform, world):
    brufen = platform.medicine(world["gp"], "Brufen 400")
    items = [WARFARIN, {"medicine_id": brufen["id"], "dosage": "1-0-1", "quantity": 10}]
    res = issue(world, items)
    assert res.status_code == 409
    alerts = res.json()["detail"]["alerts"]
    major = [a for a in alerts if a["severity"] == "major"]
    assert any("Bleeding" in a["message"] for a in major)
    ok = issue(world, items, acknowledged_alerts=[a["code"] for a in major])
    assert ok.status_code == 200, ok.text
    assert ok.json()["safety_alerts"]
    with platform.db.get_db() as conn:
        row = conn.execute("SELECT safety_alerts, safety_ack_at FROM prescriptions WHERE id = ?", (ok.json()["id"],)).fetchone()
    assert "Bleeding" in row["safety_alerts"] and row["safety_ack_at"]


def test_clean_prescription_is_not_blocked(platform, world):
    rx = prescribe(platform, world)
    assert rx["safety_alerts"] == [] or all(a["severity"] != "major" for a in rx["safety_alerts"])


def test_interaction_with_current_medicines(platform, world):
    patient = world["patient"]
    assert patient.post("/api/my/medicines", json={"name": "Warf 5", "dosage": "0-0-1"}).status_code == 200
    with platform.db.get_db() as conn:  # the patient recorded the salt too
        conn.execute("UPDATE patient_medicines SET generic_name = 'Warfarin'")
    hifenac = platform.medicine(world["gp"], "Hifenac 100")
    res = check(world, [{"medicine_id": hifenac["id"]}])
    assert "interaction" in kinds(res, "major")
    assert any(m["name"] == "Warf 5" for m in res["current_medicines"])


def test_age_pregnancy_and_dose(platform, world):
    doxy = platform.medicine(world["gp"], "Doxy-1 100")
    assert "age" in kinds(check(world, [{"medicine_id": doxy["id"]}], patient_age=6), "major")
    assert "age" not in kinds(check(world, [{"medicine_id": doxy["id"]}], patient_age=30))
    atorva = platform.medicine(world["gp"], "Atorva 10")
    assert "pregnancy" in kinds(check(world, [{"medicine_id": atorva["id"]}], pregnancy="pregnant"), "major")
    with platform.db.get_db() as conn:
        z34 = conn.execute("SELECT id FROM conditions WHERE code = 'Z34.9'").fetchone()
    if z34:  # pregnancy implied by the diagnosis
        assert "pregnancy" in kinds(check(world, [{"medicine_id": atorva["id"]}], condition_ids=[z34["id"]]))
    dolo = platform.medicine(world["gp"], "Dolo 650")
    assert "dose" in kinds(check(world, [{"medicine_id": dolo["id"], "dosage": "2-2-2-2"}]), "major")  # 5.2 g a day
    assert "dose" not in kinds(check(world, [{"medicine_id": dolo["id"], "dosage": "1-1-1"}]))


def test_allergy_is_major_and_unknown_is_info(platform, world):
    world["patient"].put("/api/my/health-profile", json={"allergies": "Penicillin"})
    augmentin = platform.medicine(world["gp"], "Augmentin 625 Duo")
    res = check(world, [{"medicine_id": augmentin["id"]}, {"name": "Herbal Kadha"}])
    assert "allergy" in kinds(res, "major")
    assert "unchecked" in kinds(res, "info")


def test_only_the_treating_doctor_can_check(platform, world):
    stranger = platform.gp(name="Dr. Stranger")
    res = stranger.post("/api/doctor/rx-check", json={"appointment_id": world["appt"]["id"], "items": []})
    assert res.status_code == 404
    res = stranger.post("/api/doctor/rx-check", json={"patient_id": world["patient"].user["id"], "items": []})
    assert res.status_code == 404
    assert world["patient"].post("/api/doctor/rx-check", json={"items": []}).status_code in (401, 403)


def test_substitutes_use_normalised_salts(platform, world):
    with platform.db.get_db() as conn:
        conn.execute("INSERT INTO medicines (name, generic_name, strength, form, pack_size, created_at, salt_key, strength_key) "
                     "VALUES ('Paramol 650', 'Paracetamol IP', '650mg', 'tablet', 15, datetime('now'), 'paracetamol', '650mg')")
        new_id = conn.execute("SELECT id FROM medicines WHERE name = 'Paramol 650'").fetchone()["id"]
        dolo_id = conn.execute("SELECT id FROM medicines WHERE name = 'Dolo 650'").fetchone()["id"]
        from features.pharmacy_offers.logic import substitutes
        ids = substitutes(conn, {"medicine_id": dolo_id, "allow_substitute": 1})
    assert new_id in ids and dolo_id in ids

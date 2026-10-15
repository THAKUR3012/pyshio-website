"""Reading uploaded prescription photos: Claude vision (with consent), typed names, pre-filled partner quotes."""
from conftest import assert_no_pii
from test_network import world  # noqa: F401  (fixture)
from test_uploads import PNG

READ = {"legible": True, "tests": ["CBC"],
        "medicines": [{"name": "Dolo 650", "dosage": "1-0-1", "duration": "5 days"},
                      {"name": "Augmentin 625", "dosage": "1-0-1", "duration": "5 days"}]}


def upload(patient, consent=True, typed=None, tests=True):
    data = {"want_medicines": "true", "want_tests": str(tests).lower(), "ai_read_consent": str(consent).lower()}
    if typed:
        data["typed_lines"] = typed
    res = patient.post("/api/my/uploads", files={"file": ("rx.png", PNG)}, data=data)
    assert res.status_code == 200, res.text
    return res.json()


def test_photo_read_with_consent_prefills_pharmacy_prices(platform, world, fake_claude):
    platform.stock(world["near"], "Dolo 650", price=27, mrp=30)       # strip of 15
    platform.lab(name="Read Labs", km_from_home=1)
    fake_claude.emit(READ)
    up = upload(world["patient"])
    assert len(fake_claude.payloads) == 1
    image = fake_claude.payloads[0]["messages"][0]["content"][0]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/png"
    assert_no_pii(fake_claude, world["patient"].user["email"], "Asha")

    req = world["near"].get("/api/partner/uploads").json()[0]
    s = world["near"].get(f"/api/partner/uploads/{req['id']}/suggested-lines").json()
    assert s["status"] == "done" and s["engine"] == "claude"
    dolo, aug = s["lines"]
    assert dolo["medicine_id"] and dolo["available"] and dolo["unit_price"] == 1.8 and dolo["quantity"] == 10
    assert aug["name"].startswith("Augmentin") and not aug["available"]   # not on this pharmacy's list
    # The pharmacy quotes (it confirms each line); the catalogue link is kept
    res = world["near"].post(f"/api/partner/uploads/{req['id']}/quote", json={"lines": [
        {"name": dolo["name"], "medicine_id": dolo["medicine_id"], "quantity": 10, "unit_price": 1.8, "mrp": 2},
        {"name": aug["name"], "medicine_id": aug["medicine_id"], "quantity": 10, "available": False}]})
    assert res.status_code == 200, res.text
    with platform.db.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM upload_quote_lines WHERE medicine_id IS NOT NULL").fetchone()[0] == 2

    # The patient sees what we read
    read = world["patient"].get(f"/api/my/uploads/{up['id']}").json()["read"]
    assert read["status"] == "done" and [m["name"] for m in read["medicines"]][0] == "Dolo 650"
    assert read["tests"] == ["Complete Blood Count (CBC)"]


def test_patient_allergy_warning_on_read(platform, world, fake_claude):
    world["patient"].put("/api/my/health-profile", json={"allergies": "penicillin"})
    fake_claude.emit(READ)
    up = upload(world["patient"])
    read = world["patient"].get(f"/api/my/uploads/{up['id']}").json()["read"]
    assert any("allergic" in w for w in read["warnings"])


def test_no_consent_means_no_ai_call(platform, world, fake_claude):
    up = upload(world["patient"], consent=False, typed="Dolo 650 1-0-1 x 5 days\nCBC")
    assert fake_claude.payloads == []
    read = world["patient"].get(f"/api/my/uploads/{up['id']}").json()["read"]
    assert read["engine"] == "typed" and read["medicines"][0]["name"] == "Dolo 650" and read["tests"]


def test_nothing_to_read_is_skipped_and_partners_quote_as_before(platform, world):
    up = upload(world["patient"], consent=True)       # consent, but no API key: nothing is read
    req = world["near"].get("/api/partner/uploads").json()[0]
    s = world["near"].get(f"/api/partner/uploads/{req['id']}/suggested-lines").json()
    assert s == {"status": "skipped", "lines": []}
    assert world["patient"].get(f"/api/my/uploads/{up['id']}").json()["read"] == {"status": "skipped"}


def test_claude_failure_is_retried_by_cron(platform, world, fake_claude):
    fake_claude.fail()
    up = upload(world["patient"], typed=None)
    with platform.db.get_db() as conn:
        row = conn.execute("SELECT status, attempts FROM ai_extractions WHERE source_id = ?", (up["id"],)).fetchone()
    assert row["status"] == "skipped"            # vision down and nothing typed: partners read it themselves
    fake_claude.down = False
    # A read left half-way (e.g. the worker stopped) is picked up by the cron job, once
    with platform.db.get_db() as conn:
        conn.execute("UPDATE ai_extractions SET status = 'running', claimed_at = '2000-01-01T00:00:00', attempts = 1")
    fake_claude.emit(READ)
    import jobs
    assert jobs.read_pending_uploads() == 1 and jobs.read_pending_uploads() == 0


def test_partners_only_see_their_own_suggestions(platform, world):
    upload(world["patient"], consent=False, typed="Dolo 650")
    req = world["near"].get("/api/partner/uploads").json()[0]
    assert world["mid"].get(f"/api/partner/uploads/{req['id']}/suggested-lines").status_code == 404
    assert world["patient"].get(f"/api/partner/uploads/{req['id']}/suggested-lines").status_code in (401, 403)

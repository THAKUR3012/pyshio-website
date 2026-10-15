"""Patient health records: own uploads, sharing with doctors, other medicines, export and account closure."""
import json
from pathlib import Path

from test_network import world  # noqa: F401  (fixture)

PDF = b"%PDF-1.4\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


def upload(client, content=PDF, name="old-report.pdf", **form):
    data = {"title": "Old blood report", "kind": "other_report", "record_date": "2025-03-01", **form}
    return client.post("/api/my/records", files={"file": (name, content)}, data=data)


def test_patient_uploads_and_shares_with_their_doctors(platform, world, monkeypatch):
    patient, gp = world["patient"], world["gp"]
    res = upload(patient)
    assert res.status_code == 200, res.text
    rec = res.json()
    assert rec["has_file"] and rec["share_with_doctors"] and rec["file_name"] == "old-report.pdf"
    photo = upload(patient, PNG, "bill.png", title="Pharmacy bill", kind="medicine_bill", share_with_doctors="false").json()
    assert photo["content_type"] == "image/png" and not photo["share_with_doctors"]
    no_file = patient.post("/api/my/records", data={"title": "Note from 2019 surgery", "kind": "other"})
    assert no_file.status_code == 200 and not no_file.json()["has_file"]

    # Bad files
    assert upload(patient, b"<script>alert(1)</script>", "x.pdf").status_code == 400
    monkeypatch.setattr(platform.settings, "MAX_UPLOAD_MB", 0)
    assert upload(patient).status_code == 400
    monkeypatch.undo()
    assert upload(patient, record_date="2999-01-01").status_code == 422

    # The doctor they booked sees shared records only; other doctors see nothing
    seen = {r["id"] for r in gp.get(f"/api/doctor/patients/{patient.user['id']}/records").json()["records"]}
    assert rec["id"] in seen and photo["id"] not in seen
    assert gp.get(f"/api/records/{rec['id']}/file").status_code == 200
    assert gp.get(f"/api/records/{photo['id']}/file").status_code == 404
    stranger = platform.gp(name="Dr. Stranger")
    assert stranger.get(f"/api/doctor/patients/{patient.user['id']}/records").status_code == 404
    assert stranger.get(f"/api/records/{rec['id']}/file").status_code == 404
    assert world["near"].get(f"/api/records/{rec['id']}/file").status_code == 404

    assert patient.patch(f"/api/my/records/{rec['id']}", json={"share_with_doctors": False}).json()["share_with_doctors"] is False
    assert gp.get(f"/api/records/{rec['id']}/file").status_code == 404
    # Another patient can't touch it
    other = platform.patient(name="Other")
    assert other.get(f"/api/records/{rec['id']}/file").status_code == 404
    assert other.delete(f"/api/my/records/{rec['id']}").status_code == 404
    assert patient.delete(f"/api/my/records/{rec['id']}").json()["ok"]
    assert rec["id"] not in {r["id"] for r in patient.get("/api/my/records").json()}


def test_other_medicines(platform, world):
    patient, gp = world["patient"], world["gp"]
    med = patient.post("/api/my/medicines", json={"name": "Shelcal 500", "dosage": "1-0-0", "since": "2024-01-01",
                                                  "prescribed_by": "Family doctor"}).json()
    stopped = patient.post("/api/my/medicines", json={"name": "Old cough syrup", "active": False}).json()
    assert [m["name"] for m in patient.get("/api/my/medicines").json()] == ["Shelcal 500", "Old cough syrup"]
    view = gp.get(f"/api/doctor/patients/{patient.user['id']}/records").json()
    assert [m["name"] for m in view["medicines"]] == ["Shelcal 500"]  # only medicines still being taken
    changed = patient.put(f"/api/my/medicines/{med['id']}", json={"name": "Shelcal 500", "active": False}).json()
    assert changed["active"] is False
    assert platform.patient(name="Xavier").put(f"/api/my/medicines/{med['id']}", json={"name": "Hack"}).status_code == 404
    patient.delete(f"/api/my/medicines/{stopped['id']}")
    assert len(patient.get("/api/my/medicines").json()) == 1


def test_export_and_account_closure_cover_records(platform, world):
    patient = world["patient"]
    rec = upload(patient).json()
    patient.post("/api/my/medicines", json={"name": "Shelcal 500"})
    data = json.loads(patient.get("/api/my/data").content)
    assert data["health_records"][0]["title"] == "Old blood report" and data["other_medicines"][0]["name"] == "Shelcal 500"
    stored = list(Path(platform.settings.UPLOAD_DIR).rglob("*.pdf"))
    assert len(stored) == 1
    assert patient.post("/api/my/delete", json={"password": "patient-pass"}).status_code == 200
    assert not stored[0].exists()  # the patient's own files are deleted

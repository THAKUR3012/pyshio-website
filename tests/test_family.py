"""Family members: profiles, booking for them, and who the doctor / prescription shows."""
from test_network import prescribe  # noqa: F401


def add_member(patient, **kw):
    body = {"name": "Riya Rao", "relation": "daughter", "dob": "2017-04-02", "sex": "F", "blood_group": "B+",
            "allergies": "Peanuts"} | kw
    res = patient.post("/api/my/family", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def test_family_crud_and_limits(platform):
    patient = platform.patient()
    riya = add_member(patient)
    assert riya["label"] == "Riya Rao (daughter)" and riya["age"] >= 7
    assert patient.post("/api/my/family", json={"name": "X", "relation": "cousin"}).status_code == 422
    assert patient.post("/api/my/family", json={"name": "Baby", "relation": "son", "dob": "2999-01-01"}).status_code == 422
    upd = patient.put(f"/api/my/family/{riya['id']}", json={"name": "Riya A. Rao", "relation": "daughter"}).json()
    assert upd["name"] == "Riya A. Rao" and upd["allergies"] is None
    other = platform.patient(name="Other Parent")
    assert other.put(f"/api/my/family/{riya['id']}", json={"name": "Mine", "relation": "son"}).status_code == 404
    assert other.get("/api/my/family").json() == []
    for i in range(9):
        add_member(patient, name=f"Member {chr(65 + i)}")
    assert patient.post("/api/my/family", json={"name": "Eleventh", "relation": "other"}).status_code == 400
    assert patient.delete(f"/api/my/family/{riya['id']}").status_code == 200
    assert len(patient.get("/api/my/family").json()) == 9


def test_booking_for_a_family_member(platform):
    gp = platform.gp(km_from_home=1)
    patient = platform.patient(name="Asha Rao")
    platform.set_home(patient)
    riya = add_member(patient)
    res = platform.book(patient, gp, family_member_id=riya["id"])
    assert res.status_code == 200, res.text
    appt = res.json()["appointment"]
    assert appt["member_name"] == "Riya Rao" and appt["patient_display"] == "Riya Rao (daughter) – booked by Asha Rao"
    seen = gp.get(f"/api/appointments/{appt['id']}").json()
    assert seen["patient_dob"] == "2017-04-02" and seen["patient_sex"] == "F"
    doctor_mail = platform.mails_to(gp.user["email"])[-1]["text"]
    assert "Riya Rao (daughter) – booked by Asha Rao" in doctor_mail
    patient_mail = platform.mails_to(patient.user["email"])[-1]["text"]
    assert "For: Riya Rao (daughter)" in patient_mail
    # The doctor sees Riya's own health profile (not the parent's)
    profile = gp.get(f"/api/doctor/patients/{patient.user['id']}/health-profile?member_id={riya['id']}").json()
    assert profile["name"] == "Riya Rao" and profile["blood_group"] == "B+" and profile["allergies"] == "Peanuts"
    # Her prescription carries her name; the parent still owns it
    w = {"gp": gp, "appt": appt}
    rx = prescribe(platform, w)
    assert rx["patient"]["name"] == "Riya Rao" and rx["patient"]["booked_by"] == "Asha Rao"
    assert patient.get(f"/api/prescriptions/{rx['id']}").json()["family_member_id"] == riya["id"]


def test_cant_book_for_someone_elses_family(platform):
    doctor = platform.doctor()
    parent = platform.patient(name="Parent One")
    stranger = platform.patient(name="Parent Two")
    kid = add_member(parent)
    assert platform.book(stranger, doctor, family_member_id=kid["id"]).status_code == 404
    parent.delete(f"/api/my/family/{kid['id']}")
    assert platform.book(parent, doctor, family_member_id=kid["id"]).status_code == 404


def test_family_records_and_export(platform):
    patient = platform.patient()
    kid = add_member(patient)
    res = patient.post("/api/my/records", data={"title": "Vaccination card", "family_member_id": str(kid["id"])})
    assert res.status_code == 200, res.text
    assert res.json()["for_name"] == "Riya Rao"
    other = platform.patient(name="Not Family")
    other_kid = add_member(other, name="Someone Else")
    assert patient.post("/api/my/records", data={"title": "X", "family_member_id": str(other_kid["id"])}).status_code == 404
    export = patient.get("/api/my/data").json()
    assert export["family_members"][0]["name"] == "Riya Rao"

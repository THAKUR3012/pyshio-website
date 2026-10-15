"""Symptom checker: red flags first (never sent to Claude), speciality hints, cheapest matching doctors nearby."""
import pytest

from conftest import HOME

EMERGENCIES = [
    "I have chest pain and sweating", "seene mein dard ho raha hai", "saans nahi aa rahi", "his face is drooping and speech slurred",
    "my father fainted", "behosh ho gaya", "she had fits", "vomiting blood", "I want to die", "marna chahta hoon",
    "my son swallowed tablets", "lips swelling after eating", "3 month old baby has fever", "snake bite on leg",
    "bleeding during pregnancy",
]


@pytest.mark.parametrize("text", EMERGENCIES)
def test_red_flags_answer_emergency_without_claude(platform, fake_claude, text):
    res = platform.client().post("/api/triage", json={"text": text})
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["emergency"] is True and "108" in out["message"] and out["doctors"] == []
    assert fake_claude.payloads == []          # red flags never go to the model


def test_suicide_flag_gives_helpline(platform):
    out = platform.client().post("/api/triage", json={"text": "I don't want to live any more"}).json()
    assert out["emergency"] and any("14416" in m for m in out["messages"])


def test_tooth_pain_finds_cheapest_dentists_nearby(platform):
    platform.doctor(name="Dr. Dear Dentist", category="dentist", qualification="BDS", specialities=["General dentistry"],
                    fee=600, km_from_home=1)
    platform.doctor(name="Dr. Cheap Dentist", category="dentist", qualification="BDS", specialities=["Root canal"],
                    fee=300, km_from_home=2)
    platform.doctor(name="Dr. Physio", fee=200, km_from_home=1)  # wrong kind of doctor
    lat, lng = HOME
    out = platform.client().post("/api/triage", json={"text": "daant mein bahut dard hai", "lat": lat, "lng": lng}).json()
    assert not out["emergency"] and "General dentistry" in out["specialities"]
    assert [d["name"] for d in out["doctors"]] == ["Dr. Cheap Dentist", "Dr. Dear Dentist"]


def test_no_specialist_nearby_falls_back_to_gp(platform):
    platform.gp(name="Dr. Family", km_from_home=1)
    lat, lng = HOME
    out = platform.client().post("/api/triage", json={"text": "itchy skin rash", "lat": lat, "lng": lng}).json()
    assert out["specialities"] == ["Dermatology"] and out["fallback_to_gp"] and out["doctors"][0]["name"] == "Dr. Family"


def test_needs_location_for_anonymous(platform):
    out = platform.client().post("/api/triage", json={"text": "back pain"}).json()
    assert out["needs_location"] and "Orthopaedics" in out["specialities"]


def test_patient_home_is_used(platform):
    patient = platform.patient()
    platform.set_home(patient)
    platform.gp(name="Dr. Near GP", km_from_home=0.5)
    out = patient.post("/api/triage", json={"text": "fever and cough since 2 days"}).json()
    assert out["doctors"][0]["name"] == "Dr. Near GP"


def test_claude_picks_from_fixed_list_and_can_only_escalate(platform, fake_claude):
    fake_claude.emit({"specialities": ["ENT"], "emergency": False, "reason": "Ear symptoms."})
    out = platform.client().post("/api/triage", json={"text": "ringing sound and blocked feeling"}).json()
    assert out["engine"] == "claude" and out["specialities"] == ["ENT"]
    fake_claude.emit({"specialities": ["Made-up speciality"], "emergency": False, "reason": "x"})
    out = platform.client().post("/api/triage", json={"text": "back pain"}).json()
    assert out["engine"] == "rules" and "Orthopaedics" in out["specialities"]     # invalid enum -> keyword answer
    fake_claude.emit({"specialities": ["Family medicine"], "emergency": True, "reason": "x"})
    out = platform.client().post("/api/triage", json={"text": "mild headache"}).json()
    assert out["emergency"] is True
    sent = fake_claude.sent_text()
    assert "Made-up" not in sent  # nothing echoed back; and the allowed list was sent as an enum
    assert '"enum"' in sent


def test_rate_limited(platform):
    c = platform.client()
    for _ in range(30):
        assert c.post("/api/triage", json={"text": "cough"}).status_code == 200
    assert c.post("/api/triage", json={"text": "cough"}).status_code == 429


def test_doctor_search_accepts_several_specialities(platform):
    platform.doctor(name="Dr. Skin", category="specialist", qualification="MD", specialities=["Dermatology"], km_from_home=1)
    platform.doctor(name="Dr. Ear", category="specialist", qualification="MS", specialities=["ENT"], km_from_home=1)
    lat, lng = HOME
    names = {d["name"] for d in platform.client().get(f"/api/doctors?lat={lat}&lng={lng}&speciality=ENT|Dermatology").json()}
    assert names == {"Dr. Skin", "Dr. Ear"}

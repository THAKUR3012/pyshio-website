"""AI scribe: notes -> draft prescription (built-in parser, Claude, fallback, no invented medicines, no PII sent)."""
from conftest import assert_no_pii
from test_network import world  # noqa: F401  (fixture)

NOTES = """C/o fever 3 days, body ache
Dx: Viral fever, HTN
Tab Dolo 650 1-0-1 x 5 days after food
Pan 40 OD x 2 weeks
Inv: CBC, HbA1c, S. Creatinine
Adv: plenty of fluids
Review after 1 week"""


def draft(w, notes=NOTES):
    res = w["gp"].post("/api/doctor/scribe", json={"appointment_id": w["appt"]["id"], "notes": notes})
    assert res.status_code == 200, res.text
    return res.json()


def test_builtin_parser(platform, world):
    d = draft(world)
    assert d["engine"] == "built-in"
    dolo, pan = d["items"]
    assert (dolo["name"], dolo["dosage"], dolo["duration_days"], dolo["quantity"]) == ("Dolo 650", "1-0-1", 5, 10)
    assert dolo["medicine_id"] and dolo["instructions"] == "after food"
    assert pan["name"] == "Pan 40" and pan["dosage"] == "OD" and pan["duration_days"] == 14 and pan["quantity"] == 14
    assert {c["code"] for c in d["conditions"]} >= {"I10"}
    assert [t["name"] for t in d["tests"]] == ["Complete Blood Count (CBC)", "HbA1c (glycated haemoglobin)", "Serum Creatinine"]
    assert all(t["test_id"] for t in d["tests"])
    assert d["advice"] == "plenty of fluids" and d["follow_up_in_days"] == 7


def test_claude_draft_is_rematched_and_redacted(platform, world, fake_claude):
    fake_claude.emit({"diagnoses": ["E11.9"], "tests": ["hba1c", "Thallium stress test"],
                      "medicines": [{"name": "Glycomet 500", "dosage": "1-0-1", "duration": "30 days"},
                                    {"name": "Invented Wonderpill", "dosage": "OD"}],
                      "advice": "Walk daily", "follow_up_in_days": 30})
    notes = "Asha Rao, ph 98765 43210. Sugar high. Start glycomet 500 BD for a month. HbA1c. Walk daily."
    d = draft(world, notes)
    assert d["engine"] == "claude"
    assert [i["name"] for i in d["items"]] == ["Glycomet 500"] and d["items"][0]["quantity"] == 60
    assert "Invented Wonderpill" in d["unparsed_lines"] and "Thallium stress test" in d["unparsed_lines"]
    assert d["conditions"][0]["code"] == "E11.9" and d["tests"][0]["name"].startswith("HbA1c")
    assert_no_pii(fake_claude, "Asha", "98765 43210", world["patient"].user["email"])
    payload = fake_claude.payloads[0]
    assert payload["tool_choice"] == {"type": "tool", "name": "emit"}
    assert payload["system"][-1]["cache_control"] == {"type": "ephemeral"}


def test_claude_down_falls_back(platform, world, fake_claude):
    fake_claude.fail()
    assert draft(world)["engine"] == "built-in"


def test_only_own_appointments(platform, world):
    stranger = platform.gp(name="Dr. Stranger")
    res = stranger.post("/api/doctor/scribe", json={"appointment_id": world["appt"]["id"], "notes": NOTES})
    assert res.status_code == 404
    assert world["patient"].post("/api/doctor/scribe", json={"appointment_id": world["appt"]["id"], "notes": NOTES}).status_code in (401, 403)

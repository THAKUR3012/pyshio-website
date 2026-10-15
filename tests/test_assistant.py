"""In-app assistant: every role's questions work, answers use only the user's own data, optional AI layer."""
import json

import pytest

from test_network import prescribe, world  # noqa: F401  (fixture)
from test_phase2 import complete_order


def ask(client, message, history=()):
    res = client.post("/api/assistant", json={"message": message, "history": list(history)})
    assert res.status_code == 200, res.text
    return res.json()


@pytest.fixture
def everyone(platform, world):
    w = world
    platform.stock(w["near"], "Dolo 650", price=15, mrp=20)
    w["rx"] = prescribe(platform, w)
    w["lab"] = platform.lab("Ask Labs", km_from_home=0.6)
    w["company"] = platform.company("Ask Pharma")
    w["mr"] = platform.mr(w["company"], state="Maharashtra", city="Pune")
    w["admin"] = platform.admin()
    return w


def test_every_suggestion_works_for_every_role(platform, everyone):
    for key in ("patient", "gp", "near", "lab", "company", "mr", "admin"):
        client = everyone[key]
        intro = client.get("/api/assistant").json()
        assert intro["mode"] == "built-in" and intro["suggestions"], key
        for chip in intro["suggestions"]:
            out = ask(client, chip)
            assert out["text"] and "skill" in out, (key, chip, out)


def test_patient_answers_come_from_their_own_data(platform, everyone):
    w = everyone
    out = ask(w["patient"], "When is my next appointment?")
    assert out["skill"] == "next_appointments" and w["gp"].user["name"] in out["text"]
    out = ask(w["patient"], "dawai sasta kahan milega?")  # Hinglish: where are medicines cheapest
    assert out["skill"] == "cheapest_pharmacy" and "Near Pharmacy" in out["text"]
    assert out["table"]["columns"][0]["label"] == "Best for" and out["links"][0]["href"] == "/account#prescriptions"

    order = w["patient"].post(f"/api/prescriptions/{w['rx']['id']}/orders", json={"pharmacy_id": w["near"].user["id"]}).json()["order"]
    out = ask(w["patient"], "where is my order")
    assert out["skill"] == "orders" and order["reference"] in out["text"]
    # Another patient never sees it
    other = platform.patient(name="Other Person")
    out = ask(other, "where is my order")
    assert order["reference"] not in json.dumps(out) and "no medicine orders" in out["text"]
    complete_order(platform, w, order["id"])
    assert "₹" in ask(w["patient"], "how much have I spent")["text"]


def test_unknown_and_greeting_offer_help(platform, everyone):
    out = ask(everyone["patient"], "hello")
    assert out["suggestions"] and out["text"].startswith("Hi Asha")
    out = ask(everyone["gp"], "what is the weather in Goa")
    assert "didn't catch" in out["text"] and out["suggestions"]


def test_pharma_answers_never_include_patients(platform, everyone):
    w = everyone
    telma = [b for b in w["company"].get("/api/company/brands?q=Dolo").json() if b["name"] == "Dolo 650"][0]
    w["company"].post(f"/api/company/brands/{telma['id']}")
    for q in ("How are our products selling?", "Which doctors prescribe us most?", "Which diseases drive our sales?"):
        text = json.dumps(ask(w["mr"], q))
        assert "Asha" not in text and "@example.com" not in text


def test_ai_mode_uses_only_the_roles_tools(platform, everyone, fake_claude):
    fake_claude.tool("next_appointments")

    def answer(payload):
        tool_result = json.loads(payload["messages"][-1]["content"][0]["content"])
        return {"content": [{"type": "text", "text": "AI says: " + tool_result["text"]}], "stop_reason": "end_turn"}

    fake_claude.queue.append(answer)
    out = ask(everyone["patient"], "kab hai mera appointment?")
    assert out["mode"] == "ai" and out["text"].startswith("AI says: Your next appointment")
    first = fake_claude.payloads[0]
    tools = {t["name"]: t for t in first["tools"]}
    assert "next_appointments" in tools and not set(tools) & {"sales", "pending", "pharmacy_forecast", "lab_today"}
    # Tools never take ids: only a few declared, typed arguments
    assert all(set(t["input_schema"]["properties"]) <= {"test", "symptoms", "days"} for t in tools.values())
    assert tools["trends"]["input_schema"]["properties"]["test"]["type"] == "string"
    # Prompt caching on the stable system text and the tool list; the name isn't in the cached part
    assert first["system"][0]["text"].startswith("You are the") and "Asha" not in first["system"][0]["text"]
    assert first["system"][-1]["cache_control"] == {"type": "ephemeral"} and first["tools"][-1]["cache_control"]

    # If the AI service fails, the built-in answer is used
    fake_claude.fail()
    out = ask(everyone["patient"], "When is my next appointment?")
    assert out["mode"] == "built-in" and out["skill"] == "next_appointments"


def test_ai_tool_arguments_are_validated(platform, everyone, fake_claude):
    fake_claude.tool("trends", {"test": "HbA1c", "patient_id": 99})       # unknown keys are dropped
    fake_claude.reply("Done.")
    out = ask(everyone["patient"], "how is my hba1c doing?")
    assert out["skill"] == "trends" and out["text"] == "Done."
    assert "reports with HbA1c" in fake_claude.sent_text()  # the handler got the test name (its answer went back to the AI)
    fake_claude.tool("pharmacy_forecast", {"days": 999})                   # not an allowed value -> default window
    fake_claude.reply("Ok.")
    out = ask(everyone["near"], "forecast please")
    assert out["mode"] == "ai" and out["skill"] == "pharmacy_forecast"


def test_red_flags_never_reach_the_ai(platform, everyone, fake_claude):
    out = ask(everyone["patient"], "I have severe chest pain since 20 minutes")
    assert out["skill"] == "emergency" and "108" in out["text"] and fake_claude.payloads == []


def test_builtin_symptom_check_and_follow_ups(platform, everyone):
    out = ask(everyone["patient"], "which doctor for khansi aur bukhar?")
    assert out["skill"] == "symptom_check" and "Fever & infections" in out["text"]
    out = ask(everyone["patient"], "Show my latest report")
    assert out["suggestions"][0] == "Explain my latest report"
    out = ask(everyone["near"], "What should I reorder for 30 days?")
    assert out["skill"] == "reorder"


def test_requires_login_and_is_rate_limited(platform, everyone):
    assert platform.client().post("/api/assistant", json={"message": "hi"}).status_code == 401
    for _ in range(30):
        everyone["near"].post("/api/assistant", json={"message": "hi"})
    assert everyone["near"].post("/api/assistant", json={"message": "hi"}).status_code == 429

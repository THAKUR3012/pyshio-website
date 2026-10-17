"""Symptom checker: describe the problem in English or Hinglish, get emergency warnings and the right kind of doctor nearby.

This file: logic. Red flags are rules and always run first; when one matches, Claude is never asked. Otherwise the
keyword map picks specialities; Claude (optional) may choose from the same fixed list of specialities and may raise
an emergency, but can never clear one. Symptom text is not stored.
"""
from __future__ import annotations

from shared import ai, settings
from shared import triage_rules as rules

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "specialities": {"type": "array", "maxItems": 3, "items": {"type": "string", "enum": settings.SPECIALITIES}},
        "emergency": {"type": "boolean", "description": "True if this could need emergency care today."},
        "reason": {"type": "string", "maxLength": 200, "description": "One short, plain sentence on why these doctors."},
    },
    "required": ["specialities", "emergency", "reason"],
}
SYSTEM = ("You help a patient in India choose which kind of doctor to see. From the symptoms, pick up to 3 specialities "
          "from the allowed list, most suitable first; prefer 'Family medicine' when unsure. Set emergency to true if the "
          "symptoms could need emergency care today. Never diagnose, never suggest medicines. Reply only by calling the emit tool.")
DISCLAIMER = "This is guidance on which doctor to see, not a diagnosis. If you feel very unwell, call 108."


def emergency_answer(hits: list) -> dict:
    return {"emergency": True, "call": rules.EMERGENCY_CALL, "messages": [m for _, m in hits], "flags": [f for f, _ in hits],
            "message": "This could be an emergency. Call " + rules.EMERGENCY_CALL + " or go to the nearest hospital now.",
            "specialities": [], "reason": None, "engine": "rules"}


def classify(text: str, age=None, user_id=None) -> dict:
    hits = rules.red_flags(text, age)
    if hits:
        return emergency_answer(hits)
    matches = rules.symptom_matches(text)
    specs, labels = [], []
    for _, group, label in matches[:2]:
        labels.append(label)
        specs += [s for s in group if s not in specs]
    out = {"emergency": False, "specialities": specs or list(rules.DEFAULT_SPECIALITIES), "matched": labels,
           "reason": f"Based on what you wrote ({', '.join(labels)})." if labels else "A general physician is a good first stop.",
           "engine": "rules"}
    if ai.enabled("triage"):
        try:
            res = ai.extract("triage", system=SYSTEM, content=[ai.text(f"Age: {ai.age_band(age)}.\nSymptoms: {ai.redact(text)}")],
                             schema=CLASSIFY_SCHEMA, max_tokens=300, timeout=20, user_id=user_id)
            if res.get("emergency"):
                return emergency_answer([("ai", "These symptoms could need urgent care.")]) | {"engine": "claude"}
            picked = [s for s in res.get("specialities", []) if s in settings.SPECIALITIES]
            if picked:
                out.update(specialities=picked, reason=res.get("reason") or out["reason"], engine="claude")
        except ai.AIUnavailable:
            pass
    return out


def answer(text: str, *, age=None, lat=None, lng=None, user=None) -> dict:
    out = classify(text, age, user["id"] if user else None)
    out["disclaimer"] = DISCLAIMER
    if out["emergency"]:
        out["doctors"] = []
        return out
    if lat is None and user and user.get("home_lat") is not None:
        lat, lng = user["home_lat"], user["home_lng"]
    if lat is None:
        out["doctors"], out["needs_location"] = [], True
        return out
    doctors = doctors_api.list_doctors(speciality="|".join(out["specialities"]), lat=lat, lng=lng, user=user,
                                       city=None, q=None, category=None, radius=None, sort="cheapest", home_visit=False,
                                       video=False)
    widened = False
    if not doctors and out["specialities"] != rules.DEFAULT_SPECIALITIES:
        doctors = doctors_api.list_doctors(speciality="|".join(rules.DEFAULT_SPECIALITIES), lat=lat, lng=lng, user=user,
                                           city=None, q=None, category=None, radius=None, sort="cheapest",
                                           home_visit=False, video=False)
        widened = bool(doctors)
    out["doctors"] = doctors[:10]
    out["fallback_to_gp"] = widened
    out["search_path"] = "/?speciality=" + "|".join(out["specialities"])
    return out


# Imported last, so slices can depend on each other in any order
from features.doctors import api as doctors_api  # noqa: E402

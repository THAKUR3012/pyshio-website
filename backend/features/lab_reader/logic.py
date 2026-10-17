"""Lab reports: read values from the report file for the lab to confirm, and explain results to patients in plain language.

This file: logic.
- Reading: Claude reads the PDF / photo and suggests values; each name is matched to the test catalogue and the
  high / low flag is always worked out here from the ranges (never taken from the model). Without Claude the form is
  pre-filled with the booked tests and their usual units and ranges. Nothing is saved: the lab checks and submits.
- Explaining: built-in sentences per test (shared/lab_explain.py) from the value, flag and trend; Claude may rewrite
  them more naturally from the numbers only (no names), with the disclaimer always added here.
"""
from __future__ import annotations

import re
from typing import Optional

from shared import ai, clinical_terms
from shared.lab_explain import DISCLAIMER, LAB_EXPLAIN, WORDS

READ_SCHEMA = {
    "type": "object",
    "properties": {"values": {"type": "array", "maxItems": 50, "items": {"type": "object", "properties": {
        "name": {"type": "string", "maxLength": 120}, "value": {"type": "string", "maxLength": 60},
        "unit": {"type": ["string", "null"], "maxLength": 30}, "ref_low": {"type": ["number", "null"]},
        "ref_high": {"type": ["number", "null"]}}, "required": ["name", "value"]}}},
    "required": ["values"],
}
READ_SYSTEM = ("You read Indian lab reports. List each test result exactly as printed: test name, value, unit and the "
               "reference range if printed. Do not interpret, do not add tests that are not printed, and leave out the "
               "patient's name, age, IDs and the lab's details. Reply only by calling the emit tool.")
EXPLAIN_SYSTEM = ("You explain lab results to an Indian patient in simple words. Use only the facts given. Do not diagnose, "
                  "do not name or suggest any medicine or dose, and do not say whether treatment is needed. Encourage "
                  "them to discuss results with their doctor. 4-6 short sentences. Language: {lang}.")
DOSAGE_PATTERN = re.compile(r"\b\d+\s*(mg|mcg|ml|tablet|tablets|capsule|units?)\b|\btake\b|\bdose\b", re.I)


def _unit_key(unit: Optional[str]) -> str:
    return re.sub(r"[\s.]", "", (unit or "").lower()).replace("µ", "u").replace("micro", "u")


def build_value(conn, name: str, value: str, unit=None, ref_low=None, ref_high=None) -> dict:
    """One suggested value: matched to the catalogue, flag recomputed, unit mismatches marked for checking."""
    test = clinical_terms.match_test(conn, name)
    low = ref_low if ref_low is not None else (test["ref_low"] if test else None)
    high = ref_high if ref_high is not None else (test["ref_high"] if test else None)
    unit = unit or (test["unit"] if test else None)
    check_unit = bool(test and test["unit"] and unit and _unit_key(unit) != _unit_key(test["unit"]) and ref_low is None
                      and ref_high is None)
    number = health_records.to_number(value) if value else None
    return {"name": test["name"] if test else name, "written_as": name, "test_id": test["id"] if test else None,
            "value": value, "unit": unit, "ref_low": low, "ref_high": high,
            "flag": None if check_unit else health_records.flag_for(number, low, high), "check_unit": check_unit}


def read_report(conn, booking_id: str, data: bytes, user_id: int) -> dict:
    """Suggested values for a lab report file (Claude), or the booked tests to fill in (built-in)."""
    if ai.enabled("lab_read"):
        try:
            out = ai.extract("lab_read", system=READ_SYSTEM, content=[ai.file_block(data), ai.text("Read this lab report.")],
                             schema=READ_SCHEMA, max_tokens=2000, timeout=45, user_id=user_id)
            values = [build_value(conn, v["name"], str(v["value"]).strip(), v.get("unit"), v.get("ref_low"), v.get("ref_high"))
                      for v in out.get("values", []) if str(v.get("value", "")).strip()]
            return {"engine": "claude", "values": values}
        except ai.AIUnavailable:
            pass
    values = []
    for item in labs_logic.booking_items(conn, booking_id):
        test = conn.execute("SELECT * FROM lab_tests WHERE id = ?", (item["test_id"],)).fetchone() if item.get("test_id") else None
        if test and (test["unit"] or test["ref_low"] is not None):
            values.append({"name": test["name"], "written_as": test["name"], "test_id": test["id"], "value": "",
                           "unit": test["unit"], "ref_low": test["ref_low"], "ref_high": test["ref_high"], "flag": None,
                           "check_unit": False})
    return {"engine": "built-in", "values": values}


def _code_for(conn, value: dict) -> Optional[str]:
    if value.get("test_id"):
        row = conn.execute("SELECT code FROM lab_tests WHERE id = ?", (value["test_id"],)).fetchone()
        if row:
            return row["code"]
    hit = clinical_terms.match_test(conn, value["name"])
    return hit["code"] if hit else None


def fmt(n) -> str:
    return f"{n:g}" if isinstance(n, (int, float)) else str(n)


def builtin_explanation(conn, values: list, trends: list, lang: str) -> list:
    words = WORDS[lang]
    trend_by_test = {t.get("test_id"): t for t in trends if t.get("test_id")}
    lines = []
    for v in values:
        shown = v["value_num"] if v["value_num"] is not None else v["value_text"]
        if shown is None:
            continue
        code = _code_for(conn, v)
        notes = LAB_EXPLAIN.get(code, {}).get(lang)
        parts = []
        if notes:
            parts.append(notes[0])
        rng = ""
        if v["ref_low"] is not None or v["ref_high"] is not None:
            rng = f" ({fmt(v['ref_low']) if v['ref_low'] is not None else ''}–{fmt(v['ref_high']) if v['ref_high'] is not None else ''})"
        if v["flag"]:
            parts.append(f"{v['name']}: {fmt(shown)} {v['unit'] or ''}".strip() + f", {words[v['flag']]}{rng}.")
            if notes and v["flag"] == "high":
                parts.append(notes[1])
            elif notes and v["flag"] == "low":
                parts.append(notes[2])
        else:
            parts.append(f"{v['name']}: {fmt(shown)} {v['unit'] or ''}".strip() + ".")
        t = trend_by_test.get(v.get("test_id"))
        if t and t.get("direction") and len(t["points"]) >= 2:
            sentence = f"{'Over your reports it' if lang == 'en' else 'Pichhli reports mein yeh'} {words[t['direction']]}"
            if t.get("toward_normal") is not None:
                sentence += f", {words['better' if t['toward_normal'] else 'worse']}"
            parts.append(sentence + ".")
        lines.append({"name": v["name"], "flag": v["flag"], "text": " ".join(parts)})
    return lines


def explain(conn, record, values: list, trends: list, lang: str, user_id=None) -> dict:
    lines = builtin_explanation(conn, values, trends, lang)
    engine, summary = "built-in", None
    if lines and ai.enabled("lab_explain"):
        facts = "\n".join(f"- {l['text']}" for l in lines)
        try:
            res = ai.call("lab_explain", system=EXPLAIN_SYSTEM.format(lang="English" if lang == "en" else "Hinglish (Roman script)"),
                          messages=[{"role": "user", "content": f"Results and notes:\n{facts}"}], max_tokens=500, user_id=user_id)
            text = ai.text_of(res)
            if text and not DOSAGE_PATTERN.search(text):
                engine, summary = "claude", text
        except ai.AIUnavailable:
            pass
    return {"engine": engine, "summary": summary, "lines": lines, "disclaimer": DISCLAIMER[lang],
            "abnormal": sum(1 for l in lines if l["flag"] in ("high", "low"))}


# Imported last, so slices can depend on each other in any order
from features.health_records import logic as health_records  # noqa: E402
from features.labs import logic as labs_logic  # noqa: E402

"""AI scribe: the doctor types or dictates consultation notes; Pyshio drafts the prescription for them to review.

This file: logic. A built-in parser understands the usual shorthand ("Tab Dolo 650 1-0-1 x 5 days", "Inv: CBC, HbA1c",
"Dx: T2DM", "Review after 2 weeks"). With Claude switched on, Claude reads freer notes; every name it returns is
matched to the catalogue again here, and anything not in the catalogue or the notes is dropped. Nothing is saved:
the draft fills the prescription form, the safety check runs, and the doctor issues it themselves.
"""
from __future__ import annotations

import math
import re
from typing import Optional

from shared import ai, clinical_terms, salts

DOSE_RE = r"(?:\d+(?:\.\d+|/\d+)?(?:\s*-\s*\d+(?:\.\d+|/\d+)?){1,3}|od|bd|bid|tds|tid|qid|hs|sos|stat|once daily|twice daily)"
DURATION_RE = r"(?:x|for|×)?\s*(\d+)\s*(d|days?|w|wks?|weeks?|m|months?)\b"
MED_PREFIX = re.compile(r"^\s*(?:\d+[.)]\s*)?(?:rx\s*[:\-]?\s*)?(tab|tablet|cap|capsule|syp|syrup|susp|inj|injection|drops?|oint|cream|gel|inhaler)\.?\s+", re.I)
FORM_OF = {"tab": "tablet", "tablet": "tablet", "cap": "capsule", "capsule": "capsule", "syp": "syrup", "syrup": "syrup",
           "susp": "syrup", "inj": "injection", "injection": "injection", "drop": "drops", "drops": "drops",
           "oint": "ointment", "cream": "cream", "gel": "gel", "inhaler": "inhaler"}
SECTION = re.compile(r"^\s*(dx|diagnosis|d/d|impression|inv|investigations?|tests?|labs?|adv|advice|f/u|follow[- ]?up|review|"
                     r"c/o|complaints?|hx|history|o/e|exam|rx|plan)\s*[:\-]\s*", re.I)
FOLLOW_RE = re.compile(r"(?:review|follow[- ]?up|f/u|revisit|come back)\D{0,15}(\d+)\s*(d|days?|w|wks?|weeks?|m|months?)", re.I)

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "diagnoses": {"type": "array", "maxItems": 10, "items": {"type": "string", "maxLength": 120},
                      "description": "Diagnoses as ICD-10 codes or plain names."},
        "medicines": {"type": "array", "maxItems": 30, "items": {"type": "object", "properties": {
            "name": {"type": "string", "maxLength": 120}, "strength": {"type": ["string", "null"], "maxLength": 40},
            "form": {"type": ["string", "null"], "maxLength": 30}, "dosage": {"type": ["string", "null"], "maxLength": 60},
            "duration": {"type": ["string", "null"], "maxLength": 40}, "instructions": {"type": ["string", "null"], "maxLength": 200}},
            "required": ["name"]}},
        "tests": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 120}},
        "advice": {"type": ["string", "null"], "maxLength": 1000},
        "follow_up_in_days": {"type": ["integer", "null"]},
    },
    "required": ["diagnoses", "medicines", "tests"],
}

SYSTEM = ("You turn an Indian doctor's consultation notes into a draft prescription. Copy only what the notes say: never "
          "add a medicine, test, dose or diagnosis the notes do not contain, and never change a dose. Write dosage the way "
          "the notes do (e.g. 1-0-1, BD, SOS). Medicines may be brand names. Ignore any personal details. Reply only by "
          "calling the emit tool.")


def _days(n: str, unit: str) -> Optional[int]:
    days = int(n) * {"d": 1, "w": 7, "m": 30}[unit[0].lower()]
    return days if 1 <= days <= 365 else None


def _quantity(daily_units, days) -> Optional[int]:
    if daily_units and days:
        return max(1, min(1000, math.ceil(daily_units * days)))
    return None


def parse_medicine(conn, line: str) -> Optional[dict]:
    """One medicine line, or None when the line doesn't look like a medicine."""
    text = line.strip().rstrip(".")
    prefix = MED_PREFIX.match(text)
    form = FORM_OF.get(prefix.group(1).lower()) if prefix else None
    rest = text[prefix.end():] if prefix else text
    dose = re.search(rf"(?<![\w/]){DOSE_RE}(?![\w/])", rest, re.I)
    if not prefix and not dose:
        return None
    duration = re.search(DURATION_RE, rest, re.I)
    head_end = min([m.start() for m in (dose, duration) if m] or [len(rest)])
    head = rest[:head_end].strip(" ,-")
    tail = rest[max([m.end() for m in (dose, duration) if m] or [len(rest)]):].strip(" ,-")
    if not head:
        return None
    strength_m = salts.STRENGTH_RE.search(head)
    name = head
    strength = strength_m.group(0) if strength_m else None
    dosage = dose.group(0).strip() if dose else None
    duration_text = f"{duration.group(1)} {duration.group(2)}" if duration else None
    duration_days = _days(duration.group(1), duration.group(2)) if duration else None
    daily = daily_units_from(dosage)
    return {"name": name, "strength": strength, "form": form, "dosage": dosage, "duration": duration_text,
            "duration_days": duration_days, "daily_units": daily, "quantity": _quantity(daily, duration_days),
            "instructions": tail or None}


def _split(notes: str) -> list:
    lines = []
    for raw in re.split(r"[\n;]+", notes or ""):
        raw = raw.strip()
        if raw:
            lines.append(raw)
    return lines


def builtin_draft(conn, notes: str) -> dict:
    diagnoses, meds, tests, advice, unparsed = [], [], [], [], []
    follow = None
    for line in _split(notes):
        m = FOLLOW_RE.search(line)
        if m:
            follow = _days(m.group(1), m.group(2))
            if FOLLOW_RE.fullmatch(line.strip()) or SECTION.match(line):
                continue
        sec = SECTION.match(line)
        label = sec.group(1).lower() if sec else ""
        body = line[sec.end():] if sec else line
        if label in ("dx", "diagnosis", "d/d", "impression"):
            diagnoses += [p.strip() for p in re.split(r",|/| and ", body) if p.strip()]
        elif label.startswith(("inv", "test", "lab")):
            tests += [p.strip() for p in re.split(r",|/|\+", body) if p.strip()]
        elif label in ("adv", "advice", "plan"):
            advice.append(body.strip())
        elif label in ("c/o", "complaint", "complaints", "hx", "history", "o/e", "exam"):
            continue  # clinical notes, not part of the prescription
        else:
            med = parse_medicine(conn, body)
            if med:
                meds.append(med)
            elif all(clinical_terms.match_test(conn, p) for p in re.split(r",|\+", body) if p.strip()):
                tests += [p.strip() for p in re.split(r",|\+", body) if p.strip()]
            else:
                unparsed.append(line)
    return {"diagnoses": diagnoses, "medicines": meds, "tests": tests, "advice": "; ".join(a for a in advice if a) or None,
            "follow_up_in_days": follow, "unparsed_lines": unparsed}


def claude_draft(conn, notes: str, *, names=(), age=None, sex=None, user_id=None) -> dict:
    context = f"Patient: age band {ai.age_band(age)}, sex {sex or 'not stated'}.\n\nNotes:\n{ai.redact(notes, names)}"
    out = ai.extract("scribe", system=SYSTEM, content=[ai.text(context)], schema=DRAFT_SCHEMA, max_tokens=1500, user_id=user_id)
    meds = []
    for m in out.get("medicines", []):
        daily = daily_units_from(m.get("dosage"))
        days = days_from(m.get("duration"))
        meds.append({**m, "daily_units": daily, "duration_days": days, "quantity": _quantity(daily, days)})
    return {"diagnoses": out.get("diagnoses", []), "medicines": meds, "tests": out.get("tests", []),
            "advice": out.get("advice"), "follow_up_in_days": out.get("follow_up_in_days"), "unparsed_lines": []}


def _in_notes(name: str, notes: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if len(w) >= 3]
    text = (notes or "").lower()
    return bool(words) and words[0] in text


def finish(conn, draft: dict, notes: str, engine: str) -> dict:
    """Match every name to the catalogue; drop what isn't in the catalogue or the notes (no invented medicines)."""
    items, unparsed = [], list(draft.get("unparsed_lines") or [])
    for m in draft["medicines"]:
        found = salts.best_match(conn, m["name"], m.get("strength"), m.get("form"), limit=3)
        best = found[0] if found and found[0]["confidence"] >= 0.8 else None
        if not best and not _in_notes(m["name"], notes):
            unparsed.append(m["name"])
            continue
        if best and engine == "claude" and not (_in_notes(m["name"], notes) or _in_notes(best["name"], notes)
                                                or _in_notes(best.get("generic_name") or "", notes)):
            unparsed.append(m["name"])
            continue
        items.append({
            "medicine_id": best["medicine_id"] if best else None, "name": best["name"] if best else m["name"],
            "strength": best["strength"] if best else m.get("strength"), "form": best["form"] if best else m.get("form"),
            "dosage": m.get("dosage"), "duration": m.get("duration"), "duration_days": m.get("duration_days"),
            "quantity": m.get("quantity"), "instructions": m.get("instructions"),
            "match": {"confidence": best["confidence"] if best else None, "written_as": m["name"],
                      "alternatives": [{"medicine_id": f["medicine_id"], "name": f["name"], "strength": f["strength"]}
                                       for f in found[1:]]}})
    conditions, notes_dx = [], []
    for d in draft["diagnoses"]:
        c = clinical_terms.match_condition(conn, d)
        if c and c["id"] not in [x["id"] for x in conditions]:
            conditions.append(c)
        elif not c:
            notes_dx.append(d)
    tests, seen = [], set()
    for t in draft["tests"]:
        hit = clinical_terms.match_test(conn, t)
        if hit and hit["id"] not in seen:
            seen.add(hit["id"])
            tests.append({"test_id": hit["id"], "name": hit["name"]})
        elif not hit and _in_notes(t, notes):
            tests.append({"test_id": None, "name": t})
        elif not hit:
            unparsed.append(t)
    follow = draft.get("follow_up_in_days")
    follow = follow if isinstance(follow, int) and 1 <= follow <= 365 else None
    return {"engine": engine, "conditions": conditions, "diagnosis_note": ", ".join(notes_dx) or None, "items": items,
            "tests": tests, "advice": draft.get("advice"), "follow_up_in_days": follow, "unparsed_lines": unparsed}


def draft(conn, notes: str, *, names=(), age=None, sex=None, user_id=None) -> dict:
    if ai.enabled("scribe"):
        try:
            return finish(conn, claude_draft(conn, notes, names=names, age=age, sex=sex, user_id=user_id), notes, "claude")
        except ai.AIUnavailable:
            pass
    return finish(conn, builtin_draft(conn, notes), notes, "built-in")


# Imported last, so slices can depend on each other in any order
from features.prescriptions.logic import daily_units_from, days_from  # noqa: E402

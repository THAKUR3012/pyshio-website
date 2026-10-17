"""Prescription safety: interactions, duplicate salts, allergies, age, pregnancy and dose checks.

This file: logic and queries. Rules live in shared/drug_rules.py; everything here is deterministic (no AI).
Alerts are advisory: "major" ones must be acknowledged by the doctor before the prescription is issued.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Optional

from shared import drug_rules as rules
from shared import salts, settings
from shared.common import stamp

SEVERITY_ORDER = {"major": 0, "moderate": 1, "info": 2}
PREGNANCY_CODES = {"Z34.9", "Z33.1", "O26.9"}
VERIFY = " Check a clinical reference before prescribing."

_CLASS_OF = {}
for _cls, _members in rules.CLASSES.items():
    for _m in _members:
        _CLASS_OF.setdefault(_m, set()).add(_cls)


def tags_for(parts: list) -> set:
    """Salt names plus 'class:<x>' tags for one medicine."""
    tags = set(parts)
    for p in parts:
        tags |= {f"class:{c}" for c in _CLASS_OF.get(p, ())}
    return tags


def describe(med: dict) -> dict:
    """Normalise one medicine line: its salts (from the catalogue when known, else the text) and tags."""
    generic = med.get("generic_name")
    parts = salts.salt_parts(generic) if generic else []
    if not parts and med.get("name"):
        guess = salts.salt_parts(med["name"])
        # A free-text brand isn't a salt; keep it only when it is a salt we know
        parts = [p for p in guess if p in _CLASS_OF or p in rules.MAX_DAILY_MG]
    return {**med, "salts": parts, "tags": tags_for(parts), "label": med.get("name") or generic or "medicine"}


def resolve_item(conn, item: dict) -> dict:
    """Fill generic name / strength from the catalogue for a prescription line, or match free text to it."""
    item = dict(item)
    if item.get("medicine_id"):
        med = conn.execute("SELECT name, generic_name, strength, form FROM medicines WHERE id = ?", (item["medicine_id"],)).fetchone()
        if med:
            for k in ("name", "generic_name", "strength", "form"):
                item[k] = item.get(k) or med[k]
    elif item.get("name") and not item.get("generic_name"):
        found = salts.best_match(conn, item["name"], item.get("strength"), item.get("form"), limit=1)
        if found and found[0]["confidence"] >= 0.85:
            item["generic_name"] = found[0]["generic_name"]
            item["strength"] = item.get("strength") or found[0]["strength"]
    return item


def current_medicines(conn, patient_id: int, family_member_id: Optional[int], exclude_rx: Optional[str] = None) -> list:
    """What the person is already taking: active courses, their own medicine list, and live prescriptions (any doctor)."""
    today = settings.now().date().isoformat()
    meds = []
    member = family_member_id
    for r in conn.execute(
            "SELECT c.name, c.generic_name, c.strength FROM courses c JOIN prescriptions p ON p.id = c.prescription_id "
            "WHERE c.patient_id = ? AND COALESCE(p.family_member_id, 0) = COALESCE(?, 0) AND (c.ends_on IS NULL OR c.ends_on >= ?)",
            (patient_id, member, today)):
        meds.append({**dict(r), "source": "course"})
    if not member:
        for r in conn.execute("SELECT name, generic_name, strength FROM patient_medicines WHERE patient_id = ? AND active = 1",
                              (patient_id,)):
            meds.append({**dict(r), "source": "own list"})
    for r in conn.execute(
            "SELECT i.name, i.generic_name, i.strength FROM prescription_items i JOIN prescriptions p ON p.id = i.prescription_id "
            "WHERE p.patient_id = ? AND COALESCE(p.family_member_id, 0) = COALESCE(?, 0) AND p.status = 'active' "
            "AND p.valid_until >= ? AND p.id != COALESCE(?, '') AND (i.is_long_term = 1 OR p.created_at >= ?)",
            (patient_id, member, stamp(), exclude_rx, stamp(settings.now() - timedelta(days=30)))):
        meds.append({**dict(r), "source": "prescription"})
    seen, out = set(), []
    for m in meds:
        key = ((m.get("generic_name") or m.get("name") or "").lower(), (m.get("strength") or "").lower())
        if key not in seen:
            seen.add(key)
            out.append(m)
    return out


def _code(*parts) -> str:
    return hashlib.sha1("|".join(sorted(str(p) for p in parts)).encode()).hexdigest()[:12]


def check(conn, *, items: list, patient_id: int, family_member_id: Optional[int] = None, age: Optional[int] = None,
          pregnancy: Optional[str] = None, allergies: Optional[str] = None, who: str = "The patient",
          exclude_rx: Optional[str] = None) -> dict:
    """All safety alerts for these prescription lines. Returns {alerts, current_medicines}."""
    lines = [describe(resolve_item(conn, i)) for i in items]
    current = [describe(m) for m in current_medicines(conn, patient_id, family_member_id, exclude_rx)]
    alerts = []

    def add(kind, severity, message, idx, extra=()):
        code = _code(kind, *extra)
        if not any(a["code"] == code for a in alerts):
            alerts.append({"code": code, "kind": kind, "severity": severity, "message": message, "items": sorted(set(idx))})

    # 1. Duplicate salts (also inside combinations), within the prescription and against current medicines
    for i, a in enumerate(lines):
        for j in range(i + 1, len(lines)):
            shared = set(a["salts"]) & set(lines[j]["salts"])
            if shared:
                add("duplicate", "major", f"Same salt twice: {a['label']} and {lines[j]['label']} both contain "
                    f"{', '.join(sorted(shared))}.", [i, j], ("dup", *shared))
        for m in current:
            shared = set(a["salts"]) & set(m["salts"])
            if shared:
                add("duplicate_current", "moderate", f"{who} already takes {m['label']} ({m['source']}), which also "
                    f"contains {', '.join(sorted(shared))}.", [i], ("dupc", m["label"], *shared))

    # 2. Interactions: within the prescription, then against current medicines
    def interact(a, b, idx, against=None):
        for ra, rb, severity, message in rules.INTERACTIONS:
            if (ra in a["tags"] and rb in b["tags"]) or (rb in a["tags"] and ra in b["tags"]):
                where = f" ({b['label']} is a current medicine)" if against else f" ({a['label']} + {b['label']})"
                add("interaction", severity, message + where + VERIFY, idx, ("ddi", ra, rb, a["label"], b["label"]))
        if "class:qt_prolonging" in a["tags"] and "class:qt_prolonging" in b["tags"] and set(a["salts"]) != set(b["salts"]):
            add("interaction", "moderate", f"{rules.QT_MESSAGE} ({a['label']} + {b['label']})" + VERIFY, idx,
                ("qt", a["label"], b["label"]))

    for i, a in enumerate(lines):
        for j in range(i + 1, len(lines)):
            interact(a, lines[j], [i, j])
        for m in current:
            interact(a, m, [i], against=True)

    # 3. Allergies (existing class map), promoted to major
    for i, line in enumerate(lines):
        for w in allergy_warnings(allergies, [line], who):
            add("allergy", "major", w, [i], ("allergy", w))

    # 4. Age
    if age is not None:
        for i, line in enumerate(lines):
            for target, lo, hi, severity, message in rules.AGE_CAUTIONS:
                if target in line["tags"] and (lo is None or age >= lo) and (hi is None or age < hi):
                    add("age", severity, f"{message} ({line['label']}, age {age})" + VERIFY, [i], ("age", target, line["label"]))

    # 5. Pregnancy / breastfeeding
    if pregnancy:
        for i, line in enumerate(lines):
            for target, applies, severity, message in rules.PREGNANCY_CAUTIONS:
                if applies == pregnancy and target in line["tags"]:
                    add("pregnancy", severity, f"{message} ({line['label']})" + VERIFY, [i], ("preg", target, line["label"]))

    # 6. Daily dose ceiling (adults, single-salt products)
    if age is None or age >= 12:
        for i, line in enumerate(lines):
            if len(line["salts"]) != 1:
                continue
            limit = rules.MAX_DAILY_MG.get(line["salts"][0])
            mg = salts.strength_mg(line.get("strength"))
            units = line.get("daily_units") or daily_units_from(line.get("dosage"))
            if limit and mg and units and mg * units > limit * 1.0001:
                add("dose", "major", f"{line['label']}: {fmt(mg * units)} mg a day is above the usual maximum of "
                    f"{fmt(limit)} mg." + VERIFY, [i], ("dose", line["label"], mg * units))

    # 7. Lines we couldn't check
    for i, line in enumerate(lines):
        if not line["salts"]:
            add("unchecked", "info", f"Couldn't identify the salt in “{line['label']}”, so it wasn't checked. "
                "Pick it from the catalogue to include it.", [i], ("unk", line["label"]))

    alerts.sort(key=lambda a: (SEVERITY_ORDER[a["severity"]], a["kind"]))
    return {"alerts": alerts,
            "current_medicines": [{"name": m["label"], "strength": m.get("strength"), "source": m["source"]} for m in current]}


def fmt(n: float) -> str:
    return f"{n:,.0f}" if n >= 10 else f"{n:g}"


def pregnancy_from(conn, condition_ids: list, explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit
    if condition_ids:
        codes = {r["code"] for r in conn.execute(f"SELECT code FROM conditions WHERE id IN ({','.join('?' * len(condition_ids))})",
                                                 condition_ids)}
        if codes & PREGNANCY_CODES:
            return "pregnant"
    return None


def unacknowledged(alerts: list, acknowledged: list) -> list:
    ack = set(acknowledged or [])
    return [a for a in alerts if a["severity"] == "major" and a["code"] not in ack]


# Imported last, so slices can depend on each other in any order
from features.health_profile.logic import allergy_warnings  # noqa: E402
from features.prescriptions.logic import daily_units_from  # noqa: E402

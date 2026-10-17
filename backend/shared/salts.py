"""Salt (generic) normalisation and catalogue matching, so "Paracetamol IP 500mg" and "paracetamol 500 mg" are one thing.

Used by substitutes, the prescription safety engine, the AI scribe and the prescription photo reader.
"""
from __future__ import annotations

import difflib
import re
from typing import Optional

ALIASES = {
    "acetaminophen": "paracetamol", "amoxycillin": "amoxicillin", "clavulanic acid": "clavulanate",
    "potassium clavulanate": "clavulanate", "clavulanate potassium": "clavulanate", "acetylsalicylic acid": "aspirin",
    "salbutamol sulphate": "salbutamol", "albuterol": "salbutamol", "glibenclamide": "glyburide",
    "metformin hydrochloride": "metformin", "metformin hcl": "metformin", "atorvastatin calcium": "atorvastatin",
    "rosuvastatin calcium": "rosuvastatin", "pantoprazole sodium": "pantoprazole", "rabeprazole sodium": "rabeprazole",
    "esomeprazole magnesium": "esomeprazole", "amlodipine besylate": "amlodipine", "losartan potassium": "losartan",
    "levothyroxine sodium": "levothyroxine", "thyroxine": "levothyroxine", "cetirizine hydrochloride": "cetirizine",
    "diclofenac sodium": "diclofenac", "diclofenac potassium": "diclofenac", "co-trimoxazole": "cotrimoxazole",
    "frusemide": "furosemide", "lignocaine": "lidocaine",
    "ondansetron hydrochloride": "ondansetron", "tramadol hydrochloride": "tramadol", "sertraline hydrochloride": "sertraline",
    "fluoxetine hydrochloride": "fluoxetine", "escitalopram oxalate": "escitalopram", "clopidogrel bisulphate": "clopidogrel",
}
# Fixed-dose combinations that are usually written by one name
COMBOS = {"sulfamethoxazole+trimethoprim": "cotrimoxazole"}
_SPLIT = re.compile(r"\s*(?:\+|/|&|,|\bwith\b|\band\b)\s*")
_NOISE = re.compile(r"\b(ip|bp|usp|ph\.?\s*eur|tablets?|tabs?|capsules?|caps?|syrup|suspension|injection|inj|cream|gel|"
                    r"drops?|sr|er|xr|cr|mr|od|ds|forte)\b\.?")
_STRENGTH = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|mcg|µg|g|ml|iu|%|units?)\b(?:\s*/\s*\d*\s*(?:ml|g))?")
STRENGTH_RE = _STRENGTH


def clean_salt(text: str) -> str:
    t = (text or "").lower()
    t = _STRENGTH.sub(" ", t)
    t = _NOISE.sub(" ", t)
    t = re.sub(r"[^a-z\- ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return ALIASES.get(t, t)


def salt_parts(generic: Optional[str]) -> list:
    """'Amoxycillin + Clavulanic acid' -> ['amoxicillin', 'clavulanate'] (sorted, aliases applied)."""
    if not generic:
        return []
    parts = []
    for raw in _SPLIT.split(generic):
        p = clean_salt(raw)
        if p and p not in parts:
            parts.append(p)
    parts = sorted(parts)
    combo = COMBOS.get("+".join(parts))
    return [combo] if combo else parts


def salt_key(generic: Optional[str]) -> Optional[str]:
    parts = salt_parts(generic)
    return "+".join(parts) if parts else None


def strength_key(strength: Optional[str]) -> Optional[str]:
    """'500 mg' -> '500mg'; '500 mg + 125 mg' -> '125mg+500mg'; '0.5 MG' -> '0.5mg'."""
    if not strength:
        return None
    found = [re.sub(r"\s+", "", m.lower()).replace("µg", "mcg") for m in _STRENGTH.findall(strength)]
    if not found:
        t = re.sub(r"\s+", "", strength.lower())
        return t or None
    return "+".join(sorted(found))


def strength_mg(strength: Optional[str]) -> Optional[float]:
    """The first strength in mg (for single-salt dose checks); None when not in mg/g/mcg."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(mg|mcg|µg|g)\b", (strength or "").lower())
    if not m:
        return None
    value, unit = float(m.group(1)), m.group(2)
    return value * 1000 if unit == "g" else value / 1000 if unit in ("mcg", "µg") else value


def backfill(conn) -> int:
    """Fill salt_key / strength_key on catalogue rows that don't have them yet (idempotent)."""
    rows = conn.execute("SELECT id, generic_name, strength FROM medicines WHERE salt_key IS NULL AND generic_name IS NOT NULL").fetchall()
    for r in rows:
        conn.execute("UPDATE medicines SET salt_key = ?, strength_key = ? WHERE id = ?",
                     (salt_key(r["generic_name"]) or "", strength_key(r["strength"]), r["id"]))
    return len(rows)


def set_keys(conn, medicine_id: int):
    r = conn.execute("SELECT generic_name, strength FROM medicines WHERE id = ?", (medicine_id,)).fetchone()
    if r:
        conn.execute("UPDATE medicines SET salt_key = ?, strength_key = ? WHERE id = ?",
                     (salt_key(r["generic_name"]) or "", strength_key(r["strength"]), medicine_id))


# ---------- Catalogue matching ----------
def _norm_name(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"^\s*(tab|tablet|cap|capsule|syp|syrup|inj|injection)\.?\s+", "", t)
    t = _STRENGTH.sub(" ", t)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", t)).strip()


def best_match(conn, name: str, strength: Optional[str] = None, form: Optional[str] = None, limit: int = 3) -> list:
    """Catalogue candidates for a free-text medicine line, best first: [{medicine_id, name, ..., confidence}].

    Brand name match first (exact / prefix / fuzzy), then salt match. Strength and form break ties."""
    query = _norm_name(name)
    if not query:
        return []
    if not strength:
        m = _STRENGTH.search((name or "").lower())
        strength = m.group(0) if m else None
    want_strength = strength_key(strength)
    rows = conn.execute("SELECT id, name, generic_name, strength, form, pack_size, mrp_paise, salt_key, strength_key "
                        "FROM medicines").fetchall()
    first = query.split()[0]
    q_salt = salt_key(query)
    scored = []
    for r in rows:
        brand = _norm_name(r["name"])
        brand_first = brand.split()[0] if brand else ""
        score = 0.0
        if brand == query or brand_first == query:
            score = 1.0
        elif brand and (brand.startswith(query) or query.startswith(brand)):
            score = 0.9
        elif brand_first == first:
            score = 0.85
        else:
            score = difflib.SequenceMatcher(None, query, brand).ratio()
            score = max(score, difflib.SequenceMatcher(None, first, brand_first).ratio() * 0.95)
        if r["salt_key"] and q_salt and (r["salt_key"] == q_salt):
            score = max(score, 0.88)
        if score < 0.72:
            continue
        if want_strength:
            score += 0.08 if (r["strength_key"] or strength_key(r["strength"])) == want_strength else -0.1
        if form and r["form"] and form.lower() == r["form"].lower():
            score += 0.02
        scored.append((round(min(score, 1.0), 3), r))
    scored.sort(key=lambda s: (-s[0], s[1]["name"]))
    return [{"medicine_id": r["id"], "name": r["name"], "generic_name": r["generic_name"], "strength": r["strength"],
             "form": r["form"], "pack_size": r["pack_size"], "mrp_paise": r["mrp_paise"], "confidence": s}
            for s, r in scored[:limit]]

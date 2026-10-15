"""Health profile: blood group, allergies, long-term conditions and an emergency contact; allergy checks for doctors.

This file: logic and queries.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from fastapi import HTTPException

from shared import settings

BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
FIELDS = ("blood_group", "allergies", "chronic_conditions", "emergency_name", "emergency_phone")

# An allergy to the class name also covers these medicines (lower-case word fragments)
ALLERGY_CLASSES = {
    "penicillin": ["amoxicillin", "ampicillin", "cloxacillin", "piperacillin", "penicillin", "amoxyclav", "augmentin"],
    "sulfa": ["sulfamethoxazole", "sulfasalazine", "cotrimoxazole", "septran", "bactrim"],
    "sulpha": ["sulfamethoxazole", "sulfasalazine", "cotrimoxazole", "septran", "bactrim"],
    "nsaid": ["ibuprofen", "diclofenac", "aspirin", "naproxen", "aceclofenac", "mefenamic", "ketorolac", "etoricoxib"],
    "aspirin": ["aspirin", "ecosprin", "acetylsalicylic"],
    "cephalosporin": ["cefixime", "cefuroxime", "ceftriaxone", "cefpodoxime", "cephalexin", "cefadroxil"],
    "macrolide": ["azithromycin", "clarithromycin", "erythromycin"],
    "fluoroquinolone": ["ciprofloxacin", "levofloxacin", "ofloxacin", "moxifloxacin", "norfloxacin"],
    "quinolone": ["ciprofloxacin", "levofloxacin", "ofloxacin", "moxifloxacin", "norfloxacin"],
    "statin": ["atorvastatin", "rosuvastatin", "simvastatin"],
    "paracetamol": ["paracetamol", "acetaminophen", "crocin", "dolo", "calpol"],
}


def age_from(dob: Optional[str]) -> Optional[int]:
    if not dob:
        return None
    try:
        born = date.fromisoformat(dob)
    except ValueError:
        return None
    today = settings.now().date()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def own_profile(user) -> dict:
    user = dict(user)
    return {k: user.get(k) for k in FIELDS} | {"name": user["name"], "relation": "self", "dob": user.get("dob"),
                                               "sex": user.get("sex"), "age": age_from(user.get("dob"))}


def profile_for(conn, patient_id: int, family_member_id: Optional[int] = None) -> dict:
    """The health profile of the person being treated: the account holder or one of their family members."""
    holder = conn.execute("SELECT * FROM users WHERE id = ?", (patient_id,)).fetchone()
    if not holder:
        raise HTTPException(status_code=404, detail="Patient not found")
    if not family_member_id:
        return own_profile(holder)
    m = conn.execute("SELECT * FROM family_members WHERE id = ? AND patient_id = ?", (family_member_id, patient_id)).fetchone()
    if not m:
        raise HTTPException(status_code=404, detail="Family member not found")
    return {"name": m["name"], "relation": m["relation"], "dob": m["dob"], "sex": m["sex"], "age": age_from(m["dob"]),
            "blood_group": m["blood_group"], "allergies": m["allergies"], "chronic_conditions": m["conditions"],
            # in an emergency the account holder is who we call
            "emergency_name": holder["name"], "emergency_phone": holder["phone"]}


def allergy_words(text: Optional[str]) -> list:
    words = [w.strip().lower() for w in re.split(r"[,;/\n]+", text or "")]
    return [w for w in words if len(w) >= 3]


def allergy_warnings(allergies: Optional[str], items: list, who: str = "The patient") -> list:
    """Messages for medicines that match a recorded allergy (by name, generic name or drug class)."""
    warnings = []
    for word in allergy_words(allergies):
        fragments = set(ALLERGY_CLASSES.get(word, [])) | {word}
        for word_part in word.split():
            fragments |= set(ALLERGY_CLASSES.get(word_part, []))
        for item in items:
            text = " ".join(str(item.get(k) or "") for k in ("name", "generic_name")).lower()
            if text and any(f in text for f in fragments if len(f) >= 3):
                warnings.append(f"{who} is allergic to “{word}”: check {item.get('name') or item.get('generic_name')}")
    return list(dict.fromkeys(warnings))


def doctor_sees_patient(conn, doctor_id: int, patient_id: int) -> bool:
    """A doctor sees the health profile of patients who booked them (not of cancelled-only or expired bookings)."""
    return bool(conn.execute("SELECT 1 FROM appointments WHERE doctor_id = ? AND patient_id = ? AND status IN "
                             "('requested', 'awaiting_patient', 'pending_payment', 'confirmed', 'completed', 'no_show') "
                             "LIMIT 1", (doctor_id, patient_id)).fetchone())

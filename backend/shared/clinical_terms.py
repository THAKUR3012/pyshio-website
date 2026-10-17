"""Matching free text to the lab-test catalogue and the diagnosis (ICD-10) list.

Used by the AI scribe, the prescription photo reader and the lab report reader. Deterministic (difflib, stdlib).
"""
from __future__ import annotations

import difflib
import re
from typing import Optional

# Shorthand doctors and labs write -> lab_tests.code
LAB_ALIASES = {
    "cbc": "CBC", "hemogram": "CBC", "haemogram": "CBC", "complete blood count": "CBC",
    "hb": "HB", "hgb": "HB", "haemoglobin": "HB", "hemoglobin": "HB",
    "hba1c": "HBA1C", "a1c": "HBA1C", "glycated haemoglobin": "HBA1C", "glycosylated haemoglobin": "HBA1C",
    "fbs": "FBS", "fasting sugar": "FBS", "fasting blood sugar": "FBS", "fasting glucose": "FBS", "fasting plasma glucose": "FBS",
    "ppbs": "PPBS", "pp sugar": "PPBS", "post prandial": "PPBS", "postprandial glucose": "PPBS",
    "rbs": "RBS", "random sugar": "RBS", "random blood sugar": "RBS",
    "lipid": "LIPID", "lipid profile": "LIPID", "lipids": "LIPID",
    "cholesterol": "CHOL", "total cholesterol": "CHOL", "ldl": "LDL", "ldl cholesterol": "LDL",
    "tg": "TG", "triglyceride": "TG", "triglycerides": "TG",
    "lft": "LFT", "liver function": "LFT", "sgpt": "ALT", "alt": "ALT",
    "kft": "KFT", "rft": "KFT", "renal function": "KFT", "kidney function": "KFT",
    "creatinine": "CREAT", "s creatinine": "CREAT", "serum creatinine": "CREAT", "urea": "UREA", "blood urea": "UREA",
    "uric acid": "URIC", "s uric acid": "URIC",
    "tft": "THY", "thyroid profile": "THY", "t3 t4 tsh": "THY", "tsh": "TSH",
    "vit d": "VITD", "vitamin d": "VITD", "25 oh vitamin d": "VITD", "vit b12": "B12", "vitamin b12": "B12", "b12": "B12",
    "iron studies": "IRON", "esr": "ESR", "crp": "CRP", "c reactive protein": "CRP",
    "ns1": "NS1", "dengue ns1": "NS1", "dengue igm": "DENIG", "dengue serology": "DENIG",
    "malaria": "MAL", "mp": "MAL", "malaria antigen": "MAL", "widal": "WIDAL",
    "platelet": "PLT", "platelets": "PLT", "platelet count": "PLT",
    "urine re": "URINE", "urine r m": "URINE", "urine routine": "URINE", "urine culture": "UCS",
    "stool": "STOOL", "stool routine": "STOOL", "psa": "PSA", "beta hcg": "BHCG", "upt": "BHCG",
    "ecg": "ECG", "2d echo": "ECHO", "echo": "ECHO", "cxr": "CXR", "chest xray": "CXR", "chest x ray": "CXR",
    "x ray knee": "XKNEE", "xray knee": "XKNEE", "x ray ls spine": "XLS", "x ray lumbar spine": "XLS",
    "usg abdomen": "USGA", "usg": "USGA", "ultrasound abdomen": "USGA", "usg pelvis": "USGP", "ultrasound pelvis": "USGP",
    "dexa": "DEXA", "bmd": "DEXA", "mri ls spine": "MRILS",
}

# Shorthand for common diagnoses -> ICD-10 code in the conditions list
CONDITION_ALIASES = {
    "t2dm": "E11.9", "dm2": "E11.9", "dm": "E11.9", "diabetes": "E11.9", "type 2 diabetes": "E11.9", "niddm": "E11.9",
    "t1dm": "E10.9", "htn": "I10", "hypertension": "I10", "high bp": "I10",
    "cad": "I25.1", "ihd": "I25.1", "dyslipidemia": "E78.5", "dyslipidaemia": "E78.5", "hypothyroid": "E03.9",
    "hypothyroidism": "E03.9", "hyperthyroid": "E05.9", "urti": "J06.9", "uri": "J06.9", "viral fever": "B34.9",
    "fever": "R50.9", "uti": "N39.0", "gastroenteritis": "A09", "aom": "H66.9",
    "gerd": "K21.9", "acid reflux": "K21.9", "lbp": "M54.5", "low back pain": "M54.5", "back pain": "M54.5",
    "oa knee": "M17.9", "knee oa": "M17.9", "asthma": "J45.9", "copd": "J44.9", "migraine": "G43.9",
    "anemia": "D50.9", "anaemia": "D50.9", "ida": "D50.9", "dengue": "A90", "typhoid": "A01.0", "malaria": "B54",
    "pregnancy": "Z34.9", "anc": "Z34.9", "ckd": "N18.9", "gout": "M10.9", "bph": "N40", "pcos": "E28.2",
    "allergic rhinitis": "J30.4", "obesity": "E66.9", "vit d deficiency": "E55.9", "b12 deficiency": "E53.8",
    "fatty liver": "K76.0", "nafld": "K76.0", "anxiety": "F41.9", "depression": "F32.9", "insomnia": "G47.0",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())).strip()


def match_test(conn, text: str, cutoff: float = 0.82) -> Optional[dict]:
    """The catalogue test for free text such as 'CBC', 'S. Creatinine' or 'HbA1c (glycated haemoglobin)'."""
    q = _norm(text)
    if not q:
        return None
    rows = conn.execute("SELECT id, name, code, unit, ref_low, ref_high FROM lab_tests").fetchall()
    by_code = {r["code"].upper(): r for r in rows if r["code"]}
    code = LAB_ALIASES.get(q) or LAB_ALIASES.get(re.sub(r"^(s|serum)\s+", "", q))
    if not code and q.upper() in by_code:
        code = q.upper()
    if code and code in by_code:
        return dict(by_code[code])
    for r in rows:  # "(CBC)" inside the catalogue name
        if _norm(r["name"]) == q:
            return dict(r)
    names = {_norm(r["name"]): r for r in rows}
    close = difflib.get_close_matches(q, list(names) + list(LAB_ALIASES), n=1, cutoff=cutoff)
    if close:
        hit = names.get(close[0]) or by_code.get(LAB_ALIASES.get(close[0], ""))
        return dict(hit) if hit else None
    return None


def match_condition(conn, text: str, cutoff: float = 0.8) -> Optional[dict]:
    """The diagnosis for an ICD-10 code, a common abbreviation or a name."""
    raw = (text or "").strip()
    if not raw:
        return None
    rows = conn.execute("SELECT id, code, name, care_area FROM conditions").fetchall()
    by_code = {r["code"].upper(): r for r in rows}
    m = re.fullmatch(r"([A-Za-z]\d{2}(?:\.\d{1,2})?)", raw)
    if m and m.group(1).upper() in by_code:
        return dict(by_code[m.group(1).upper()])
    q = _norm(raw)
    code = CONDITION_ALIASES.get(q)
    if code and code in by_code:
        return dict(by_code[code])
    for r in rows:
        name = _norm(r["name"])
        if q == name or q == _norm(re.sub(r"\(.*?\)", "", r["name"])):
            return dict(r)
    names = {_norm(re.sub(r"\(.*?\)", "", r["name"])): r for r in rows}
    close = difflib.get_close_matches(q, list(names), n=1, cutoff=cutoff)
    return dict(names[close[0]]) if close else None

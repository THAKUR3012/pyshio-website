"""Admin analytics: disease, age, city, state and medicine analysis for the super admin.

This file: logic and queries.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from fastapi import HTTPException
from typing import Literal, Optional

from shared import reference_data as ref, settings, validators as v


SEXES = ["M", "F", "O", "Unknown"]


def mask(n: int):
    return n if n == 0 or n >= settings.MIN_GROUP_SIZE else f"<{settings.MIN_GROUP_SIZE}"


def visible(n: int) -> bool:
    return n == 0 or n >= settings.MIN_GROUP_SIZE


def age_band(age) -> str:
    if age is None or age < 0:
        return "Unknown"
    for label, low, high in ref.AGE_BANDS:
        if low <= age <= high:
            return label
    return "Unknown"


def band_labels() -> list:
    return [b[0] for b in ref.AGE_BANDS] + ["Unknown"]


class Filters:
    def __init__(self, date_from=None, date_to=None, state=None, city=None, care_area=None, condition_id=None,
                 medicine=None, age=None, sex=None):
        today = settings.now().date()
        self.date_from = v.iso_date(date_from) if date_from else (today - timedelta(days=365)).isoformat()
        self.date_to = v.iso_date(date_to) if date_to else today.isoformat()
        if self.date_from > self.date_to:
            raise HTTPException(status_code=400, detail="'From' date must be before 'to' date")
        try:
            self.state = v.state(state) if state else None
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        self.city = (city or "").strip() or None
        if care_area and care_area not in ref.CARE_AREAS:
            raise HTTPException(status_code=400, detail="Unknown care area")
        self.care_area = care_area or None
        self.condition_id = condition_id
        self.medicine = (medicine or "").strip().lower() or None
        if age and age not in band_labels():
            raise HTTPException(status_code=400, detail="Unknown age band")
        self.age = age or None
        self.sex = sex or None

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("date_from", "date_to", "state", "city", "care_area", "condition_id",
                                              "medicine", "age", "sex")}


def filters(date_from: Optional[str] = None, date_to: Optional[str] = None, state: Optional[str] = None,
            city: Optional[str] = None, care_area: Optional[str] = None, condition_id: Optional[int] = None,
            medicine: Optional[str] = None, age: Optional[str] = None,
            sex: Optional[Literal["M", "F", "O", "Unknown"]] = None) -> Filters:
    try:
        return Filters(date_from, date_to, state, city, care_area, condition_id, medicine, age, sex)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def load(conn, f: Filters) -> dict:
    """The prescriptions matching the filters, with their diagnoses and medicines (no names or contacts)."""
    sql = """SELECT r.id, r.patient_id, r.doctor_id, r.parent_id, r.created_at,
                    COALESCE(r.patient_age, CASE WHEN pu.dob IS NOT NULL THEN
                        CAST((julianday(substr(r.created_at, 1, 10)) - julianday(pu.dob)) / 365.25 AS INTEGER) END) AS age,
                    COALESCE(r.patient_sex, pu.sex) AS sex,
                    COALESCE(pu.state, d.state) AS state, COALESCE(pu.city, d.city) AS city
             FROM prescriptions r JOIN users pu ON pu.id = r.patient_id JOIN doctors d ON d.user_id = r.doctor_id
             WHERE r.status != 'cancelled' AND r.created_at >= ? AND r.created_at < ?"""
    end = (datetime.fromisoformat(f.date_to) + timedelta(days=1)).date().isoformat()
    params = [f.date_from, end]
    if f.state:
        sql += " AND COALESCE(pu.state, d.state) = ?"
        params.append(f.state)
    if f.city:
        sql += " AND lower(COALESCE(pu.city, d.city)) = lower(?)"
        params.append(f.city)
    if f.sex:
        if f.sex == "Unknown":
            sql += " AND COALESCE(r.patient_sex, pu.sex) IS NULL"
        else:
            sql += " AND COALESCE(r.patient_sex, pu.sex) = ?"
            params.append(f.sex)
    root = "COALESCE(r.parent_id, r.id)"  # refills carry their original prescription's diagnoses
    if f.condition_id:
        sql += f" AND EXISTS (SELECT 1 FROM prescription_conditions pc WHERE pc.prescription_id = {root} " \
               "AND pc.condition_id = ?)"
        params.append(f.condition_id)
    if f.care_area:
        sql += f""" AND (EXISTS (SELECT 1 FROM prescription_conditions pc JOIN conditions c ON c.id = pc.condition_id
                                WHERE pc.prescription_id = {root} AND c.care_area = ?)
                         OR EXISTS (SELECT 1 FROM prescription_items i JOIN medicines m ON m.id = i.medicine_id
                                    WHERE i.prescription_id = r.id AND m.care_area = ?))"""
        params += [f.care_area, f.care_area]
    if f.medicine:
        like = f"%{f.medicine}%"
        sql += """ AND EXISTS (SELECT 1 FROM prescription_items i LEFT JOIN medicines m ON m.id = i.medicine_id
                              WHERE i.prescription_id = r.id AND (lower(i.name) LIKE ?
                                    OR lower(COALESCE(m.generic_name, i.generic_name, '')) LIKE ?))"""
        params += [like, like]
    rx = [dict(r) for r in conn.execute(sql, params)]
    for r in rx:
        r["band"] = age_band(r["age"])
        r["sex"] = r["sex"] or "Unknown"
        r["month"] = r["created_at"][:7]
    if f.age:
        rx = [r for r in rx if r["band"] == f.age]

    by_id = {r["id"]: r for r in rx}
    roots = {r["parent_id"] or r["id"] for r in rx}
    conditions, items = {}, {}
    root_ids, ids = list(roots), list(by_id)
    for chunk in range(0, len(root_ids), 500):
        part = root_ids[chunk:chunk + 500]
        for c in conn.execute("SELECT pc.prescription_id, c.id, c.code, c.name, c.care_area FROM prescription_conditions pc "
                              f"JOIN conditions c ON c.id = pc.condition_id WHERE pc.prescription_id IN "
                              f"({','.join('?' * len(part))})", part):
            conditions.setdefault(c["prescription_id"], []).append(dict(c))
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        for i in conn.execute("SELECT i.prescription_id, i.quantity, COALESCE(m.name, i.name) AS name, "
                              "COALESCE(m.generic_name, i.generic_name, i.name) AS generic, "
                              "COALESCE(m.strength, i.strength) AS strength, m.care_area, m.manufacturer "
                              "FROM prescription_items i LEFT JOIN medicines m ON m.id = i.medicine_id "
                              f"WHERE i.prescription_id IN ({','.join('?' * len(part))})", part):
            items.setdefault(i["prescription_id"], []).append(dict(i))
    tests = {}
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        for t in conn.execute("SELECT pt.prescription_id, pt.name, lt.category FROM prescription_tests pt "
                              "LEFT JOIN lab_tests lt ON lt.id = pt.test_id "
                              f"WHERE pt.prescription_id IN ({','.join('?' * len(part))})", part):
            tests.setdefault(t["prescription_id"], []).append(dict(t))
    for r in rx:
        r["conditions"] = conditions.get(r["parent_id"] or r["id"], [])
        r["items"] = items.get(r["id"], [])
        r["tests"] = tests.get(r["id"], [])
    return {"rx": rx, "consults": [r for r in rx if not r["parent_id"]]}


def top_condition(rows) -> Optional[str]:
    counts = {}
    for r in rows:
        for c in r["conditions"]:
            counts[c["name"]] = counts.get(c["name"], 0) + 1
    if not counts:
        return None
    name, n = max(counts.items(), key=lambda x: (x[1], x[0]))
    return name if visible(n) and n else None


def build(conn, f: Filters) -> dict:
    data = load(conn, f)
    rx, consults = data["rx"], data["consults"]
    coded = [r for r in consults if r["conditions"]]

    # Disease
    cond = {}
    for r in consults:
        for c in r["conditions"]:
            entry = cond.setdefault(c["id"], {**{k: c[k] for k in ("id", "code", "name", "care_area")},
                                              "cases": 0, "patients": set(), "months": {}})
            entry["cases"] += 1
            entry["patients"].add(r["patient_id"])
            entry["months"][r["month"]] = entry["months"].get(r["month"], 0) + 1
    cond_rows = sorted(cond.values(), key=lambda c: (-c["cases"], c["name"]))
    conditions = [{k: c[k] for k in ("id", "code", "name", "care_area")} | {
        "cases": mask(c["cases"]), "patients": mask(len(c["patients"])),
        "percent": round(100 * c["cases"] / len(coded), 1) if coded and visible(c["cases"]) and c["cases"] else None}
        for c in cond_rows[:30]]

    areas = {}
    for r in consults:
        for a in {c["care_area"] for c in r["conditions"]}:
            areas[a] = areas.get(a, 0) + 1
    care_areas = [{"care_area": a, "cases": mask(n)} for a, n in sorted(areas.items(), key=lambda x: -x[1])]

    # Age x sex
    grid = {(b, s): 0 for b in band_labels() for s in SEXES}
    for r in consults:
        grid[(r["band"], r["sex"] if r["sex"] in SEXES else "Unknown")] += 1
    age = [{"band": b, **{s: mask(grid[(b, s)]) for s in SEXES},
            "total": mask(sum(grid[(b, s)] for s in SEXES))} for b in band_labels()]

    # Geography
    states, cities = {}, {}
    for r in consults:
        states.setdefault(r["state"] or "Unknown", []).append(r)
        cities.setdefault((r["city"] or "Unknown", r["state"] or "Unknown"), []).append(r)
    state_rows = [{"state": k, "cases": mask(len(rows)), "patients": mask(len({x["patient_id"] for x in rows})),
                   "top_condition": top_condition(rows)} for k, rows in sorted(states.items(), key=lambda x: -len(x[1]))]
    city_rows = [{"city": k[0], "state": k[1], "cases": mask(len(rows)),
                  "patients": mask(len({x["patient_id"] for x in rows})), "top_condition": top_condition(rows)}
                 for k, rows in sorted(cities.items(), key=lambda x: -len(x[1]))[:40]]

    # Medicines (all prescriptions, refills included)
    meds, generics = {}, {}
    for r in rx:
        for i in r["items"]:
            m = meds.setdefault((i["name"], i["strength"]), {"name": i["name"], "generic_name": i["generic"],
                                                             "strength": i["strength"], "care_area": i["care_area"],
                                                             "manufacturer": i["manufacturer"], "rx": set(), "units": 0})
            m["rx"].add(r["id"])
            m["units"] += i["quantity"]
            g = generics.setdefault((i["generic"] or "").lower(), {"generic_name": i["generic"],
                                                                     "care_area": i["care_area"], "rx": set(),
                                                                     "units": 0, "brands": set()})
            g["rx"].add(r["id"])
            g["units"] += i["quantity"]
            g["brands"].add(i["name"])
    medicines = [{k: m[k] for k in ("name", "generic_name", "strength", "care_area", "manufacturer")}
                 | {"prescriptions": mask(len(m["rx"])), "units": mask(m["units"])}
                 for m in sorted(meds.values(), key=lambda m: (-len(m["rx"]), m["name"]))[:30]]
    generic_rows = sorted(generics.values(), key=lambda g: (-len(g["rx"]), g["generic_name"] or ""))
    generic_list = [{"generic_name": g["generic_name"], "care_area": g["care_area"], "brands": len(g["brands"]),
                     "prescriptions": mask(len(g["rx"])), "units": mask(g["units"])} for g in generic_rows[:30]]

    # Lab tests doctors ordered
    test_counts = {}
    for r in rx:
        for t in r["tests"]:
            e = test_counts.setdefault(t["name"], {"name": t["name"], "category": t["category"], "rx": 0})
            e["rx"] += 1
    test_rows = [{"name": e["name"], "category": e["category"], "prescriptions": mask(e["rx"])}
                 for e in sorted(test_counts.values(), key=lambda e: (-e["rx"], e["name"]))[:30]]

    # Trend by month
    months = {}
    for r in rx:
        m = months.setdefault(r["month"], {"rx": 0, "consults": 0, "patients": set()})
        m["rx"] += 1
        m["patients"].add(r["patient_id"])
        if not r["parent_id"]:
            m["consults"] += 1
    trend = [{"month": k, "prescriptions": mask(m["rx"]), "consultations": mask(m["consults"]),
              "patients": mask(len(m["patients"]))} for k, m in sorted(months.items())]

    # Cross views
    top_ids = [c["id"] for c in cond_rows[:10]]
    cross_age = []
    for cid in top_ids:
        counts = {b: 0 for b in band_labels()}
        for r in consults:
            if any(c["id"] == cid for c in r["conditions"]):
                counts[r["band"]] += 1
        cross_age.append({"condition": cond[cid]["name"], **{b: mask(n) for b, n in counts.items()}})
    top_states = [k for k, _ in sorted(states.items(), key=lambda x: -len(x[1]))[:8]]
    cross_state = []
    for g in generic_rows[:10]:
        counts = {s: 0 for s in top_states}
        for r in rx:
            if (r["state"] or "Unknown") in counts and any((i["generic"] or "").lower() == (g["generic_name"] or "").lower()
                                                           for i in r["items"]):
                counts[r["state"] or "Unknown"] += 1
        cross_state.append({"generic_name": g["generic_name"], **{s: mask(n) for s, n in counts.items()}})

    top_med = medicines[0]["name"] if medicines and visible(len(meds[(medicines[0]["name"], medicines[0]["strength"])]["rx"])) \
        else None
    summary = {"prescriptions": mask(len(rx)), "consultations": mask(len(consults)),
               "refills": mask(len(rx) - len(consults)),
               "patients": mask(len({r["patient_id"] for r in rx})), "doctors": len({r["doctor_id"] for r in rx}),
               "coded_percent": round(100 * len(coded) / len(consults), 1) if consults else None,
               "top_condition": cond_rows[0]["name"] if cond_rows and visible(cond_rows[0]["cases"]) else None,
               "top_medicine": top_med,
               "states": len([s for s in states if s != "Unknown"]),
               "cities": len([c for c in cities if c[0] != "Unknown"])}
    return {"filters": f.as_dict(), "min_group_size": settings.MIN_GROUP_SIZE, "summary": summary,
            "conditions": conditions, "care_areas": care_areas, "age": age, "sexes": SEXES,
            "states": state_rows, "cities": city_rows, "medicines": medicines, "generics": generic_list,
            "trend": trend, "tests": test_rows, "condition_by_age": cross_age, "age_bands": band_labels(),
            "medicine_by_state": cross_state, "top_states": top_states}


EXPORTS = {
    "conditions": ("conditions", ["code", "name", "care_area", "cases", "patients", "percent"]),
    "care-areas": ("care_areas", ["care_area", "cases"]),
    "age": ("age", ["band", "M", "F", "O", "Unknown", "total"]),
    "states": ("states", ["state", "cases", "patients", "top_condition"]),
    "cities": ("cities", ["city", "state", "cases", "patients", "top_condition"]),
    "medicines": ("medicines", ["name", "generic_name", "strength", "care_area", "manufacturer", "prescriptions",
                                "units"]),
    "generics": ("generics", ["generic_name", "care_area", "brands", "prescriptions", "units"]),
    "trend": ("trend", ["month", "prescriptions", "consultations", "patients"]),
    "tests": ("tests", ["name", "category", "prescriptions"]),
}

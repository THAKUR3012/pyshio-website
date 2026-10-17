"""Pharma insights: companies, divisions, MRs, brands, MR meetings and sales / demand insights.

This file: logic and queries.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta
from fastapi import HTTPException
from typing import List, Optional

from shared import reference_data as ref, settings
from shared.common import stamp
from shared.db import get_db


def care_area_list(value: List[str]) -> List[str]:
    unknown = [a for a in value if a not in ref.CARE_AREAS]
    if unknown:
        raise ValueError(f"Unknown care area: {', '.join(unknown)}")
    return list(dict.fromkeys(value))


# ---------- Helpers ----------
def mask(n: int):
    """Small groups are hidden so no individual can be singled out."""
    return n if n == 0 or n >= settings.MIN_GROUP_SIZE else f"<{settings.MIN_GROUP_SIZE}"


def split_areas(text: str) -> list:
    return [a for a in (text or "").split("|") if a]


def company_view(row) -> dict:
    return {k: row[k] for k in ("id", "name", "cin", "gstin", "address", "city", "state", "website", "status",
                                "status_reason", "created_at", "approved_at")}


def company_for(conn, user: dict, approved: bool = False):
    row = conn.execute("SELECT * FROM companies WHERE admin_id = ?", (user["id"],)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Company not found")
    if approved and row["status"] != "approved":
        raise HTTPException(status_code=403, detail="Your company must be approved first")
    return row


MR_SELECT = """SELECT m.*, u.name, u.email, u.phone, u.is_active, u.last_login_at, c.name AS company_name,
                      c.status AS company_status, dv.name AS division_name, dv.care_areas
               FROM mrs m JOIN users u ON u.id = m.user_id JOIN companies c ON c.id = m.company_id
               LEFT JOIN divisions dv ON dv.id = m.division_id"""


def mr_view(row) -> dict:
    return {"id": row["user_id"], "name": row["name"], "email": row["email"], "phone": row["phone"],
            "active": bool(row["is_active"]), "employee_code": row["employee_code"],
            "division_id": row["division_id"], "division": row["division_name"],
            "care_areas": split_areas(row["care_areas"]), "territory_city": row["territory_city"],
            "territory_state": row["territory_state"], "company": row["company_name"],
            "last_login_at": row["last_login_at"], "created_at": row["created_at"]}


def mr_for(conn, user: dict):
    """The MR's profile; MRs of companies that aren't approved can't use the platform."""
    row = conn.execute(MR_SELECT + " WHERE m.user_id = ?", (user["id"],)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="MR profile not found")
    if row["company_status"] != "approved":
        raise HTTPException(status_code=403, detail="Your company's account isn't active")
    return row


def me_extra(user: dict) -> dict:
    with get_db() as conn:
        if user["role"] == "company":
            row = conn.execute("SELECT * FROM companies WHERE admin_id = ?", (user["id"],)).fetchone()
            return {"company": company_view(row) if row else None}
        row = conn.execute(MR_SELECT + " WHERE m.user_id = ?", (user["id"],)).fetchone()
        return {"mr": {**mr_view(row), "company_status": row["company_status"]} if row else None}


def admin_emails(conn) -> list:
    return [r["email"] for r in conn.execute("SELECT email FROM users WHERE role = 'admin' AND is_active = 1")]


def division_view(row) -> dict:
    return {"id": row["id"], "name": row["name"], "care_areas": split_areas(row["care_areas"]),
            "active": bool(row["active"]), "mrs": row["mrs"] if "mrs" in row.keys() else None}


def check_division(conn, company_id: int, division_id: Optional[int]) -> None:
    if division_id and not conn.execute("SELECT 1 FROM divisions WHERE id = ? AND company_id = ? AND active = 1",
                                        (division_id, company_id)).fetchone():
        raise HTTPException(status_code=400, detail="Choose one of your active divisions")


# ---------- Sales & prescribing insights ----------
# Doctors who switched on "share my prescribing insights" are shown by name. Every other doctor appears under a
# pseudonym that is stable for one company (HMAC with a platform secret, so it can't be reversed), with only their
# category and city. Where fewer than settings.MIN_ANON_DOCTORS non-sharing doctors of a category practise in a city,
# they are pooled into one row. Patients never appear: the queries read medicines, doctors, dates and diagnoses only.
def platform_secret(conn) -> bytes:
    row = conn.execute("SELECT value FROM app_secrets WHERE key = 'doctor_pseudonyms'").fetchone()
    if not row:
        conn.execute("INSERT OR IGNORE INTO app_secrets (key, value) VALUES ('doctor_pseudonyms', ?)", (secrets.token_hex(32),))
        row = conn.execute("SELECT value FROM app_secrets WHERE key = 'doctor_pseudonyms'").fetchone()
    return bytes.fromhex(row["value"])


def pseudonym(secret: bytes, company_id: int, doctor_id: int) -> str:
    digest = hmac.new(secret, f"{company_id}:{doctor_id}".encode(), hashlib.sha256).digest()
    return "Doctor " + base64.b32encode(digest).decode()[:4]


def scope_doctors(conn, state: str = None, city: str = None):
    """Every approved, active doctor in the area (named or not)."""
    sql = ("SELECT d.user_id, u.name, d.clinic_name, d.city, d.state, d.specialities, d.category, d.share_insights_at "
           "FROM doctors d JOIN users u ON u.id = d.user_id WHERE d.status = 'approved' AND u.is_active = 1")
    params = []
    if state:
        sql += " AND d.state = ?"
        params.append(state)
    if city:
        sql += " AND lower(d.city) = lower(?)"
        params.append(city)
    return conn.execute(sql + " ORDER BY u.name", params).fetchall()


def doctor_identities(conn, company_id: int, doctors: list) -> dict:
    """{doctor_id: {key, label, named, category, city, ...}} with pooling for small anonymous groups."""
    secret = platform_secret(conn)
    anon_groups = {}
    for d in doctors:
        if not d["share_insights_at"]:
            anon_groups.setdefault((d["category"], d["city"]), []).append(d["user_id"])
    out = {}
    for d in doctors:
        category = settings.DOCTOR_CATEGORIES.get(d["category"], {}).get("label", d["category"])
        if d["share_insights_at"]:
            out[d["user_id"]] = {"key": f"d{d['user_id']}", "label": d["name"], "named": True, "category": category,
                                 "city": d["city"], "clinic_name": d["clinic_name"], "doctor_id": d["user_id"],
                                 "speciality": ", ".join(split_areas(d["specialities"]))[:80]}
        elif len(anon_groups[(d["category"], d["city"])]) < settings.MIN_ANON_DOCTORS:
            out[d["user_id"]] = {"key": f"pool:{d['category']}:{d['city']}", "label": f"Other {category.lower()}s",
                                 "named": False, "pooled": True, "category": category, "city": d["city"]}
        else:
            name = pseudonym(secret, company_id, d["user_id"])
            out[d["user_id"]] = {"key": name, "label": name, "named": False, "category": category, "city": d["city"]}
    return out


def build_insights(conn, company_id: int, doctors: list, care_areas: Optional[list], days: int,
                   product_id: Optional[int] = None) -> dict:
    since = stamp(settings.now() - timedelta(days=days))
    who = doctor_identities(conn, company_id, doctors)
    areas = set(care_areas) if care_areas else None
    ids = list(who)
    own_meds = {r["id"]: r for r in conn.execute("SELECT * FROM medicines WHERE company_id = ?", (company_id,))}
    if areas:
        own_meds = {k: m for k, m in own_meds.items() if m["care_area"] in areas}
    if product_id:
        own_meds = {k: m for k, m in own_meds.items() if k == product_id}
    own_salts = {((m["generic_name"] or m["name"]).lower(), (m["strength"] or "").lower()) for m in own_meds.values()}

    def salt(r):
        return ((r["generic"] or r["name"]).lower(), (r["strength"] or "").lower())

    rows, sales = [], []
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        marks = ",".join("?" * len(part))
        rows += conn.execute(
            "SELECT i.prescription_id, i.quantity, i.medicine_id, COALESCE(m.name, i.name) AS name, "
            "COALESCE(m.generic_name, i.generic_name) AS generic, COALESCE(m.strength, i.strength) AS strength, "
            "m.care_area, m.company_id, m.manufacturer, r.doctor_id, COALESCE(r.parent_id, r.id) AS root_id, "
            "substr(r.created_at, 1, 7) AS month FROM prescription_items i JOIN prescriptions r ON r.id = i.prescription_id "
            f"LEFT JOIN medicines m ON m.id = i.medicine_id WHERE r.status != 'cancelled' AND r.created_at >= ? "
            f"AND r.doctor_id IN ({marks})", (since, *part)).fetchall()
        if own_meds:
            sales += conn.execute(
                "SELECT oi.medicine_id, oi.quantity, oi.line_paise, substr(o.updated_at, 1, 7) AS month, r.doctor_id, "
                "p.city AS pharmacy_city, p.state AS pharmacy_state FROM order_items oi "
                "JOIN medicine_orders o ON o.id = oi.order_id JOIN prescriptions r ON r.id = o.prescription_id "
                "JOIN pharmacies p ON p.user_id = o.pharmacy_id "
                f"WHERE o.status = 'completed' AND o.updated_at >= ? AND r.doctor_id IN ({marks}) "
                f"AND oi.medicine_id IN ({','.join('?' * len(own_meds))})", (since, *part, *own_meds)).fetchall()
    in_area = [r for r in rows if not areas or r["care_area"] in areas]
    market = [r for r in rows if salt(r) in own_salts]           # every brand of the salts this company makes
    own = [r for r in rows if r["medicine_id"] in own_meds]
    conditions = {}
    roots = list({r["root_id"] for r in market})
    for chunk in range(0, len(roots), 500):
        part = roots[chunk:chunk + 500]
        for c in conn.execute("SELECT pc.prescription_id, c.name, c.code, c.care_area FROM prescription_conditions pc "
                              f"JOIN conditions c ON c.id = pc.condition_id WHERE pc.prescription_id IN ({','.join('?' * len(part))})",
                              part):
            conditions.setdefault(c["prescription_id"], []).append(dict(c))

    # Products
    products = {}
    for m in own_meds.values():
        products[m["id"]] = {"id": m["id"], "name": m["name"], "generic_name": m["generic_name"], "strength": m["strength"],
                             "care_area": m["care_area"], "rx": set(), "units": 0, "units_sold": 0, "sales_paise": 0,
                             "conds": {}, "doctors": set()}
    salt_rx = {}
    for r in market:
        salt_rx.setdefault(salt(r), set()).add(r["prescription_id"])
    for r in own:
        p = products[r["medicine_id"]]
        p["rx"].add(r["prescription_id"])
        p["units"] += r["quantity"]
        p["doctors"].add(r["doctor_id"])
        for c in conditions.get(r["root_id"], []):
            p["conds"][c["name"]] = p["conds"].get(c["name"], 0) + 1
    for s in sales:
        products[s["medicine_id"]]["units_sold"] += s["quantity"]
        products[s["medicine_id"]]["sales_paise"] += s["line_paise"]
    product_rows = []
    for p in sorted(products.values(), key=lambda p: (-p["sales_paise"], -len(p["rx"]), p["name"])):
        m = own_meds[p["id"]]
        market_n = len(salt_rx.get(salt({"generic": m["generic_name"], "name": m["name"], "strength": m["strength"]}), ()))
        product_rows.append({
            **{k: p[k] for k in ("id", "name", "generic_name", "strength", "care_area", "units", "units_sold", "sales_paise")},
            "prescriptions": len(p["rx"]), "doctors": len(p["doctors"]),
            "conversion_percent": round(100 * p["units_sold"] / p["units"], 1) if p["units"] else None,
            "salt_market_prescriptions": market_n,
            "salt_share_percent": round(100 * len(p["rx"]) / market_n, 1) if market_n else None,
            "top_conditions": [{"name": n, "prescriptions": mask(c)} for n, c in
                               sorted(p["conds"].items(), key=lambda x: -x[1])[:3]]})

    # Diseases the company's salts are prescribed for
    cond_rows = {}
    for r in market:
        for c in conditions.get(r["root_id"], []):
            e = cond_rows.setdefault(c["name"], {"condition": c["name"], "code": c["code"], "care_area": c["care_area"],
                                                 "own": set(), "market": set(), "products": {}})
            e["market"].add(r["prescription_id"])
            if r["medicine_id"] in own_meds:
                e["own"].add(r["prescription_id"])
                e["products"][r["name"]] = e["products"].get(r["name"], 0) + 1
    disease_rows = [{"condition": e["condition"], "code": e["code"], "care_area": e["care_area"],
                     "own_prescriptions": mask(len(e["own"])), "market_prescriptions": mask(len(e["market"])),
                     "own_share_percent": round(100 * len(e["own"]) / len(e["market"]), 1)
                     if len(e["market"]) >= settings.MIN_GROUP_SIZE else None,
                     "top_product": max(e["products"].items(), key=lambda x: x[1])[0] if e["products"] else None}
                    for e in sorted(cond_rows.values(), key=lambda e: (-len(e["own"]), -len(e["market"])))][:30]

    # Doctor by doctor (named or anonymous)
    doc = {}
    for r in market:
        ident = who[r["doctor_id"]]
        d = doc.setdefault(ident["key"], {**ident, "own_rx": set(), "own_units": 0, "market_rx": set(), "products": {},
                                          "competitors": {}, "members": set()})
        d["members"].add(r["doctor_id"])
        d["market_rx"].add(r["prescription_id"])
        if r["medicine_id"] in own_meds:
            d["own_rx"].add(r["prescription_id"])
            d["own_units"] += r["quantity"]
            pr = d["products"].setdefault(r["name"], {"name": r["name"], "prescriptions": 0, "units": 0})
            pr["prescriptions"] += 1
            pr["units"] += r["quantity"]
        else:
            d["competitors"][r["name"]] = d["competitors"].get(r["name"], 0) + 1
    doctor_rows = []
    for d in sorted(doc.values(), key=lambda d: (-len(d["own_rx"]), -len(d["market_rx"]), d["label"])):
        top_comp = max(d["competitors"].items(), key=lambda x: x[1])[0] if d["competitors"] else None
        doctor_rows.append({k: d.get(k) for k in ("key", "label", "named", "category", "city", "clinic_name", "speciality",
                                                   "doctor_id", "pooled")}
                           | {"doctors": len(d["members"]), "own_prescriptions": len(d["own_rx"]), "own_units": d["own_units"],
                              "market_prescriptions": len(d["market_rx"]),
                              "share_percent": round(100 * len(d["own_rx"]) / len(d["market_rx"]), 1) if d["market_rx"] else None,
                              "products": sorted(d["products"].values(), key=lambda x: -x["prescriptions"]),
                              "top_competitor": top_comp})

    # Trend, sales by city, care areas, market leaders
    months = {}
    for r in own:
        months.setdefault(r["month"], {"rx": set(), "sold": 0, "sales": 0})["rx"].add(r["prescription_id"])
    for s in sales:
        m = months.setdefault(s["month"], {"rx": set(), "sold": 0, "sales": 0})
        m["sold"] += s["quantity"]
        m["sales"] += s["line_paise"]
    cities = {}
    for s in sales:
        c = cities.setdefault((s["pharmacy_city"], s["pharmacy_state"]), {"units_sold": 0, "sales_paise": 0})
        c["units_sold"] += s["quantity"]
        c["sales_paise"] += s["line_paise"]
    area_rx = {}
    for r in own:
        area_rx.setdefault(r["care_area"] or "Unclassified", set()).add(r["prescription_id"])
    leaders = {}
    for r in in_area:
        e = leaders.setdefault((r["name"], r["strength"]), {"name": r["name"], "generic_name": r["generic"],
                                                            "strength": r["strength"], "manufacturer": r["manufacturer"],
                                                            "care_area": r["care_area"], "own": r["medicine_id"] in own_meds,
                                                            "rx": set(), "units": 0})
        e["rx"].add(r["prescription_id"])
        e["units"] += r["quantity"]
    own_rx = {r["prescription_id"] for r in own}
    market_rx = {r["prescription_id"] for r in market}
    return {
        "days": days, "care_areas": sorted(areas) if areas else None, "min_group_size": settings.MIN_GROUP_SIZE,
        "totals": {"doctors_in_scope": len(who), "named_doctors": sum(1 for d in who.values() if d["named"]),
                   "doctors_prescribing": len({r["doctor_id"] for r in own}), "own_prescriptions": len(own_rx),
                   "own_units": sum(r["quantity"] for r in own), "units_sold": sum(s["quantity"] for s in sales),
                   "sales_paise": sum(s["line_paise"] for s in sales), "market_prescriptions": len(market_rx),
                   "own_share_percent": round(100 * len(own_rx) / len(market_rx), 1) if market_rx else None,
                   "products": len(own_meds)},
        "products": product_rows,
        "diseases": disease_rows,
        "doctors": doctor_rows,
        "trend": [{"month": k, "own_prescriptions": len(m["rx"]), "units_sold": m["sold"], "sales_paise": m["sales"]}
                  for k, m in sorted(months.items())],
        "sales_by_city": [{"city": k[0], "state": k[1], **c} for k, c in sorted(cities.items(), key=lambda x: -x[1]["sales_paise"])],
        "care_areas_breakdown": [{"care_area": a, "prescriptions": len(s)} for a, s in sorted(area_rx.items(), key=lambda x: -len(x[1]))],
        "top_medicines": [{k: e[k] for k in ("name", "generic_name", "strength", "manufacturer", "care_area", "own")}
                          | {"prescriptions": len(e["rx"]), "units": e["units"]}
                          for e in sorted(leaders.values(), key=lambda e: (-len(e["rx"]), e["name"]))[:20]],
    }


def period(days: int) -> int:
    if days not in (30, 90, 180, 365):
        raise HTTPException(status_code=400, detail="Period must be 30, 90, 180 or 365 days")
    return days


# ---------- MR dashboard ----------
def mr_scope(conn, mr) -> tuple:
    """(care areas of the MR's division, every approved doctor in the MR's territory)."""
    areas = split_areas(mr["care_areas"]) or None
    return areas, scope_doctors(conn, mr["territory_state"], mr["territory_city"])


# Which doctor specialities usually prescribe for a care area (for the MR's doctor filter)
SPECIALITY_AREAS = {
    "Cardiology": ["Cardiology", "Hypertension"], "Diabetes & Endocrine": ["Diabetes"],
    "Respiratory": ["Pulmonology", "Cardio-respiratory"], "Gastro & Liver": ["Gastroenterology"],
    "Infections": ["Fever & infections"], "Pain & Ortho": ["Orthopaedics", "Orthopaedic", "Sports injury",
                                                           "Pain management"],
    "Neurology": ["Neurology", "Neurological"], "Psychiatry": ["Psychiatry"], "Dermatology": ["Dermatology"],
    "Women's health": ["Gynaecology", "Women's health"], "Paediatrics": ["Paediatrics", "Paediatric"],
    "ENT & Eye": ["ENT"], "Dental": ["General dentistry", "Orthodontics", "Root canal", "Oral surgery"],
}


REQUEST_SELECT = """SELECT q.*, du.name AS doctor_name, du.email AS doctor_email, d.clinic_name, d.city AS doctor_city,
                           mu.name AS mr_name, mu.email AS mr_email, mu.phone AS mr_phone, c.name AS company_name,
                           dv.name AS division_name
                    FROM mr_requests q JOIN users du ON du.id = q.doctor_id JOIN doctors d ON d.user_id = q.doctor_id
                    JOIN users mu ON mu.id = q.mr_id JOIN companies c ON c.id = q.company_id
                    LEFT JOIN divisions dv ON dv.id = q.division_id"""


def request_view(row) -> dict:
    return {k: row[k] for k in ("id", "doctor_id", "doctor_name", "clinic_name", "doctor_city", "mr_id", "mr_name",
                                "company_name", "division_name", "purpose", "products", "preferred_date",
                                "preferred_time", "status", "doctor_note", "scheduled_for", "created_at", "updated_at")}


MR_TRANSITIONS = {"accept": ("pending", "accepted"), "decline": ("pending", "declined"),
                  "done": ("accepted", "completed")}

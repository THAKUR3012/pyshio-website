"""MR next-best-action and company alerts: which doctors to meet next and why; unusual changes in a product's prescribing.

This file: logic. Privacy rule: a doctor's prescribing only influences their rank (and the reasons shown) when the
doctor chose to share their prescribing insights by name. Every other doctor is ranked on public facts only
(speciality fit, when this MR last met them, whether they accept meetings), so the order can't reveal what an
anonymous doctor prescribes. Counts under settings.MIN_GROUP_SIZE are masked as everywhere else in pharma insights.
"""
from __future__ import annotations

import json
import math
from datetime import timedelta
from typing import Optional

from shared import ai, salts, settings
from shared.common import stamp


def _days_since(value: Optional[str], today) -> Optional[int]:
    if not value:
        return None
    from datetime import date
    return (today - date.fromisoformat(value[:10])).days


def next_actions(conn, mr, user_id: int) -> dict:
    areas, doctors = insights.mr_scope(conn, mr)
    data = insights.build_insights(conn, mr["company_id"], doctors, areas, 90)
    named = {d["doctor_id"]: d for d in data["doctors"] if d.get("named") and d.get("doctor_id")}
    wanted = {s.lower() for a in (areas or []) for s in insights.SPECIALITY_AREAS.get(a, [])}
    today = settings.now().date()
    month_ago = stamp(settings.now() - timedelta(days=30))
    ranked, waiting = [], []
    for doc in doctors:
        row = conn.execute("SELECT d.accept_mr_meetings, d.slug, d.specialities, d.category, d.clinic_name FROM doctors d "
                           "WHERE d.user_id = ?", (doc["user_id"],)).fetchone()
        if not row["accept_mr_meetings"]:
            continue
        pending = conn.execute("SELECT 1 FROM mr_requests WHERE mr_id = ? AND doctor_id = ? AND status IN ('pending', 'accepted')",
                               (user_id, doc["user_id"])).fetchone()
        recent = conn.execute("SELECT COUNT(*) FROM mr_requests WHERE mr_id = ? AND doctor_id = ? AND created_at > ?",
                              (user_id, doc["user_id"], month_ago)).fetchone()[0]
        last_met = conn.execute("SELECT MAX(updated_at) FROM mr_requests WHERE mr_id = ? AND doctor_id = ? AND status = 'completed'",
                                (user_id, doc["user_id"])).fetchone()[0]
        specialities = insights.split_areas(row["specialities"])
        item = {"doctor_id": doc["user_id"], "name": doc["name"], "clinic_name": row["clinic_name"], "city": doc["city"],
                "specialities": specialities, "shares_insights": bool(doc["share_insights_at"]), "last_met": last_met,
                "score": 0, "reasons": []}
        if pending:
            waiting.append({**item, "status": "request open"})
            continue
        if recent >= settings.MR_REQUESTS_PER_MONTH:
            continue
        # Public facts: speciality fit and visit recency
        fit = wanted & {s.lower() for s in specialities}
        if fit:
            item["score"] += 30
            item["reasons"].append(f"Speciality fits your division ({', '.join(sorted(fit))})")
        elif row["category"] == "general_physician":
            item["score"] += 15
            item["reasons"].append("General physician: prescribes across care areas")
        days = _days_since(last_met, today)
        if days is None:
            item["score"] += 20
            item["reasons"].append("You haven't met yet")
        elif days >= 60:
            item["score"] += 15
            item["reasons"].append(f"Last met {days} days ago")
        elif days >= 30:
            item["score"] += 8
            item["reasons"].append(f"Last met {days} days ago")
        elif days < 14:
            item["score"] -= 30
            item["reasons"].append(f"Met only {days} days ago")
        # Prescribing signals: only for doctors who share their insights by name
        ins = named.get(doc["user_id"])
        if ins and ins["market_prescriptions"]:
            gap = ins["market_prescriptions"] * (1 - (ins["share_percent"] or 0) / 100)
            item["score"] += min(40, round(gap * 4))
            item["opportunity"] = {"market_prescriptions": insights.mask(ins["market_prescriptions"]),
                                   "share_percent": ins["share_percent"], "top_competitor": ins["top_competitor"]}
            market = insights.mask(ins["market_prescriptions"])
            item["reasons"].append(f"Prescribes the salts you make ~{market} times in 90 days; your share {ins['share_percent']}%"
                                   + (f" (top competitor: {ins['top_competitor']})" if ins["top_competitor"] else ""))
        ranked.append(item)
    ranked.sort(key=lambda d: (-d["score"], d["name"]))
    anonymous = [{k: d.get(k) for k in ("label", "category", "city", "pooled")}
                 | {"market_prescriptions": insights.mask(d["market_prescriptions"]), "share_percent": d["share_percent"]
                    if d["market_prescriptions"] >= settings.MIN_GROUP_SIZE else None}
                 for d in data["doctors"] if not d.get("named") and d["market_prescriptions"] >= settings.MIN_GROUP_SIZE][:10]
    return {"next": ranked[:15], "waiting": waiting, "anonymous_opportunities": anonymous,
            "note": "Doctors who don't share their prescribing insights are ranked on speciality and visit history only."}


BRIEF_SYSTEM = ("You help a pharmaceutical medical representative in India prepare a short, ethical visit. Using only the "
                "facts given, write 3 short bullet points: what to discuss, which product evidence to bring, and one "
                "question to ask the doctor. No claims beyond the facts, no gifts or incentives, no patient information.")


def visit_brief(conn, mr, user_id: int, doctor_id: int) -> dict:
    plan = next_actions(conn, mr, user_id)
    item = next((d for d in plan["next"] + plan["waiting"] if d["doctor_id"] == doctor_id), None)
    if not item:
        return None
    products = [r["name"] for r in conn.execute("SELECT name FROM medicines WHERE company_id = ? ORDER BY name LIMIT 10",
                                                (mr["company_id"],))]
    bullets = [f"Introduce {', '.join(products[:3]) or 'your products'} and the conditions they're used for.",
               "Bring the latest prescribing information and any local price comparison.",
               "Ask which patients they find hardest to manage in your care areas."]
    if item.get("opportunity") and item["opportunity"].get("top_competitor"):
        bullets[1] = (f"They often prescribe {item['opportunity']['top_competitor']}: bring evidence comparing it with "
                      f"{', '.join(products[:2]) or 'your brand'}.")
    engine = "built-in"
    if ai.enabled("mr_brief"):
        facts = json.dumps({"doctor_specialities": item["specialities"], "reasons": item["reasons"],
                            "opportunity": item.get("opportunity"), "our_products": products, "division": mr["division_name"]})
        try:
            res = ai.call("mr_brief", system=BRIEF_SYSTEM, messages=[{"role": "user", "content": facts}], max_tokens=300,
                          user_id=user_id)
            lines = [l.strip(" -•*\t") for l in ai.text_of(res).splitlines() if l.strip(" -•*\t")]
            if lines:
                bullets, engine = lines[:3], "claude"
        except ai.AIUnavailable:
            pass
    return {"doctor_id": doctor_id, "name": item["name"], "bullets": bullets, "engine": engine}


# ---------- Company anomaly alerts ----------
RECENT_DAYS, BASELINE_DAYS = 28, 56


def _rx_count(conn, medicine_ids: list, start: str, end: str) -> int:
    if not medicine_ids:
        return 0
    return conn.execute(
        f"SELECT COUNT(DISTINCT i.prescription_id) FROM prescription_items i JOIN prescriptions r ON r.id = i.prescription_id "
        f"WHERE r.status != 'cancelled' AND r.created_at >= ? AND r.created_at < ? AND i.medicine_id IN ({','.join('?' * len(medicine_ids))})",
        (start, end, *medicine_ids)).fetchone()[0]


def product_alerts(conn, company_id: int) -> list:
    """Unusual changes per product: prescriptions far above / below their usual rate, or a falling share of the salt."""
    now = settings.now()
    recent_start = stamp(now - timedelta(days=RECENT_DAYS))
    base_start = stamp(now - timedelta(days=RECENT_DAYS + BASELINE_DAYS))
    end = stamp(now + timedelta(seconds=1))
    alerts = []
    for m in conn.execute("SELECT * FROM medicines WHERE company_id = ?", (company_id,)).fetchall():
        obs = _rx_count(conn, [m["id"]], recent_start, end)
        base = _rx_count(conn, [m["id"]], base_start, recent_start)
        expected = base * RECENT_DAYS / BASELINE_DAYS
        if expected >= settings.MIN_GROUP_SIZE:
            z = (obs - expected) / math.sqrt(expected)
            if abs(z) >= 3:
                alerts.append({"type": "rise" if z > 0 else "drop", "product_id": m["id"], "product": m["name"],
                               "care_area": m["care_area"], "recent": obs, "usual": round(expected, 1), "z": round(z, 1),
                               "message": f"{m['name']}: {obs} prescriptions in the last 4 weeks vs about {round(expected)} usually "
                                          f"({'up' if z > 0 else 'down'} {abs(round(100 * (obs - expected) / expected))}%)."})
        key, strength = m["salt_key"] or salts.salt_key(m["generic_name"]), m["strength_key"] or salts.strength_key(m["strength"])
        if not key:
            continue
        market_ids = [r["id"] for r in conn.execute("SELECT id FROM medicines WHERE salt_key = ? AND strength_key IS ?",
                                                    (key, strength))]
        m_recent = _rx_count(conn, market_ids, recent_start, end)
        m_base = _rx_count(conn, market_ids, base_start, recent_start)
        if m_recent >= 2 * settings.MIN_GROUP_SIZE and m_base >= 2 * settings.MIN_GROUP_SIZE:
            share_now, share_before = 100 * obs / m_recent, 100 * base / m_base
            if share_before - share_now >= 10:
                alerts.append({"type": "share_drop", "product_id": m["id"], "product": m["name"], "care_area": m["care_area"],
                               "share_now": round(share_now, 1), "share_before": round(share_before, 1),
                               "message": f"{m['name']}: share of {m['generic_name']} prescriptions fell from "
                                          f"{round(share_before)}% to {round(share_now)}% in the last 4 weeks."})
    return alerts


def record_alerts(conn, company_id: int) -> list:
    """New alerts this ISO week (each product/type once a week)."""
    week = settings.now().strftime("%G-W%V")
    fresh = []
    for a in product_alerts(conn, company_id):
        cur = conn.execute("INSERT OR IGNORE INTO intel_alerts (kind, subject_id, period, payload, created_at) "
                           "VALUES ('company_anomaly', ?, ?, ?, ?)",
                           (f"{company_id}:{a['product_id']}:{a['type']}", week, json.dumps(a), stamp()))
        if cur.rowcount:
            fresh.append(a)
    return fresh


def alerts_for(conn, company_id: int, care_areas: Optional[list] = None, limit: int = 30) -> list:
    rows = conn.execute("SELECT payload, period, created_at FROM intel_alerts WHERE kind = 'company_anomaly' AND subject_id LIKE ? "
                        "ORDER BY created_at DESC LIMIT 200", (f"{company_id}:%",)).fetchall()
    out = []
    for r in rows:
        a = json.loads(r["payload"])
        if care_areas and a.get("care_area") not in care_areas:
            continue
        out.append({**a, "week": r["period"], "created_at": r["created_at"]})
    return out[:limit]


# Imported last, so slices can depend on each other in any order
from features.pharma_insights import logic as insights  # noqa: E402

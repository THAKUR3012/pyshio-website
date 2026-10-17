"""Medicine courses: who is on which medicine until when, refill predictions and pharmacy demand forecast.

This file: logic and queries.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from shared import settings


def record_courses(conn, order_id: str, started=None) -> int:
    """Note when each handed-over medicine will run out (refill reminders, course tracking, demand forecasts)."""
    started = started or settings.now().date()
    rows = conn.execute(
        "SELECT oi.*, o.patient_id, o.pharmacy_id, o.prescription_id, r.lat, r.lng, pi.daily_units, pi.duration_days, "
        "pi.is_long_term, pi.quantity AS prescribed, COALESCE(m.generic_name, pi.generic_name) AS generic, "
        "COALESCE(m.strength, pi.strength) AS item_strength "
        "FROM order_items oi JOIN medicine_orders o ON o.id = oi.order_id "
        "JOIN prescriptions r ON r.id = o.prescription_id "
        "JOIN prescription_items pi ON pi.id = oi.prescription_item_id "
        "LEFT JOIN medicines m ON m.id = oi.medicine_id WHERE oi.order_id = ?", (order_id,)).fetchall()
    for r in rows:
        if r["daily_units"]:
            days = math.ceil(r["quantity"] / r["daily_units"])
        else:
            days = r["duration_days"]
        ends = (started + timedelta(days=days)).isoformat() if days else None
        conn.execute("INSERT OR IGNORE INTO courses (order_id, prescription_id, prescription_item_id, patient_id, "
                     "pharmacy_id, medicine_id, name, generic_name, strength, units, daily_units, is_long_term, "
                     "started_on, ends_on, lat, lng) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (order_id, r["prescription_id"], r["prescription_item_id"], r["patient_id"], r["pharmacy_id"],
                      r["medicine_id"], r["name"], r["generic"], r["item_strength"], r["quantity"], r["daily_units"],
                      r["is_long_term"], started.isoformat(), ends, r["lat"], r["lng"]))
    return len(rows)


def course_status(course, today: str, refilled: bool) -> str:
    if not course["ends_on"]:
        return "open"
    if course["ends_on"] < today:
        if not course["is_long_term"]:
            return "completed"
        return "refilled" if refilled else "overdue"
    soon = (datetime.fromisoformat(today) + timedelta(days=7)).date().isoformat()
    return "ending" if course["ends_on"] <= soon else "active"


def medicine_key(generic, name, strength) -> tuple:
    return ((generic or name or "").strip().lower(), (strength or "").strip().lower())


def courses_view(conn, where: str, params: tuple, include_patient: bool) -> list:
    rows = conn.execute(
        "SELECT c.*, u.name AS patient_name, u.phone AS patient_phone, r.reference, "
        "COALESCE(r.parent_id, r.id) AS root_id, ph.store_name FROM courses c "
        "JOIN users u ON u.id = c.patient_id JOIN prescriptions r ON r.id = c.prescription_id "
        "JOIN pharmacies ph ON ph.user_id = c.pharmacy_id "
        f"WHERE {where} ORDER BY c.ends_on IS NULL, c.ends_on", params).fetchall()
    # A long-term course counts as refilled once the same patient got the same medicine again later
    latest = {}
    for r in conn.execute(f"SELECT patient_id, generic_name, name, strength, MAX(started_on) AS last FROM courses "
                          f"WHERE patient_id IN ({','.join('?' * len({r['patient_id'] for r in rows}))}) "
                          "GROUP BY patient_id, lower(COALESCE(generic_name, name)), lower(COALESCE(strength, ''))",
                          tuple({r["patient_id"] for r in rows})) if rows else []:
        latest[(r["patient_id"], medicine_key(r["generic_name"], r["name"], r["strength"]))] = r["last"]
    today = settings.now().date().isoformat()
    out = []
    for r in rows:
        refilled = latest.get((r["patient_id"], medicine_key(r["generic_name"], r["name"], r["strength"])), "") > r["started_on"]
        item = {k: r[k] for k in ("id", "order_id", "prescription_id", "name", "generic_name", "strength", "units",
                                  "daily_units", "started_on", "ends_on", "reference", "store_name")}
        item.update(is_long_term=bool(r["is_long_term"]), status=course_status(r, today, refilled))
        root = conn.execute("SELECT * FROM prescriptions WHERE id = ?", (r["root_id"],)).fetchone()
        item["refills_left"] = max(root["refills_allowed"] - root["refills_used"], 0) if root["refills_allowed"] else 0
        if include_patient:
            item.update(patient_name=r["patient_name"], patient_phone=r["patient_phone"])
        out.append(item)
    return out


FORECAST_LOOKBACK_DAYS = 28


# ---------- Pharmacy demand forecast ----------
FORECAST_WEEKS = 8          # history used for the level, trend and weekday pattern
LEVEL_ALPHA = 0.4           # weight of the latest week in the level (exponential smoothing)
TREND_DAMPING = 0.8         # how quickly the trend fades into the future
SERVICE_Z = 1.28            # safety stock for ~90% of weeks without running out
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _slope(values: list) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mx, my = (n - 1) / 2, sum(values) / n
    sxx = sum((i - mx) ** 2 for i in range(n))
    return sum((i - mx) * (v - my) for i, v in enumerate(values)) / sxx


def weekday_factors(counts: list) -> list:
    """Units by weekday turned into multipliers around 1, shrunk toward no pattern when there is little data."""
    total, prior = sum(counts), 5.0
    if not total:
        return [1.0] * 7
    return [7 * (c + prior / 7) / (total + prior) for c in counts]


def smart_estimate(weekly: list, days: int, start, factors: list) -> dict:
    """Expected units over the next `days`: smoothed weekly level plus a damped trend, spread by the weekday pattern."""
    level = weekly[0]
    for w in weekly[1:]:
        level = LEVEL_ALPHA * w + (1 - LEVEL_ALPHA) * level
    trend = _slope(weekly)
    cap = 0.5 * max(level, 1)
    trend = max(-cap, min(cap, trend))
    total = 0.0
    for h in range(1, days + 1):
        t = h / 7
        damped = trend * TREND_DAMPING * (1 - TREND_DAMPING ** t) / (1 - TREND_DAMPING)
        total += max(0.0, level + damped) / 7 * factors[(start + timedelta(days=h)).weekday()]
    mean = sum(weekly) / len(weekly)
    sigma_week = math.sqrt(sum((w - mean) ** 2 for w in weekly) / max(len(weekly) - 1, 1))
    safety = SERVICE_Z * sigma_week / math.sqrt(7) * math.sqrt(days)
    active_weeks = sum(1 for w in weekly if w)
    confidence = "good" if active_weeks >= 4 and sum(weekly) >= 20 else "fair" if active_weeks >= 2 else "low"
    return {"forecast": total, "safety": safety, "trend_pct": round(100 * trend / level) if level >= 1 else None,
            "confidence": confidence}


def forecast(conn, pharmacy, days: int) -> dict:
    """Demand near the store. `expected_units` is the simple 28-day average (plus refills due); `forecast_units` adds the
    trend and weekday pattern; `reorder_units` / `reorder_packs` add safety stock and subtract what is on the shelf."""
    now = settings.now()
    today = now.date()
    radius = max(settings.NEARBY_RADIUS_KM, pharmacy["delivery_radius_km"] or 0)
    min_lat, max_lat, min_lng, max_lng = geo.bounding_box(pharmacy["lat"], pharmacy["lng"], radius)
    since = stamp(now - timedelta(days=FORECAST_LOOKBACK_DAYS))
    trend_since = stamp(now - timedelta(weeks=FORECAST_WEEKS))
    rx_rows = conn.execute(
        "SELECT r.id, r.lat, r.lng, r.created_at FROM prescriptions r WHERE r.status != 'cancelled' "
        "AND r.created_at >= ? AND r.lat BETWEEN ? AND ? AND r.lng BETWEEN ? AND ?",
        (trend_since, min_lat, max_lat, min_lng, max_lng)).fetchall()
    near = {r["id"]: r["created_at"] for r in rx_rows
            if geo.haversine_km(pharmacy["lat"], pharmacy["lng"], r["lat"], r["lng"]) <= radius}
    demand, weekly, by_weekday = {}, [0] * FORECAST_WEEKS, [0] * 7

    def entry(key, generic, strength):
        return demand.setdefault(key, {"generic_name": generic, "strength": strength, "brands": {}, "prescriptions": set(),
                                       "units": 0, "refill_units": 0, "weekly": [0] * FORECAST_WEEKS})

    ids = list(near)
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        for i in conn.execute(
                f"SELECT i.prescription_id, i.name, i.quantity, COALESCE(m.generic_name, i.generic_name) AS generic, "
                f"COALESCE(m.strength, i.strength) AS strength FROM prescription_items i "
                f"LEFT JOIN medicines m ON m.id = i.medicine_id WHERE i.prescription_id IN ({','.join('?' * len(part))})",
                part):
            created = near[i["prescription_id"]]
            when = datetime.fromisoformat(created)
            week = min(FORECAST_WEEKS - 1, max(0, (now - when).days // 7))
            weekly[FORECAST_WEEKS - 1 - week] += i["quantity"]
            by_weekday[when.weekday()] += i["quantity"]
            d = entry(medicine_key(i["generic"], i["name"], i["strength"]), i["generic"] or i["name"], i["strength"])
            d["weekly"][FORECAST_WEEKS - 1 - week] += i["quantity"]
            if created < since:
                continue
            d["units"] += i["quantity"]
            d["prescriptions"].add(i["prescription_id"])
            d["brands"][i["name"]] = d["brands"].get(i["name"], 0) + 1
    # Long-term courses nearby that run out inside the window will need a refill
    until = (today + timedelta(days=days)).isoformat()
    for c in conn.execute("SELECT * FROM courses WHERE is_long_term = 1 AND ends_on >= ? AND ends_on <= ? "
                          "AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",
                          (today.isoformat(), until, min_lat, max_lat, min_lng, max_lng)):
        if geo.haversine_km(pharmacy["lat"], pharmacy["lng"], c["lat"], c["lng"]) > radius:
            continue
        entry(medicine_key(c["generic_name"], c["name"], c["strength"]), c["generic_name"] or c["name"],
              c["strength"])["refill_units"] += c["units"]
    stock = {}
    for s_row in conn.execute(STOCK_SELECT + " WHERE s.pharmacy_id = ?", (pharmacy["user_id"],)):
        key = medicine_key(s_row["generic_name"], s_row["name"], s_row["strength"])
        st = stock.setdefault(key, {"in_stock": False, "on_hand": None, "pack_size": s_row["pack_size"]})
        st["in_stock"] = st["in_stock"] or bool(s_row["in_stock"])
        if s_row["on_hand"] is not None:
            st["on_hand"] = (st["on_hand"] or 0) + s_row["on_hand"]
    factors = weekday_factors(by_weekday)
    peak = WEEKDAYS[max(range(7), key=lambda d: factors[d])] if sum(by_weekday) >= 14 and max(factors) >= 1.2 else None
    rows = []
    for key, d in demand.items():
        expected = math.ceil(d["units"] / FORECAST_LOOKBACK_DAYS * days + d["refill_units"])
        smart = smart_estimate(d["weekly"], days, today, factors)
        predicted = math.ceil(smart["forecast"] + d["refill_units"])
        st = stock.get(key) or {}
        stock_status = ("in_stock" if st["in_stock"] else "out_of_stock") if st else "not_listed"
        need = math.ceil(predicted + smart["safety"] - (st.get("on_hand") or 0))
        pack = st.get("pack_size") or 1
        rows.append({"generic_name": d["generic_name"], "strength": d["strength"],
                     "brands": [b for b, _ in sorted(d["brands"].items(), key=lambda x: -x[1])[:3]],
                     "prescriptions_recent": len(d["prescriptions"]), "units_recent": d["units"],
                     "refill_units": d["refill_units"], "expected_units": expected, "forecast_units": predicted,
                     "safety_units": math.ceil(smart["safety"]), "trend_pct": smart["trend_pct"],
                     "confidence": smart["confidence"], "on_hand": st.get("on_hand"),
                     "reorder_units": max(0, need), "reorder_packs": math.ceil(need / pack) if need > 0 else 0,
                     "stock_status": stock_status,
                     "stock_up": max(expected, predicted) >= 5 and stock_status != "in_stock"})
    rows.sort(key=lambda r: (-max(r["expected_units"], r["forecast_units"]), r["generic_name"]))
    week_starts = [(today - timedelta(weeks=FORECAST_WEEKS - 1 - n, days=today.weekday())).isoformat()
                   for n in range(FORECAST_WEEKS)]
    return {"days": days, "radius_km": radius, "lookback_days": FORECAST_LOOKBACK_DAYS, "items": rows[:60],
            "stock_up": sum(r["stock_up"] for r in rows), "peak_weekday": peak,
            "weekly_units": [{"week": week_starts[n], "units": weekly[n]} for n in range(FORECAST_WEEKS)]}


# Imported last, so slices can depend on each other in any order
from shared import geo  # noqa: E402
from shared.common import stamp  # noqa: E402
from features.medicines.logic import STOCK_SELECT  # noqa: E402

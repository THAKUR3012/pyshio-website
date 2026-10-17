"""Pharmacy offers: prescriptions go to nearby pharmacies, they quote, the patient compares prices.

This file: logic and queries.
"""
from __future__ import annotations

from fastapi import BackgroundTasks, HTTPException

from shared import salts, settings
from shared.common import stamp


def distribute(conn, rx_id: str, lat: float, lng: float, radius_km: float) -> list:
    """Send the prescription to every nearby pharmacy that hasn't had it yet. Returns [(distance, pharmacy)]."""
    added = []
    for distance, pharmacy in nearby_pharmacies(conn, lat, lng, radius_km):
        cur = conn.execute("INSERT OR IGNORE INTO rx_requests (prescription_id, pharmacy_id, distance_km, status, "
                           "created_at, updated_at) VALUES (?, ?, ?, 'open', ?, ?)",
                           (rx_id, pharmacy["user_id"], distance, stamp(), stamp()))
        if cur.rowcount:
            added.append((distance, dict(pharmacy)))
    return added


# ---------- Price comparison ----------
def substitutes(conn, item) -> list:
    """Catalogue medicines that can fill this prescription line (same salt, strength, form if allowed)."""
    if not item["medicine_id"]:
        return []
    med = conn.execute("SELECT * FROM medicines WHERE id = ?", (item["medicine_id"],)).fetchone()
    if item["allow_substitute"] and med["generic_name"] and med["strength"]:
        # Normalised salt + strength ("Paracetamol IP 500mg" = "paracetamol 500 mg"); exact text as a fallback
        key, strength = salts.salt_key(med["generic_name"]), salts.strength_key(med["strength"])
        rows = conn.execute("SELECT id FROM medicines WHERE ((salt_key = ? AND strength_key = ?) OR "
                            "(lower(generic_name) = lower(?) AND lower(COALESCE(strength, '')) = lower(?))) "
                            "AND lower(COALESCE(form, '')) = lower(COALESCE(?, ''))",
                            (key, strength, med["generic_name"], med["strength"], med["form"])).fetchall()
        return sorted({r["id"] for r in rows} | {med["id"]})
    return [med["id"]]


def unit_price(price_paise: int, pack_size: int) -> int:
    return (price_paise + pack_size // 2) // max(pack_size, 1)


def estimate(conn, pharmacy, items: list, candidates: dict) -> dict:
    """Price the items for one pharmacy: its own price list first (cheapest matching product per line),
    otherwise its flat "% off MRP" applied to the catalogue MRP."""
    discount = pharmacy["flat_discount_percent"]
    lines = []
    for item in items:
        ids = candidates.get(item["id"]) or []
        marks = ",".join("?" * len(ids))
        line = None
        if ids:
            best = conn.execute(
                f"SELECT s.medicine_id, s.price_paise, s.mrp_paise, m.pack_size, m.name FROM pharmacy_stock s "
                f"JOIN medicines m ON m.id = s.medicine_id WHERE s.pharmacy_id = ? AND s.in_stock = 1 "
                f"AND s.medicine_id IN ({marks}) ORDER BY s.price_paise * 1.0 / m.pack_size LIMIT 1",
                (pharmacy["user_id"], *ids)).fetchone()
            if best:
                line = (best, unit_price(best["price_paise"], best["pack_size"]),
                        unit_price(best["mrp_paise"], best["pack_size"]), "price_list")
            elif discount is not None:
                med = conn.execute(f"SELECT id AS medicine_id, name, pack_size, mrp_paise FROM medicines "
                                   f"WHERE id IN ({marks}) AND mrp_paise IS NOT NULL "
                                   f"ORDER BY mrp_paise * 1.0 / pack_size LIMIT 1", ids).fetchone()
                if med:
                    mrp_unit = unit_price(med["mrp_paise"], med["pack_size"])
                    line = (med, int(round(mrp_unit * (100 - discount) / 100)), mrp_unit, "mrp_discount")
        if line:
            med, unit, mrp_unit, source = line
            lines.append({"prescription_item_id": item["id"], "available": True, "medicine_id": med["medicine_id"],
                          "name": med["name"], "quantity": item["quantity"], "unit_price_paise": unit,
                          "line_paise": unit * item["quantity"], "mrp_unit_paise": mrp_unit, "price_source": source,
                          "substitute": med["medicine_id"] != item["medicine_id"]})
        else:
            lines.append({"prescription_item_id": item["id"], "available": False, "medicine_id": None,
                          "name": item["name"], "quantity": item["quantity"], "unit_price_paise": 0, "line_paise": 0,
                          "mrp_unit_paise": None, "price_source": None, "substitute": False})
    return summarise(lines)


def summarise(lines: list) -> dict:
    available = [l for l in lines if l["available"]]
    # Discount is measured only on lines whose MRP we know
    with_mrp = [l for l in available if l.get("mrp_unit_paise")]
    mrp_total = sum(l["mrp_unit_paise"] * l["quantity"] for l in with_mrp)
    discount = mrp_total - sum(l["line_paise"] for l in with_mrp)
    return {"lines": lines, "available": len(available), "items": len(lines),
            "complete": bool(lines) and len(available) == len(lines),
            "subtotal_paise": sum(l["line_paise"] for l in available),
            "mrp_total_paise": mrp_total or None, "discount_paise": discount if mrp_total else None,
            "discount_percent": round(discount * 100 / mrp_total, 1) if mrp_total else None}


def active_quote(conn, rx_id: str, pharmacy_id: int):
    return conn.execute("SELECT * FROM quotes WHERE prescription_id = ? AND pharmacy_id = ? AND status = 'active' "
                        "AND valid_until > ? ORDER BY id DESC LIMIT 1", (rx_id, pharmacy_id, stamp())).fetchone()


def quote_offer(conn, quote, items: list) -> dict:
    wanted = {i["id"] for i in items}
    rows = conn.execute("SELECT * FROM quote_items WHERE quote_id = ?", (quote["id"],)).fetchall()
    lines = [{"prescription_item_id": r["prescription_item_id"], "available": bool(r["available"]), "medicine_id": None,
              "name": r["supplied_name"] or next(i["name"] for i in items if i["id"] == r["prescription_item_id"]),
              "quantity": r["quantity"], "unit_price_paise": r["unit_price_paise"], "line_paise": r["line_paise"],
              "mrp_unit_paise": r["mrp_paise"], "price_source": "quote", "substitute": False}
             for r in rows if r["prescription_item_id"] in wanted]
    return {**summarise(lines), "quote_id": quote["id"], "delivery_available": bool(quote["delivery_available"]),
            "delivery_fee_paise": quote["delivery_fee_paise"], "ready_in_minutes": quote["ready_in_minutes"],
            "delivery_minutes": quote["delivery_minutes"], "note": quote["note"], "valid_until": quote["valid_until"]}


SORTS = ("cheapest", "discount", "fastest", "nearest")


def sort_options(options: list, sort: str = "cheapest") -> list:
    big = float("inf")

    def best(o, key, default=0):
        return (o["best"] or {}).get(key) if (o["best"] or {}).get(key) is not None else default

    keys = {
        # Offers covering every medicine first, then lowest total, then nearest
        "cheapest": lambda o: (o["best"] is None, not best(o, "complete", False), -best(o, "available"),
                               best(o, "subtotal_paise"), o["pharmacy"]["distance_km"]),
        "discount": lambda o: (o["best"] is None, -best(o, "discount_percent", -1), best(o, "subtotal_paise")),
        "fastest": lambda o: (o["best"] is None, not o["delivery_available"],
                              o["delivery_minutes"] if o["delivery_available"] else o["pickup_minutes"],
                              not best(o, "complete", False), best(o, "subtotal_paise")),
        "nearest": lambda o: (o["best"] is None, o["pharmacy"]["distance_km"]),
    }
    return sorted(options, key=keys[sort])


def offer_summary(o) -> dict:
    b = o["best"]
    return {"pharmacy_id": o["pharmacy"]["id"], "store_name": o["pharmacy"]["store_name"],
            "distance_km": o["pharmacy"]["distance_km"], "total_paise": b["subtotal_paise"],
            "available": b["available"], "items": b["items"], "complete": b["complete"],
            "discount_percent": b["discount_percent"], "discount_paise": b["discount_paise"],
            "delivery_available": o["delivery_available"], "delivery_minutes": o["delivery_minutes"],
            "delivery_fee_paise": o["delivery_fee_paise"], "pickup_minutes": o["pickup_minutes"]}


def highlights(options: list) -> dict:
    """The patient's three headline choices. Offers covering every medicine win over partial ones."""
    priced = [o for o in options if o["best"] and o["best"]["available"]]

    def prefer_complete(group):
        return [o for o in group if o["best"]["complete"]] or group

    cheapest = sort_options(prefer_complete(priced), "cheapest")[:1]
    discounted = prefer_complete([o for o in priced if o["best"]["discount_percent"]])
    best_discount = sort_options(discounted, "discount")[:1]
    delivering = prefer_complete([o for o in priced if o["delivery_available"]])
    fastest = sort_options(delivering, "fastest")[:1]
    return {"cheapest": offer_summary(cheapest[0]) if cheapest else None,
            "best_discount": offer_summary(best_discount[0]) if best_discount else None,
            "fastest_delivery": offer_summary(fastest[0]) if fastest else None}


def price_options(conn, rx_row, radius_km: float = None, sort: str = "cheapest") -> list:
    """Every reachable pharmacy with its best offer for the not-yet-ordered items."""
    if rx_row["lat"] is None:
        return []
    ordered = ordered_item_ids(conn, rx_row["id"])
    items = [i for i in rx_items(conn, rx_row["id"]) if i["id"] not in ordered]
    if not items:
        return []
    candidates = {i["id"]: substitutes(conn, i) for i in items}
    requests = {r["pharmacy_id"]: r for r in conn.execute("SELECT * FROM rx_requests WHERE prescription_id = ?",
                                                          (rx_row["id"],))}
    quotes = {}
    for q in conn.execute("SELECT * FROM quotes WHERE prescription_id = ? AND status = 'active' AND valid_until > ? "
                          "ORDER BY id", (rx_row["id"], stamp())):
        quotes[q["pharmacy_id"]] = q  # newest wins
    options = []
    for distance, ph in nearby_pharmacies(conn, rx_row["lat"], rx_row["lng"], radius_km or rx_row["radius_km"]):
        req = requests.get(ph["user_id"])
        if not req or req["status"] == "declined":
            continue
        quote = quotes.get(ph["user_id"])
        offer_q = quote_offer(conn, quote, items) if quote else None
        offer_e = estimate(conn, ph, items, candidates)
        best = offer_q or (offer_e if offer_e["available"] else None)
        delivery_ok = bool(ph["offers_delivery"]) and distance <= ph["delivery_radius_km"]
        if offer_q:
            delivery_ok = delivery_ok and offer_q["delivery_available"]
        pickup = offer_q["ready_in_minutes"] if offer_q and offer_q["ready_in_minutes"] is not None \
            else ph["pickup_ready_minutes"]
        delivery = ((offer_q or {}).get("delivery_minutes") or ph["delivery_minutes"]) if delivery_ok else None
        options.append({
            "pharmacy": pharmacy_public(ph, distance), "request_status": req["status"],
            "quote": offer_q, "estimate": offer_e if offer_e["available"] else None,
            "best": {**best, "source": "quote" if offer_q else "price_list"} if best else None,
            "delivery_available": delivery_ok, "pickup_minutes": pickup, "delivery_minutes": delivery,
            "delivery_fee_paise": (offer_q["delivery_fee_paise"] if offer_q else ph["delivery_fee_paise"]) if delivery_ok else None,
        })
    tops = highlights(options)
    for o in options:
        o["tags"] = [k for k, h in tops.items() if h and h["pharmacy_id"] == o["pharmacy"]["id"]]
    return sort_options(options, sort)


def options_payload(conn, row, sort: str = "cheapest") -> dict:
    if row["lat"] is None:
        return {"needs_location": True, "shared": False, "options": [], "radius_km": row["radius_km"],
                "highlights": highlights([]), "wider_radii_km": []}
    shared = conn.execute("SELECT COUNT(*) FROM rx_requests WHERE prescription_id = ?", (row["id"],)).fetchone()[0]
    options = price_options(conn, row, sort=sort)
    return {"needs_location": False, "shared": bool(shared), "radius_km": row["radius_km"],
            "location_source": row["location_source"], "sort": sort, "options": options,
            "highlights": highlights(options), "home_differs": row["home_lat"] is not None and (
                row["location_source"] != "home" or (row["lat"], row["lng"]) != (row["home_lat"], row["home_lng"])),
            "wider_radii_km": [r for r in settings.WIDER_RADII_KM if r > row["radius_km"]]}


def share_prescription(conn, row, radius: float = None, use_home: bool = False) -> list:
    """Point the prescription at the patient's home (if asked / unknown) and send it to pharmacies in range."""
    rx_id = row["id"]
    if row["home_lat"] is not None and (use_home or row["lat"] is None):
        conn.execute("UPDATE prescriptions SET lat = ?, lng = ?, location_source = 'home' WHERE id = ?",
                     (row["home_lat"], row["home_lng"], rx_id))
    if radius and radius > row["radius_km"]:
        conn.execute("UPDATE prescriptions SET radius_km = ? WHERE id = ?", (radius, rx_id))
    row = conn.execute(RX_SELECT + " WHERE r.id = ?", (rx_id,)).fetchone()
    if row["lat"] is None:
        return []
    return distribute(conn, rx_id, row["lat"], row["lng"], row["radius_km"])


def email_new_requests(background: BackgroundTasks, conn, rx_id: str, added: list) -> None:
    if added:
        view = rx_view(conn, conn.execute(RX_SELECT + " WHERE r.id = ?", (rx_id,)).fetchone(), "pharmacy")
        for distance, pharmacy in added:
            background.add_task(pharmacy_offers_emails.send_rx_request_to_pharmacy, pharmacy, view, distance)


# ---------- Pharmacy: requests & quotes ----------
def request_for(conn, req_id: int, pharmacy_id: int):
    req = conn.execute("SELECT * FROM rx_requests WHERE id = ? AND pharmacy_id = ?", (req_id, pharmacy_id)).fetchone()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    return req


# Imported last, so slices can depend on each other in any order
from features.pharmacies.logic import nearby_pharmacies, pharmacy_public  # noqa: E402
from features.prescriptions.logic import ordered_item_ids, rx_items, RX_SELECT, rx_view  # noqa: E402
from features.pharmacy_offers import emails as pharmacy_offers_emails  # noqa: E402

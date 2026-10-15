"""Assistant: the per-role chatbot (built-in answers, optional Claude).

This file: logic and queries.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from fastapi import HTTPException

from shared import ai, clinical_terms, mailer, settings
from shared import triage_rules
from shared.common import stamp
from shared.db import get_db


log = logging.getLogger("pyshio.assistant")


money = mailer.money


def reply(text, table=None, links=None):
    """table: {"columns": [{"label", "num"}], "rows": [[...]]} — the page adds Sr No and house styling."""
    return {"text": text, "table": table, "links": links or []}


def tbl(columns, rows, num=()):
    return {"columns": [{"label": c, "num": i in num} for i, c in enumerate(columns)], "rows": rows}


def pretty(d: str) -> str:
    from datetime import date
    try:
        return date.fromisoformat(d[:10]).strftime("%a %d %b %Y")
    except ValueError:
        return d


# ====================================================================== Patient
def p_next_appointments(user):
    today = settings.now().date().isoformat()
    with get_db() as conn:
        rows = conn.execute("SELECT a.*, du.name AS doctor_name, d.clinic_name, d.slug FROM appointments a "
                            "JOIN users du ON du.id = a.doctor_id JOIN doctors d ON d.user_id = a.doctor_id "
                            "WHERE a.patient_id = ? AND a.status = 'confirmed' AND a.appointment_date >= ? "
                            "ORDER BY a.appointment_date, a.appointment_time LIMIT 5", (user["id"], today)).fetchall()
    if not rows:
        return reply("You have no upcoming appointments.", links=[{"label": "Find a doctor nearby", "href": "/"}])
    a = rows[0]
    return reply(f"Your next appointment is with {a['doctor_name']} on {pretty(a['appointment_date'])} at "
                 f"{a['appointment_time']} ({a['clinic_name']}).",
                 tbl(["Date", "Time", "Doctor", "Service", "Fee"],
                     [[pretty(r["appointment_date"]), r["appointment_time"], r["doctor_name"], r["service_name"],
                       f"₹{r['fee']:,}"] for r in rows], num=(4,)),
                 [{"label": "My appointments", "href": "/account#appointments"}])


def latest_live_rx(conn, user, need: str):
    for row in conn.execute(prescriptions_logic.RX_SELECT + " WHERE r.patient_id = ? AND r.status = 'active' AND r.valid_until > ? "
                            "ORDER BY r.created_at DESC", (user["id"], stamp())).fetchall():
        if need == "medicines":
            left = {i["id"] for i in prescriptions_logic.rx_items(conn, row["id"])} - prescriptions_logic.ordered_item_ids(conn, row["id"])
        else:
            left = {t["id"] for t in labs.rx_tests(conn, row["id"])} - labs.booked_test_ids(conn, row["id"])
        if left:
            return row
    return None


def p_cheapest_pharmacy(user):
    with get_db() as conn:
        row = latest_live_rx(conn, user, "medicines")
        if not row:
            return reply("You have no prescription with medicines still to buy.",
                         links=[{"label": "My prescriptions", "href": "/account#prescriptions"}])
        data = pharmacy_offers.options_payload(conn, row)
    if data.get("needs_location"):
        return reply("Set your home location first so I can find pharmacies near you.",
                     links=[{"label": "Set home location", "href": "/account#profile"}])
    h = data["highlights"]
    if not h.get("cheapest"):
        return reply(f"Pharmacies near you haven't priced prescription {row['reference']} yet. I'll show offers as they come in.",
                     links=[{"label": "Open prescription", "href": "/account#prescriptions"}])
    rows = []
    for label, key in (("Cheapest", "cheapest"), ("Best discount", "best_discount"), ("Fastest delivery", "fastest_delivery")):
        o = h.get(key)
        if o:
            rows.append([label, o["store_name"], f"{o['distance_km']} km", money(o["total_paise"]),
                         f"{o['discount_percent']}%" if o["discount_percent"] else "—",
                         f"~{o['delivery_minutes']} min" if o.get("delivery_available") else "Pickup"])
    c = h["cheapest"]
    return reply(f"For prescription {row['reference']}, the cheapest is {c['store_name']} at {money(c['total_paise'])} "
                 f"({c['available']} of {c['items']} medicines).",
                 tbl(["Best for", "Pharmacy", "Distance", "Price", "Discount", "Delivery"], rows, num=(2, 3, 4)),
                 [{"label": "Compare and order", "href": "/account#prescriptions"}])


def p_cheapest_lab(user):
    with get_db() as conn:
        row = latest_live_rx(conn, user, "tests")
        if not row:
            return reply("You have no tests waiting to be booked.", links=[{"label": "Lab tests", "href": "/account#labs"}])
        data = labs.options_payload(conn, row, "cheapest")
    if not data["has_location"]:
        return reply("Set your home location first so I can find labs near you.",
                     links=[{"label": "Set home location", "href": "/account#profile"}])
    h = data["highlights"]
    if not h["cheapest"]:
        return reply(f"No lab near you has priced the tests on {row['reference']} yet.",
                     links=[{"label": "Open prescription", "href": "/account#prescriptions"}])
    rows = []
    for label, key in (("Cheapest", "cheapest"), ("Fastest report", "fastest"), ("Home collection", "home_collection")):
        o = h.get(key)
        if o:
            rows.append([label, o["lab_name"], f"{o['distance_km']} km", money(o["subtotal_paise"] + (o["collection_fee_paise"] if key == "home_collection" else 0)),
                         f"{o['report_hours']} h" if o["report_hours"] else "—"])
    c = h["cheapest"]
    return reply(f"For your tests on {row['reference']}, the cheapest lab is {c['lab_name']} at {money(c['subtotal_paise'])}.",
                 tbl(["Best for", "Lab", "Distance", "Price", "Report in"], rows, num=(2, 3, 4)),
                 [{"label": "Compare and book", "href": "/account#prescriptions"}])


def p_orders(user):
    with get_db() as conn:
        rows = conn.execute(medicine_orders_logic.ORDER_SELECT + " WHERE o.patient_id = ? AND o.status IN "
                            "('placed', 'accepted', 'ready', 'out_for_delivery') ORDER BY o.created_at DESC", (user["id"],)).fetchall()
    if not rows:
        return reply("You have no medicine orders in progress.", links=[{"label": "Medicine orders", "href": "/account#orders"}])
    status = {"placed": "Waiting for the pharmacy", "accepted": "Being packed", "ready": "Ready for pickup",
              "out_for_delivery": "Out for delivery"}
    o = rows[0]
    extra = f" Your handover code is {o['delivery_otp']}." if o["delivery_otp"] else ""
    return reply(f"Order {o['reference']} from {o['store_name']}: {status[o['status']]}.{extra}",
                 tbl(["Order", "Pharmacy", "Status", "Total"], [[r["reference"], r["store_name"], status[r["status"]],
                                                                money(r["total_paise"])] for r in rows], num=(3,)),
                 [{"label": "Medicine orders", "href": "/account#orders"}])


def p_reports(user):
    with get_db() as conn:
        rows = conn.execute(health_records.RECORD_SELECT + " WHERE h.patient_id = ? AND h.deleted_at IS NULL "
                            "ORDER BY h.record_date DESC, h.id DESC LIMIT 5", (user["id"],)).fetchall()
        recs = [health_records.record_view(conn, r) for r in rows]
        pending = conn.execute("SELECT COUNT(*) FROM lab_bookings WHERE patient_id = ? AND status IN "
                               "('booked', 'accepted', 'sample_collected')", (user["id"],)).fetchone()[0]
    if not recs:
        return reply("You have no reports yet." + (f" {pending} lab booking(s) are in progress." if pending else ""),
                     links=[{"label": "Health records", "href": "/account#records"}])
    flagged = [f"{v['name']} {v['value_num']} {v['unit'] or ''} ({v['flag']})".strip() for r in recs for v in r["values"]
               if v["flag"] in ("high", "low")]
    text = f"Your latest report is {recs[0]['title']} from {pretty(recs[0]['record_date'])}."
    text += f" Results outside the normal range: {', '.join(flagged[:4])}." if flagged else " No results were outside the normal range."
    if pending:
        text += f" {pending} more lab booking(s) are in progress."
    rows = [[pretty(r["record_date"]), r["title"], ", ".join(f"{v['name']} {v['value_num'] if v['value_num'] is not None else v['value_text']}"
                                                              f"{' (' + v['flag'] + ')' if v['flag'] in ('high', 'low') else ''}"
                                                              for v in r["values"]) or "—"] for r in recs]
    return reply(text + " Please discuss results with your doctor.", tbl(["Date", "Report", "Key results"], rows),
                 [{"label": "Health records", "href": "/account#records"}])


def p_trends(user, test=None):
    with get_db() as conn:
        series = health_records.trends_for(conn, user["id"])
        hit = clinical_terms.match_test(conn, test) if test else None
    series = [s for s in series if len(s["points"]) > 1]
    if test:
        wanted = [s for s in series if (hit and s.get("test_id") == hit["id"]) or test.lower() in s["name"].lower()]
        if not wanted:
            return reply(f"I need at least two reports with {test} to show how it changed.",
                         links=[{"label": "Health records", "href": "/account#records"}])
        series = wanted
    if not series:
        return reply("I need at least two lab reports with the same test to show a trend.",
                     links=[{"label": "Health records", "href": "/account#records"}])
    rows = [[s["name"], " → ".join(str(p["value"]) for p in s["points"]), s["unit"] or "",
             f"{s['ref_low'] if s['ref_low'] is not None else ''}–{s['ref_high'] if s['ref_high'] is not None else ''}"]
            for s in series[:6]]
    s = series[0]
    direction = {"up": "went up", "down": "came down", "flat": "stayed about the same"}.get(s.get("direction"), "changed")
    toward = {True: ", toward the normal range", False: ", away from the normal range"}.get(s.get("toward_normal"), "")
    return reply(f"Your {s['name']} {direction} from {s['points'][0]['value']} to {s['points'][-1]['value']} "
                 f"{s['unit'] or ''} over {len(s['points'])} reports{toward}.", tbl(["Test", "Results (oldest first)", "Unit", "Normal"], rows),
                 [{"label": "See charts", "href": "/account#records"}])


def p_refills(user):
    with get_db() as conn:
        out = []
        for row in conn.execute(prescriptions_logic.RX_SELECT + " WHERE r.patient_id = ? AND r.parent_id IS NULL AND r.refills_allowed > 0 "
                                "AND r.status = 'active' ORDER BY r.created_at DESC", (user["id"],)).fetchall():
            st = prescriptions_logic.refill_status(conn, row)
            names = ", ".join(i["name"] for i in prescriptions_logic.rx_items(conn, row["id"]) if i["is_long_term"])
            out.append([row["reference"], names, f"{st['left']} of {st['allowed']}",
                        "Available now" if st["can_refill"] else (st["reason"] or "—")])
        courses = [c for c in medicine_courses.courses_view(conn, "c.patient_id = ?", (user["id"],), False) if c["status"] in ("ending", "overdue")]
    if not out and not courses:
        return reply("None of your prescriptions have refills, and no medicines are running out.")
    text = ""
    if courses:
        text = "Running out soon: " + ", ".join(f"{c['name']} ({pretty(c['ends_on'])})" for c in courses[:4]) + ". "
    ready = [r for r in out if r[3] == "Available now"]
    text += f"You can refill {ready[0][1]} now." if ready else "No refill is due right now." if out else ""
    return reply(text.strip(), tbl(["Prescription", "Long-term medicines", "Refills left", "Status"], out, num=(2,)) if out else None,
                 [{"label": "Refill from Prescriptions", "href": "/account#prescriptions"}])


def p_spending(user):
    with get_db() as conn:
        t = patient_history.patient_history(conn, user["id"])["totals"]
    return reply(f"You've spent {money(t['net_paise'])} on Pyshio: {money(t['consultations_paise'])} on consultations and "
                 f"{money(t['medicines_paise'])} on medicines" + (f", with {money(t['refunds_paise'])} refunded" if t["refunds_paise"] else "") + ".",
                 tbl(["Item", "Count"], [["Doctor visits", t["visits"]], ["Doctors", t["doctors"]], ["Prescriptions", t["prescriptions"]],
                                         ["Medicine orders", t["orders"]], ["Pharmacies", t["pharmacies"]]], num=(1,)),
                 [{"label": "Full history", "href": "/account#history"}, {"label": "Download CSV", "href": "/api/my/history.csv"}])


def p_my_doctors(user):
    docs = patient_history_api.my_doctors(user=user)
    if not docs:
        return reply("You haven't seen any doctor on Pyshio yet.", links=[{"label": "Find a doctor nearby", "href": "/"}])
    return reply(f"You've seen {len(docs)} doctor(s). {docs[0]['name']} is the one you visited most recently.",
                 tbl(["Doctor", "Type", "Visits", "Last visit", "Fee from"],
                     [[d["name"], d["category_label"], d["visits"], pretty(d["last_visit"]) if d["last_visit"] else "—",
                       f"₹{d['fee_from']:,}" if d["fee_from"] is not None else "—"] for d in docs[:8]], num=(2, 4)),
                 [{"label": f"Book {docs[0]['name']} again", "href": f"/doctors/{docs[0]['slug']}"}])


def p_find_doctor(user):
    if user.get("home_lat") is None:
        return reply("Set your home location so I can find doctors within 3 km.", links=[{"label": "Set home location", "href": "/account#profile"}])
    docs = doctors_api.list_doctors(lat=user["home_lat"], lng=user["home_lng"], sort="cheapest", user=user, city=None,
                               speciality=None, q=None, category=None, radius=None)[:6]
    if not docs:
        return reply("No verified doctors within 3 km yet.", links=[{"label": "Search wider", "href": "/"}])
    return reply(f"Cheapest doctors within {settings.NEARBY_RADIUS_KM:g} km of your home:",
                 tbl(["Doctor", "Type", "Distance", "Fee from", "Visited"],
                     [[d["name"], d["category_label"], f"{d['distance_km']} km", f"₹{d['fee_from']:,}" if d["fee_from"] is not None else "—",
                       d["visited_count"] or "—"] for d in docs], num=(2, 3, 4)),
                 [{"label": "See all and book", "href": "/"}])


def p_medicines(user):
    with get_db() as conn:
        meds = [health_records.medicine_view(r) for r in conn.execute(
            "SELECT * FROM patient_medicines WHERE patient_id = ? ORDER BY active DESC, name", (user["id"],))]
        current = conn.execute("SELECT c.name, c.ends_on FROM courses c WHERE c.patient_id = ? AND (c.ends_on IS NULL OR c.ends_on >= ?) "
                               "ORDER BY c.ends_on", (user["id"], settings.now().date().isoformat())).fetchall()
    rows = [[c["name"], "Bought on Pyshio", f"until {pretty(c['ends_on'])}" if c["ends_on"] else "—"] for c in current] + \
           [[m["name"], "Your list", "Taking" if m["active"] else "Stopped"] for m in meds]
    if not rows:
        return reply("You have no current medicines recorded.", links=[{"label": "Add other medicines", "href": "/account#records"}])
    return reply(f"You have {len(current)} medicine course(s) from Pyshio and {sum(m['active'] for m in meds)} other medicine(s) you take.",
                 tbl(["Medicine", "Source", "Status"], rows), [{"label": "Health records", "href": "/account#records"}])


def p_today(user):
    """Today's doses (features/medicine_reminders) and how regularly they're taken."""
    with get_db() as conn:
        me = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        day = medicine_reminders.day_schedule(conn, me, settings.now().date())
        adh = medicine_reminders.adherence(conn, me, 30)
    if not day["summary"]["due"]:
        return reply("No daily medicines are due today.", links=[{"label": "Open Today", "href": "/account#today"}])
    rows = [[g["label"], g["time"], d["name"] + (f" (for {d['for_name']})" if d["for_name"] else ""), d["status"].title()]
            for g in day["groups"] for d in g["doses"]]
    text = f"{day['summary']['taken']} of {day['summary']['due']} doses taken today."
    if adh["percent"] is not None:
        text += f" Over the last 30 days you marked {adh['percent']}% of doses as taken."
    return reply(text, tbl(["When", "Time", "Medicine", "Status"], rows), [{"label": "Mark doses", "href": "/account#today"}])


def p_follow_ups(user):
    with get_db() as conn:
        ups = follow_ups.my_follow_ups(conn, user["id"])
    if not ups:
        return reply("None of your doctors has asked for a follow-up visit.")
    rows = [[u["doctor_name"], pretty(u["follow_up_on"]), u["for_name"] or "You",
             f"Booked for {pretty(u['booked_for'])}" if u["booked_for"] else ("Overdue" if u["overdue"] else f"In {u['days_left']} days")]
            for u in ups]
    first = next((u for u in ups if not u["booked_for"]), None)
    return reply(f"You have {len(ups)} follow-up(s).", tbl(["Doctor", "Due", "For", "Status"], rows),
                 [{"label": f"Book with {first['doctor_name']}", "href": first["book_path"]}] if first else [])


def p_family(user):
    with get_db() as conn:
        members = family.members(conn, user["id"])
    if not members:
        return reply("You haven't added family members yet. Add them to book doctors and keep reports for them.",
                     links=[{"label": "Add family", "href": "/account#profile"}])
    return reply(f"You manage {len(members)} family member(s).",
                 tbl(["Name", "Relation", "Age", "Blood group", "Allergies"],
                     [[m["name"], m["relation"].title(), m["age"] if m["age"] is not None else "—", m["blood_group"] or "—",
                       m["allergies"] or "—"] for m in members], num=(2,)),
                 [{"label": "Manage family", "href": "/account#profile"}])


def any_notifications(user):
    with get_db() as conn:
        rows = conn.execute("SELECT title, created_at, link FROM notifications WHERE user_id = ? AND read_at IS NULL "
                            "ORDER BY id DESC LIMIT 8", (user["id"],)).fetchall()
    if not rows:
        return reply("You're all caught up: no unread notifications.")
    return reply(f"You have {len(rows)}{'+' if len(rows) == 8 else ''} unread notification(s). Open the bell at the top to see them all.",
                 tbl(["Update", "When"], [[r["title"], pretty(r["created_at"])] for r in rows]),
                 [{"label": "Open", "href": rows[0]["link"] or "/"}])


def p_uploads(user):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM rx_uploads WHERE patient_id = ? ORDER BY created_at DESC LIMIT 3", (user["id"],)).fetchall()
        if not rows:
            return reply("You haven't uploaded a prescription yet. Upload a photo and nearby pharmacies and labs will send quotations.",
                         links=[{"label": "Upload a prescription", "href": "/account#uploads"}])
        d = prescription_uploads.detail(conn, rows[0], "cheapest")
    out = []
    for kind, label in (("medicines", "Medicines"), ("tests", "Lab tests")):
        for o in d[kind]["options"][:4]:
            out.append([label, o["partner_name"], f"{o['distance_km']} km", money(o["subtotal_paise"]), f"{o['lines_available']} of {o['lines_total']}"])
    best = d["medicines"]["highlights"]["cheapest"] or d["tests"]["highlights"]["cheapest"]
    text = (f"Your upload {d['reference']} has {len(out)} quotation(s). Cheapest: {best['partner_name']} at {money(best['subtotal_paise'])}."
            if best else f"Your upload {d['reference']} was sent to {d['medicines']['sent_to'] + d['tests']['sent_to']} nearby; no quotations yet.")
    return reply(text, tbl(["For", "From", "Distance", "Price", "Items"], out, num=(2, 3, 4)) if out else None,
                 [{"label": "Compare and book", "href": "/account#uploads"}])


def partner_uploads_skill(user):
    rows = prescription_uploads_api.partner_uploads(status="open", user=user)
    waiting = [r for r in rows if r["status"] == "open"]
    stats = prescription_uploads_api.partner_stats(user=user)
    return reply(f"{len(waiting)} uploaded prescription(s) nearby are waiting for your quotation, {len(rows) - len(waiting)} already quoted, "
                 f"and {stats['open_orders']} booking(s) from uploads are in progress.",
                 tbl(["Request", "Distance", "Patient's note"], [[r["reference"], f"{r['distance_km']} km", r["note"] or "—"] for r in waiting[:10]], num=(1,))
                 if waiting else None, [{"label": "Uploaded prescriptions", "href": "/pharmacy" if user["role"] == "pharmacy" else "/lab"}])


# ====================================================================== Doctor
def d_today(user):
    today = settings.now().date().isoformat()
    with get_db() as conn:
        rows = conn.execute("SELECT a.*, u.name AS patient_name FROM appointments a JOIN users u ON u.id = a.patient_id "
                            "WHERE a.doctor_id = ? AND a.appointment_date = ? AND a.status IN ('confirmed', 'completed', 'no_show') "
                            "ORDER BY a.appointment_time", (user["id"], today)).fetchall()
    if not rows:
        return reply("You have no appointments today.", links=[{"label": "Appointments", "href": "/doctor"}])
    left = sum(r["status"] == "confirmed" for r in rows)
    return reply(f"You have {len(rows)} appointment(s) today; {left} still to see.",
                 tbl(["Time", "Patient", "Service", "Status"], [[r["appointment_time"], r["patient_name"], r["service_name"],
                                                                r["status"].replace("_", " ")] for r in rows]),
                 [{"label": "Open appointments", "href": "/doctor"}])


def d_requests(user):
    data = pharma_insights_api.doctor_requests(user=user)
    c = data["counts"]
    pending = [r for r in data["mr_requests"] if r["status"] == "pending"]
    text = (f"{c['new_bookings']} new booking(s), {c['awaiting_reply']} patient message(s) waiting for a reply, "
            f"{c['refills']} refill(s) used in 30 days and {c['mr_pending']} MR meeting request(s) waiting.")
    return reply(text, tbl(["MR", "Company", "Purpose", "Preferred"],
                           [[r["mr_name"], r["company_name"], r["purpose"], " ".join(x for x in (r["preferred_date"], r["preferred_time"]) if x) or "Any time"]
                            for r in pending]) if pending else None,
                 [{"label": "Open Requests", "href": "/doctor#requests"}])


def d_month(user):
    s = appointments_api.doctor_stats(user=user)
    with get_db() as conn:
        e = settlements.settlement_for(conn, "doctor", user["id"])
        rating = conn.execute("SELECT ROUND(AVG(rating), 1), COUNT(*) FROM reviews WHERE target_role = 'doctor' AND target_id = ?",
                              (user["id"],)).fetchone()
    return reply(f"This month: {s['completed_month']} completed visits, {s['no_show_month']} no-shows, ₹{s['revenue_month']:,} collected. "
                 f"Online fees due to you: {money(e['due_paise'])}." + (f" Rating {rating[0]} from {rating[1]} reviews." if rating[1] else ""),
                 tbl(["Item", "Value"], [["Today", s["today"]], ["Next 7 days", s["upcoming_7_days"]], ["Patients (all time)", s["patients"]],
                                         ["Online fees earned", money(e["earned_paise"])], ["Paid out", money(e["paid_out_paise"])]], num=(1,)),
                 [{"label": "Profile & payouts", "href": "/doctor#profile"}])


def d_prescriptions(user):
    since = stamp(settings.now() - timedelta(days=30))
    with get_db() as conn:
        rows = conn.execute("SELECT r.reference, r.created_at, u.name AS patient_name, r.parent_id, "
                            "(SELECT COUNT(*) FROM medicine_orders o WHERE o.prescription_id = r.id AND o.status = 'completed') AS dispensed, "
                            "(SELECT COUNT(*) FROM lab_bookings b WHERE b.prescription_id = r.id AND b.status IN ('report_ready', 'completed')) AS reports "
                            "FROM prescriptions r JOIN users u ON u.id = r.patient_id WHERE r.doctor_id = ? AND r.created_at >= ? "
                            "ORDER BY r.created_at DESC LIMIT 10", (user["id"], since)).fetchall()
    if not rows:
        return reply("You haven't written prescriptions in the last 30 days.")
    return reply(f"Your last {len(rows)} prescriptions (30 days):",
                 tbl(["Date", "Prescription", "Patient", "Dispensed", "Lab reports"],
                     [[pretty(r["created_at"]), r["reference"] + (" (refill)" if r["parent_id"] else ""), r["patient_name"],
                       "Yes" if r["dispensed"] else "Not yet", r["reports"] or "—"] for r in rows], num=(4,)),
                 [{"label": "All prescriptions", "href": "/doctor#prescriptions"}])


def d_reports(user):
    since = (settings.now() - timedelta(days=30)).date().isoformat()
    with get_db() as conn:
        rows = conn.execute(health_records.RECORD_SELECT + " WHERE h.doctor_id = ? AND h.kind = 'lab_report' AND h.deleted_at IS NULL "
                            "AND h.record_date >= ? ORDER BY h.record_date DESC LIMIT 10", (user["id"], since)).fetchall()
        out = []
        for r in rows:
            v = health_records.record_view(conn, r)
            name = conn.execute("SELECT name FROM users WHERE id = ?", (r["patient_id"],)).fetchone()[0]
            flags = [f"{x['name']} {x['value_num']} ({x['flag']})" for x in v["values"] if x["flag"] in ("high", "low")]
            out.append([pretty(v["record_date"]), name, v["title"], ", ".join(flags) or "Normal"])
    if not out:
        return reply("No lab reports came in for tests you ordered in the last 30 days.")
    abnormal = sum(1 for r in out if r[3] != "Normal")
    return reply(f"{len(out)} report(s) for tests you ordered in 30 days; {abnormal} with results outside the normal range.",
                 tbl(["Date", "Patient", "Tests", "Out of range"], out), [{"label": "Patients", "href": "/doctor"}])


def d_pharma(user):
    with get_db() as conn:
        row = conn.execute("SELECT share_insights_at, accept_mr_meetings FROM doctors WHERE user_id = ?", (user["id"],)).fetchone()
        upcoming = conn.execute("SELECT COUNT(*) FROM mr_requests WHERE doctor_id = ? AND status = 'accepted' AND scheduled_for >= ?",
                                (user["id"], settings.now().isoformat()[:16])).fetchone()[0]
    named = "by name" if row["share_insights_at"] else "anonymously (category and city only)"
    return reply(f"Pharma companies see your prescribing counts {named}; they never see patient details. "
                 f"MR meeting requests are {'on' if row['accept_mr_meetings'] else 'off'}. You have {upcoming} MR meeting(s) coming up.",
                 links=[{"label": "Change in Profile", "href": "/doctor#profile"}])


# ====================================================================== Pharmacy
def ph_today(user):
    s = pharmacies_api.pharmacy_stats(user=user)
    return reply(f"{s['open_requests']} prescription request(s) to quote, {s['open_orders']} open order(s). This month: "
                 f"{s['completed_month']} completed orders, {money(s['sales_month_paise'])} in sales. Online money due to you: "
                 f"{money(s['settlement']['due_paise'])}.", links=[{"label": "Open dashboard", "href": "/pharmacy"}])


def ph_orders(user):
    with get_db() as conn:
        rows = conn.execute(medicine_orders_logic.ORDER_SELECT + " WHERE o.pharmacy_id = ? AND o.status IN ('placed', 'accepted', 'ready', 'out_for_delivery') "
                            "ORDER BY o.created_at", (user["id"],)).fetchall()
    if not rows:
        return reply("No open orders right now.")
    waiting = sum(r["status"] == "placed" for r in rows)
    return reply(f"{len(rows)} open order(s); {waiting} waiting for you to accept (they auto-reject after {settings.ORDER_ACCEPT_MINUTES} min).",
                 tbl(["Order", "Patient", "Status", "Type", "Total"], [[r["reference"], r["patient_name"], r["status"].replace("_", " "),
                                                                       r["fulfilment"], money(r["total_paise"])] for r in rows], num=(4,)),
                 [{"label": "Orders", "href": "/pharmacy"}])


def ph_forecast(user, days=7):
    f = medicine_courses_api.pharmacy_forecast(days=days, user=user)
    rows = [i for i in f["items"] if i["stock_up"]][:8] or f["items"][:8]
    if not rows:
        return reply("No prescriptions near your store in the last 4 weeks, so there's nothing to forecast yet.")
    return reply(f"{f['stock_up']} medicine(s) need stocking up for the next {days} days. Highest demand: {f['items'][0]['generic_name']} "
                 f"({f['items'][0]['forecast_units']} units)." + (f" Your busiest day is {f['peak_weekday']}." if f.get("peak_weekday") else ""),
                 tbl(["Medicine", "Forecast units", "Your stock"], [[f"{i['generic_name']} {i['strength'] or ''}".strip(), i["forecast_units"],
                                                                     {"in_stock": "In stock", "out_of_stock": "Out of stock", "not_listed": "Not listed"}[i["stock_status"]]]
                                                                    for i in rows], num=(1,)),
                 [{"label": "Full forecast", "href": "/pharmacy"}])


def ph_courses(user):
    data = medicine_courses_api.pharmacy_courses(status=None, user=user)
    due = [c for c in data["courses"] if c["status"] in ("ending", "overdue")][:10]
    c = data["counts"]
    if not due:
        return reply("None of your customers' medicines run out in the next 7 days.")
    return reply(f"{c.get('ending', 0)} customer course(s) run out within 7 days and {c.get('overdue', 0)} ran out without a refill.",
                 tbl(["Customer", "Medicine", "Runs out", "Refills left"], [[x["patient_name"], x["name"], pretty(x["ends_on"]), x["refills_left"]]
                                                                         for x in due], num=(3,)),
                 [{"label": "Courses & refills", "href": "/pharmacy"}])


# ====================================================================== Lab
def l_today(user):
    s = labs_api.lab_stats(user=user)
    return reply(f"{s['open_requests']} test request(s) to quote, {s['new_bookings']} new booking(s) to accept, "
                 f"{s['awaiting_report']} sample(s) waiting for a report. This month: {s['completed_month']} reports, "
                 f"{money(s['revenue_month_paise'])} revenue.", links=[{"label": "Open dashboard", "href": "/lab"}])


def l_bookings(user):
    rows = labs_api.lab_bookings(status="open", user=user)
    if not rows:
        return reply("No open bookings right now.")
    label = {"booked": "New", "accepted": "Accepted", "sample_collected": "Report due", "report_ready": "Report sent"}
    return reply(f"{len(rows)} open booking(s).", tbl(["Booking", "Patient", "Tests", "Collection", "Status"],
                                                      [[b["reference"], b["patient_name"], ", ".join(i["name"] for i in b["items"]),
                                                        "Home" if b["collection"] == "home" else "Walk-in", label[b["status"]]] for b in rows[:10]]),
                 [{"label": "Bookings", "href": "/lab#bookings"}])


# ====================================================================== Company & MR
def _insights(user, days=90):
    with get_db() as conn:
        if user["role"] == "company":
            company = pharma_insights.company_for(conn, user, approved=True)
            return pharma_insights.build_insights(conn, company["id"], pharma_insights.scope_doctors(conn), None, days)
        mr = pharma_insights.mr_for(conn, user)
        areas, doctors = pharma_insights.mr_scope(conn, mr)
        return pharma_insights.build_insights(conn, mr["company_id"], doctors, areas, days)


def c_sales(user, days=90):
    d = _insights(user, days)
    t = d["totals"]
    if not d["products"]:
        return reply("List your brands first (company dashboard → Brands) so I can track their sales.")
    p = d["products"][0]
    return reply(f"Last {days} days: {money(t['sales_paise'])} sales, {t['units_sold']:,} units sold, {t['own_prescriptions']} prescriptions "
                 f"with your brands ({t['own_share_percent'] or 0}% share of those salts). Top product: {p['name']}.",
                 tbl(["Product", "Prescriptions", "Units sold", "Sales", "Salt share"],
                     [[x["name"], x["prescriptions"], f"{x['units_sold']:,}", money(x["sales_paise"]),
                       f"{x['salt_share_percent']}%" if x["salt_share_percent"] is not None else "—"] for x in d["products"][:8]], num=(1, 2, 3, 4)),
                 [{"label": "Full insights", "href": "/company" if user["role"] == "company" else "/mr"}])


def c_doctors(user):
    d = _insights(user)
    rows = [x for x in d["doctors"] if x["own_prescriptions"]][:10]
    if not rows:
        return reply("No doctor in scope prescribed your brands in the last 90 days.")
    top = rows[0]
    return reply(f"{d['totals']['doctors_prescribing']} doctor(s) prescribed your brands in 90 days. Top: {top['label']} "
                 f"({top['own_prescriptions']} prescriptions, {top['share_percent']}% share). Doctors who haven't opted in are anonymous.",
                 tbl(["Doctor", "City", "Your brands", "All brands (your salts)", "Top competitor"],
                     [[x["label"] + ("" if x["named"] else " (anonymous)"), x["city"], x["own_prescriptions"], x["market_prescriptions"],
                       x["top_competitor"] or "—"] for x in rows], num=(2, 3)),
                 [{"label": "Doctor-wise view", "href": "/company" if user["role"] == "company" else "/mr"}])


def c_diseases(user):
    d = _insights(user)
    rows = [x for x in d["diseases"] if x["own_prescriptions"] not in (0,)][:10]
    if not rows:
        return reply("No coded diagnoses yet for prescriptions of your salts.")
    return reply(f"Your brands are prescribed most for {rows[0]['condition']}. Counts under {settings.MIN_GROUP_SIZE} are hidden.",
                 tbl(["Disease", "With your brands", "All brands", "Top product"],
                     [[x["condition"], x["own_prescriptions"], x["market_prescriptions"], x["top_product"] or "—"] for x in rows], num=(1, 2)),
                 [{"label": "Full insights", "href": "/company" if user["role"] == "company" else "/mr"}])


def c_meetings(user):
    with get_db() as conn:
        if user["role"] == "company":
            company = pharma_insights.company_for(conn, user)
            rows = conn.execute(pharma_insights.REQUEST_SELECT + " WHERE q.company_id = ? AND q.status IN ('pending', 'accepted') "
                                "ORDER BY q.created_at DESC LIMIT 15", (company["id"],)).fetchall()
            mrs = conn.execute("SELECT COUNT(*) FROM mrs m JOIN users u ON u.id = m.user_id WHERE m.company_id = ? AND u.is_active = 1",
                               (company["id"],)).fetchone()[0]
            head = f"You have {mrs} active MR(s). "
        else:
            pharma_insights.mr_for(conn, user)
            rows = conn.execute(pharma_insights.REQUEST_SELECT + " WHERE q.mr_id = ? AND q.status IN ('pending', 'accepted') "
                                "ORDER BY q.created_at DESC", (user["id"],)).fetchall()
            head = ""
    accepted = [r for r in rows if r["status"] == "accepted"]
    text = head + f"{len(rows) - len(accepted)} meeting request(s) waiting for doctors, {len(accepted)} accepted."
    return reply(text, tbl(["Doctor", "MR", "Status", "Scheduled"], [[r["doctor_name"], r["mr_name"], r["status"],
                                                                     r["scheduled_for"].replace("T", " ") if r["scheduled_for"] else "—"] for r in rows]) if rows else None,
                 [{"label": "Meetings", "href": "/company#meetings" if user["role"] == "company" else "/mr#meetings"}])


def mr_territory(user):
    rows = pharma_insights_api.mr_doctors(care_area=None, q=None, user=user)
    with get_db() as conn:
        mr = pharma_insights.mr_for(conn, user)
    open_doors = [d for d in rows if d["accepts_meetings"] and d["last_request"] != "pending"]
    return reply(f"Your territory ({mr['territory_city'] or 'whole state'}, {mr['territory_state']}) has {len(rows)} approved doctors; "
                 f"{len(open_doors)} accept meeting requests now.",
                 tbl(["Doctor", "Type", "Clinic", "Last contact"], [[d["name"], d["category"], d["clinic_name"],
                                                                   d["last_request"] or "—"] for d in open_doors[:10]]),
                 [{"label": "Request a meeting", "href": "/mr#doctors"}])


# ====================================================================== Admin
def a_pending(user):
    with get_db() as conn:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE status = 'pending'").fetchone()[0]
                  for t in ("doctors", "pharmacies", "labs", "companies")}
    total = sum(counts.values())
    return reply(f"{total} application(s) waiting: {counts['doctors']} doctor(s), {counts['pharmacies']} pharmacy(ies), "
                 f"{counts['labs']} lab(s), {counts['companies']} pharma company(ies).",
                 links=[{"label": "Doctors", "href": "/admin#doctors"}, {"label": "Pharmacies", "href": "/admin#pharmacies"},
                        {"label": "Labs", "href": "/admin#labs"}, {"label": "Companies", "href": "/admin#companies"}])


def a_platform(user):
    s = admin_api.admin_stats(user=user)
    n = admin_api.network_stats(user=user)
    with get_db() as conn:
        lab_month = conn.execute("SELECT COUNT(*) FROM lab_bookings WHERE created_at >= ?",
                                 (settings.now().date().replace(day=1).isoformat(),)).fetchone()[0]
    a = s["appointments_month"]
    return reply(f"This month: {a['confirmed'] + a['completed']} appointments, {n['prescriptions_month']} prescriptions, "
                 f"{n['orders_month']} medicine orders ({money(n['medicine_sales_month_paise'])} completed sales), {lab_month} lab bookings. "
                 f"Failed refunds needing attention: {s['refunds_failed'] + n['order_refunds_failed']}.",
                 tbl(["Item", "Count"], [["Patients", s["patients"]], ["Approved doctors", s["doctors"]["approved"]],
                                         ["Approved pharmacies", n["pharmacies"]["approved"]], ["Medicines in catalogue", n["catalogue_size"]]], num=(1,)),
                 [{"label": "Analytics", "href": "/admin#analytics"}])


def a_diseases(user):
    today = settings.now().date()
    with get_db() as conn:
        data = admin_analytics.build(conn, admin_analytics.Filters((today - timedelta(days=30)).isoformat(), today.isoformat()))
    rows = data["conditions"][:8]
    if not rows:
        return reply("No coded diagnoses in the last 30 days.")
    return reply(f"Top disease in the last 30 days: {rows[0]['name']}. Groups under {settings.MIN_GROUP_SIZE} are hidden.",
                 tbl(["Disease", "Care area", "Cases"], [[r["name"], r["care_area"], r["cases"]] for r in rows], num=(2,)),
                 [{"label": "Analytics with filters", "href": "/admin#analytics"}])


# ====================================================================== Intelligence skills
def emergency_reply(hits) -> dict:
    return reply("This could be an emergency. Call 108 (ambulance) or 112, or go to the nearest hospital now. "
                 + " ".join(m for _, m in hits), links=[{"label": "Call 108", "href": "tel:108"}])


def p_symptom(user, symptoms=None):
    if not symptoms:
        return reply("Tell me what's bothering you, e.g. “back pain for 2 weeks” or “bacche ko bukhar hai”.")
    out = triage.answer(symptoms, user=user)
    if out["emergency"]:
        return reply(out["message"] + " " + " ".join(out["messages"]), links=[{"label": "Call 108", "href": "tel:108"}])
    specs = ", ".join(out["specialities"])
    if not out.get("doctors"):
        return reply(f"A {specs} doctor would suit this. {out['reason']} " + (
            "Set your home location so I can find them near you." if out.get("needs_location") else "None are close by yet."),
            links=[{"label": "Find doctors", "href": "/"}])
    return reply(f"A {specs} doctor would suit this. {out['reason']}"
                 + (" None of these specialists are close by, so here are general physicians." if out.get("fallback_to_gp") else "")
                 + " " + out["disclaimer"],
                 tbl(["Doctor", "Type", "Distance", "Fee from"],
                     [[d["name"], d["category_label"], f"{d['distance_km']} km", f"₹{d['fee_from']:,}" if d["fee_from"] is not None else "—"]
                      for d in out["doctors"][:6]], num=(2, 3)),
                 [{"label": f"Book {out['doctors'][0]['name']}", "href": f"/doctors/{out['doctors'][0]['slug']}"}])


def p_explain_report(user):
    with get_db() as conn:
        row = conn.execute(health_records.RECORD_SELECT + " WHERE h.patient_id = ? AND h.deleted_at IS NULL AND "
                           "EXISTS (SELECT 1 FROM record_values v WHERE v.record_id = h.id) ORDER BY h.record_date DESC, h.id DESC "
                           "LIMIT 1", (user["id"],)).fetchone()
        if not row:
            return reply("You don't have a report with results yet.", links=[{"label": "Health records", "href": "/account#records"}])
        values = health_records.record_values(conn, row["id"])
        out = lab_reader.explain(conn, row, values, health_records.trends_for(conn, user["id"]), "en", user["id"])
    text = out["summary"] or " ".join(l["text"] for l in out["lines"][:4])
    return reply(f"{row['title']} ({pretty(row['record_date'])}): {text} {out['disclaimer']}",
                 links=[{"label": "Full explanation", "href": "/account#records"}])


def p_adherence(user):
    with get_db() as conn:
        a = medicine_reminders.adherence(conn, user, 30)
    if not a["due"]:
        return reply("There are no scheduled doses to track yet.", links=[{"label": "Today's medicines", "href": "/account#today"}])
    worst = next((m for m in a["medicines"] if m["percent"] is not None), None)
    return reply(f"In the last 30 days you marked {a['percent']}% of doses as taken ({a['taken']} of {a['due']})."
                 + (f" {worst['name']} is missed most ({worst['percent']}%)." if worst and worst["percent"] < 80 else ""),
                 tbl(["Medicine", "Taken", "Due", "Score"], [[m["name"], m["taken"], m["due"], f"{m['percent']}%"]
                                                           for m in a["medicines"][:8]], num=(1, 2, 3)),
                 [{"label": "Today's medicines", "href": "/account#today"}])


def d_risky_today(user):
    today = settings.now().date().isoformat()
    with get_db() as conn:
        rows = conn.execute("SELECT a.*, pu.name AS patient_name, pu.phone AS patient_phone FROM appointments a JOIN users pu ON "
                            "pu.id = a.patient_id WHERE a.doctor_id = ? AND a.status = 'confirmed' AND a.appointment_date BETWEEN ? "
                            "AND date(?, '+1 day') ORDER BY a.appointment_date, a.appointment_time", (user["id"], today, today)).fetchall()
        risky = []
        for r in rows:
            s = risk_nudges.no_show_score(conn, r)
            if s["band"] == "high" and not r["attendance_confirmed_at"]:
                risky.append([pretty(r["appointment_date"]), r["appointment_time"], r["patient_name"], r["patient_phone"] or "—",
                              "; ".join(s["reasons"])])
    if not risky:
        return reply("No visits today or tomorrow look likely to be missed.")
    return reply(f"{len(risky)} visit(s) may need a reminder call. Patients also get one automatic “Still coming?” message.",
                 tbl(["Date", "Time", "Patient", "Phone", "Why"], risky), [{"label": "Appointments", "href": "/doctor"}])


def ph_reorder(user, days=7):
    f = medicine_courses_api.pharmacy_forecast(days=days, user=user)
    rows = [i for i in f["items"] if i["reorder_units"]][:10]
    if not rows:
        return reply(f"Nothing to reorder for the next {days} days" + (" based on what's on your shelf." if f["items"] else "; no demand yet."))
    return reply(f"Suggested reorder for the next {days} days (forecast plus a buffer, minus what's on your shelf):",
                 tbl(["Medicine", "Forecast", "Reorder units", "Packs"],
                     [[f"{i['generic_name']} {i['strength'] or ''}".strip(), i["forecast_units"], i["reorder_units"], i["reorder_packs"]]
                      for i in rows], num=(1, 2, 3)),
                 [{"label": "Forecast", "href": "/pharmacy#forecast"}])


def mr_next(user):
    with get_db() as conn:
        plan = pharma_nba.next_actions(conn, pharma_insights.mr_for(conn, user), user["id"])
    if not plan["next"]:
        return reply("No doctors in your territory accept meeting requests right now.")
    top = plan["next"][0]
    return reply(f"Start with {top['name']}: {top['reasons'][0] if top['reasons'] else 'good fit for your division'}.",
                 tbl(["Doctor", "Clinic", "Why"], [[d["name"], d["clinic_name"], "; ".join(d["reasons"][:2])] for d in plan["next"][:6]]),
                 [{"label": "Who to visit next", "href": "/mr#next"}])


def c_alerts(user):
    with get_db() as conn:
        if user["role"] == "company":
            alerts = pharma_nba.alerts_for(conn, pharma_insights.company_for(conn, user, approved=True)["id"])
        else:
            mr = pharma_insights.mr_for(conn, user)
            alerts = pharma_nba.alerts_for(conn, mr["company_id"], pharma_insights.split_areas(mr["care_areas"]) or None)
    if not alerts:
        return reply("No unusual changes in your products' prescribing recently.")
    return reply(f"{len(alerts)} recent alert(s). Latest: {alerts[0]['message']}",
                 tbl(["Week", "Product", "What changed"], [[a["week"], a["product"], a["message"]] for a in alerts[:8]]))


# ====================================================================== Skill registry
# (id, roles, handler, description for the AI, keywords for the built-in matcher, suggestion chip)
SKILLS = [
    ("today", {"patient"}, p_today, "Medicines due today, which are taken, and how regularly the patient takes them.",
     ["today", "dose", "doses", "medicines today", "medicine today", "today's medicine", "should i take", "missed", "aaj",
      "reminder"], "What medicines do I take today?"),
    ("adherence", {"patient"}, p_adherence, "How regularly the patient took their medicines in the last 30 days.",
     ["adherence", "regularly", "missed doses", "how often", "score"], "How regularly do I take my medicines?"),
    ("symptom_check", {"patient"}, p_symptom,
     "Which kind of doctor suits the patient's symptoms, with emergency warning signs, and the cheapest such doctors nearby. "
     "Pass the symptoms in the user's own words.",
     ["symptom", "which doctor", "kaunsa doctor", "kis doctor", "pain", "dard", "bukhar", "fever", "hurts", "sick", "takleef",
      "cough", "khansi"], "Which doctor should I see for my symptoms?"),
    ("explain_report", {"patient"}, p_explain_report, "Explains the patient's latest lab report in plain words.",
     ["explain", "what does my report mean", "samjha", "meaning", "matlab"], "Explain my latest report"),
    ("follow_ups", {"patient"}, p_follow_ups, "Follow-up visits doctors asked for.",
     ["follow-up", "follow up", "followup", "come back", "dobara", "next check"], "Do I have a follow-up due?"),
    ("family", {"patient"}, p_family, "Family members the patient manages.",
     ["family", "daughter", "son", "wife", "husband", "mother", "father", "parivar", "child", "kids"], "Who is in my family list?"),
    ("notifications", {"patient", "doctor", "pharmacy", "lab", "company", "mr", "admin"}, any_notifications,
     "Unread in-app notifications.", ["notification", "updates", "what's new", "whats new", "alerts", "bell", "unread"],
     "Any new updates for me?"),
    ("next_appointments", {"patient"}, p_next_appointments, "The patient's upcoming appointments.",
     ["appointment", "next visit", "booking", "when is my", "kab hai", "agla", "schedule"], "When is my next appointment?"),
    ("cheapest_pharmacy", {"patient"}, p_cheapest_pharmacy, "Cheapest / best-discount / fastest pharmacy offers for the patient's latest prescription.",
     ["cheapest", "pharmacy", "medicine price", "medicines", "sasta", "dawai", "dawa", "chemist", "discount", "buy medicine"],
     "Where are my medicines cheapest?"),
    ("cheapest_lab", {"patient"}, p_cheapest_lab, "Cheapest / fastest / home-collection labs for the patient's tests.",
     ["lab", "test", "cheapest lab", "blood test", "home collection", "jaanch", "sample"], "Which lab is cheapest for my tests?"),
    ("orders", {"patient"}, p_orders, "Status of the patient's medicine orders and handover code.",
     ["order", "delivery", "otp", "handover", "code", "track", "kab aayega"], "Where is my medicine order?"),
    ("reports", {"patient"}, p_reports, "The patient's latest lab reports and results outside the normal range.",
     ["report", "result", "hba1c", "sugar", "cholesterol", "high", "low", "normal"], "Show my latest report"),
    ("trends", {"patient"}, p_trends, "How the patient's test results changed over time.",
     ["trend", "over time", "improve", "progress", "change", "graph"], "How have my results changed?"),
    ("refills", {"patient"}, p_refills, "Refills left on long-term prescriptions and medicines running out.",
     ["refill", "run out", "running out", "khatam", "repeat", "long-term"], "Can I refill my medicines?"),
    ("spending", {"patient"}, p_spending, "How much the patient spent on consultations and medicines.",
     ["spent", "spend", "expense", "kharcha", "kitna", "money", "payment", "total"], "How much have I spent?"),
    ("my_doctors", {"patient"}, p_my_doctors, "Doctors the patient has seen, with visit counts.",
     ["my doctors", "doctors i", "visited", "book again", "revisit", "history"], "Which doctors have I seen?"),
    ("find_doctor", {"patient"}, p_find_doctor, "Cheapest verified doctors within 3 km of the patient's home.",
     ["find doctor", "doctor near", "nearby doctor", "gp", "physio", "dentist", "doctor"], "Find a doctor near me"),
    ("my_medicines", {"patient"}, p_medicines, "Medicines the patient is currently taking.",
     ["current medicines", "taking", "my medicines", "other medicines", "which medicines"], "What medicines am I taking?"),

    ("uploads", {"patient"}, p_uploads, "Quotations from pharmacies and labs for prescriptions the patient uploaded.",
     ["upload", "uploaded", "quotation", "quote", "photo", "parchi"], "Any quotations for my upload?"),
    ("partner_uploads", {"pharmacy", "lab"}, partner_uploads_skill, "Uploaded prescriptions nearby waiting for a quotation.",
     ["upload", "uploaded", "quotation", "quote", "photo", "parchi"], "Any uploaded prescriptions to quote?"),
    ("today", {"doctor"}, d_today, "The doctor's appointments today.",
     ["today", "aaj", "schedule", "appointments", "patients today", "opd"], "Who am I seeing today?"),
    ("risky_today", {"doctor"}, d_risky_today, "Today's and tomorrow's visits that may be missed (no-show risk) and why.",
     ["no-show", "no show", "may be missed", "miss", "reminder call", "risk"], "Which visits may be missed?"),
    ("doctor_requests", {"doctor"}, d_requests, "Waiting items: new bookings, patient messages, refills used, MR meeting requests.",
     ["request", "pending", "waiting", "message", "mr", "meeting", "inbox"], "What's waiting for me?"),
    ("doctor_month", {"doctor"}, d_month, "The doctor's month: visits, no-shows, revenue, payouts, rating.",
     ["month", "earning", "revenue", "income", "payout", "rating", "kamai"], "How was my month?"),
    ("doctor_prescriptions", {"doctor"}, d_prescriptions, "Prescriptions written in the last 30 days and whether they were dispensed.",
     ["prescription", "dispensed", "written", "rx"], "Were my prescriptions dispensed?"),
    ("doctor_reports", {"doctor"}, d_reports, "Lab reports for tests the doctor ordered, with abnormal results.",
     ["report", "lab", "result", "abnormal", "test"], "Any new lab reports?"),
    ("doctor_pharma", {"doctor"}, d_pharma, "What pharma companies see about the doctor, and MR meeting settings.",
     ["pharma", "privacy", "anonymous", "name", "share", "company", "mr setting"], "What do pharma companies see?"),

    ("pharmacy_today", {"pharmacy"}, ph_today, "The pharmacy's open requests, orders, sales and money due.",
     ["today", "summary", "sales", "money", "due", "overview", "status"], "Give me today's summary"),
    ("pharmacy_orders", {"pharmacy"}, ph_orders, "The pharmacy's open orders.",
     ["order", "pending", "accept", "delivery", "open"], "Which orders are open?"),
    ("pharmacy_forecast", {"pharmacy"}, ph_forecast, "Expected demand near the store for 7 days and what to stock up.",
     ["stock", "forecast", "demand", "stock up", "predict", "kya rakhna"], "What should I stock up on?"),
    ("reorder", {"pharmacy"}, ph_reorder, "Suggested reorder quantities from the demand forecast and the units on the shelf.",
     ["reorder", "distributor", "how much to buy", "purchase", "kitna mangana", "order stock"], "What should I reorder?"),
    ("pharmacy_courses", {"pharmacy"}, ph_courses, "Customers whose medicines run out soon or ran out without a refill.",
     ["course", "refill", "running out", "customers", "khatam"], "Whose medicines are running out?"),

    ("lab_today", {"lab"}, l_today, "The lab's requests, bookings, pending reports and revenue.",
     ["today", "summary", "revenue", "requests", "overview", "status"], "Give me today's summary"),
    ("lab_bookings", {"lab"}, l_bookings, "The lab's open bookings.",
     ["booking", "collection", "sample", "report due", "patients", "home"], "Which bookings are open?"),

    ("sales", {"company", "mr"}, c_sales, "Sales, units sold, prescriptions and share for the company's products (last 90 days).",
     ["sales", "sold", "revenue", "product", "brand", "share", "performance", "bikri"], "How are our products selling?"),
    ("top_doctors", {"company", "mr"}, c_doctors, "Doctor-wise prescribing of the company's brands (named or anonymous).",
     ["doctor", "prescriber", "who prescribes", "top doctor", "doctor-wise", "which doctor"], "Which doctors prescribe us most?"),
    ("diseases", {"company", "mr"}, c_diseases, "Diseases the company's products are prescribed for.",
     ["disease", "condition", "diagnosis", "for what", "kis bimari", "indication"], "Which diseases drive our sales?"),
    ("meetings", {"company", "mr"}, c_meetings, "MR meeting requests waiting and accepted.",
     ["meeting", "visit", "appointment", "request", "mr"], "What meetings are coming up?"),
    ("next_visits", {"mr"}, mr_next, "Which doctors the MR should visit next and why.",
     ["next visit", "who to visit", "who should i meet", "visit next", "plan my", "kisse milna"], "Who should I visit next?"),
    ("alerts", {"company", "mr"}, c_alerts, "Unusual recent changes in the company's products' prescribing.",
     ["alert", "unusual", "drop", "rise", "anomaly", "changed"], "Any unusual changes in our products?"),
    ("territory", {"mr"}, mr_territory, "Doctors in the MR's territory who accept meeting requests.",
     ["territory", "doctors near", "accept", "list doctors", "area"], "Which doctors can I meet?"),

    ("pending", {"admin"}, a_pending, "Applications waiting for approval.",
     ["pending", "approve", "approval", "application", "verify", "waiting"], "What's waiting for approval?"),
    ("platform", {"admin"}, a_platform, "Platform activity this month and failed refunds.",
     ["month", "summary", "activity", "platform", "refund", "numbers", "overview"], "How is the platform doing this month?"),
    ("admin_diseases", {"admin"}, a_diseases, "Top diseases in the last 30 days (aggregated).",
     ["disease", "condition", "trend", "outbreak", "dengue", "analytics"], "What are the top diseases this month?"),
]


# Arguments a skill accepts (JSON schema properties); the AI fills them, the built-in matcher reads them from the text
SKILL_ARGS = {
    "trends": {"test": {"type": "string", "maxLength": 80, "description": "A test name, e.g. HbA1c or cholesterol."}},
    "symptom_check": {"symptoms": {"type": "string", "maxLength": 500, "description": "The symptoms, in the user's words."}},
    "pharmacy_forecast": {"days": {"type": "integer", "enum": [7, 14, 30], "description": "Forecast window in days."}},
    "reorder": {"days": {"type": "integer", "enum": [7, 14, 30], "description": "Reorder window in days."}},
    "sales": {"days": {"type": "integer", "enum": [30, 90, 180, 365], "description": "Period in days."}},
}
# What people usually ask next (shown as chips after an answer)
FOLLOW_UPS = {
    "reports": ["explain_report", "trends", "cheapest_lab"], "explain_report": ["trends", "find_doctor"],
    "trends": ["explain_report", "reports"], "symptom_check": ["next_appointments", "my_doctors"],
    "today": ["adherence", "refills", "risky_today", "doctor_requests"], "adherence": ["today", "refills"],
    "cheapest_pharmacy": ["orders", "refills"], "refills": ["cheapest_pharmacy", "today"],
    "pharmacy_forecast": ["reorder", "pharmacy_courses"], "reorder": ["pharmacy_forecast", "pharmacy_courses"],
    "sales": ["alerts", "top_doctors", "diseases"], "alerts": ["sales", "top_doctors"],
    "next_visits": ["meetings", "territory"], "risky_today": ["today", "doctor_requests"],
}


def skills_for(role: str) -> list:
    return [s for s in SKILLS if role in s[1]]


def suggestions_after(role: str, skill_id) -> list:
    mine = {s[0]: s for s in skills_for(role)}
    picked = [mine[i][5] for i in FOLLOW_UPS.get(skill_id, []) if i in mine and i != skill_id]
    picked += [s[5] for s in mine.values() if s[0] != skill_id and s[5] not in picked]
    return picked[:4]


def extract_args(skill_id: str, text: str) -> dict:
    """Simple arguments from the user's own words (built-in mode)."""
    t = text.lower()
    args = {}
    if skill_id == "symptom_check":
        args["symptoms"] = text.strip()
    if skill_id == "trends":
        for alias in sorted(clinical_terms.LAB_ALIASES, key=len, reverse=True):
            if len(alias) >= 3 and re.search(rf"\b{re.escape(alias)}\b", t):
                args["test"] = alias
                break
    if skill_id in SKILL_ARGS and "days" in SKILL_ARGS[skill_id]:
        m = re.search(r"(\d+)\s*(days?|din)", t)
        n = int(m.group(1)) if m else 30 if re.search(r"\b(month|mahina)\b", t) else None
        if n:
            allowed = SKILL_ARGS[skill_id]["days"]["enum"]
            args["days"] = min(allowed, key=lambda d: abs(d - n))
    return args


def clean_args(skill_id: str, raw) -> dict:
    """Keep only the declared arguments that pass their schema (the AI's input is never trusted blindly)."""
    props = SKILL_ARGS.get(skill_id, {})
    out = {}
    for k, v in (raw or {}).items() if isinstance(raw, dict) else []:
        if k in props:
            try:
                out[k] = ai.validate(v, props[k])
            except ValueError:
                pass
    return {k: v for k, v in out.items() if v is not None}


def red_flag_reply(user, text: str):
    if user["role"] != "patient":
        return None
    hits = triage_rules.red_flags(text)
    return {**emergency_reply(hits), "skill": "emergency", "suggestions": []} if hits else None


def match(role: str, text: str):
    q = " " + re.sub(r"[^a-z0-9ऀ-ॿ ]+", " ", text.lower()) + " "
    best, score = None, 0
    for skill in skills_for(role):
        s = sum(len(k) for k in skill[4] if f" {k}" in q or k in q)
        if s > score:
            best, score = skill, s
    return best


def greeting(user) -> dict:
    first = user["name"].split()[0]
    chips = [s[5] for s in skills_for(user["role"])]
    return reply(f"Hi {first}! I can answer questions about your own Pyshio data. Try one of these:"), chips


def builtin_answer(user, text: str) -> dict:
    urgent = red_flag_reply(user, text)
    if urgent:
        return urgent
    t = text.strip().lower()
    if re.fullmatch(r"(hi|hello|hey|namaste|help|menu|start|\?)+[!. ]*", t):
        out, chips = greeting(user)
        return {**out, "suggestions": chips}
    skill = match(user["role"], text)
    if not skill:
        out, chips = greeting(user)
        out["text"] = "I didn't catch that. I can help with these:"
        return {**out, "suggestions": chips}
    try:
        out = skill[2](user, **extract_args(skill[0], text))
    except HTTPException as e:
        out = reply(e.detail if isinstance(e.detail, str) else "That isn't available for your account yet.")
    return {**out, "skill": skill[0], "suggestions": suggestions_after(user["role"], skill[0])}


# ====================================================================== Optional AI layer (Claude)
def ai_enabled() -> bool:
    return ai.enabled("assistant")


ROLE_LABEL = {"patient": "a patient", "doctor": "a doctor", "pharmacy": "a pharmacy", "lab": "a diagnostic lab",
              "company": "a pharma company admin", "mr": "a pharma medical representative", "admin": "the platform super admin"}


def tools_for(role: str) -> list:
    return [{"name": s[0], "description": s[3],
             "input_schema": {"type": "object", "properties": SKILL_ARGS.get(s[0], {})}} for s in skills_for(role)]


def system_prompt(user) -> list:
    # The stable part first (cached across users of this role), the user's name last
    rules = (f"You are the {settings.PLATFORM_NAME} assistant for {ROLE_LABEL[user['role']]}. "
             f"Answer only about this user's own {settings.PLATFORM_NAME} data, using the tools (they already return only this "
             "user's data). If a question needs data no tool provides, or is about another person or account, say you can't help "
             "with that here. Never give a diagnosis or change a treatment; for medical questions suggest speaking to their "
             "doctor, and for symptoms use symptom_check. Keep answers short (2-4 sentences), plain and friendly; amounts are in "
             "Indian rupees. Reply in the user's language (English or Hinglish). Don't repeat the full table; the app shows the "
             "tool's table below your answer.")
    return [{"type": "text", "text": rules}, {"type": "text", "text": f"The user's first name is {user['name'].split()[0]}."}]


def ai_answer(user, text: str, history: list) -> dict:
    urgent = red_flag_reply(user, text)
    if urgent:
        return urgent
    skills = {s[0]: s for s in skills_for(user["role"])}
    messages = [{"role": h.role, "content": h.text} for h in history[-6:]] + [{"role": "user", "content": text}]
    last_result, used = None, None
    for _ in range(4):
        res = ai.call("assistant", system=system_prompt(user), messages=messages, tools=tools_for(user["role"]),
                      max_tokens=600, user_id=user["id"])
        messages.append({"role": "assistant", "content": res["content"]})
        calls = [b for b in res["content"] if b["type"] == "tool_use"]
        if not calls:
            answer = ai.text_of(res)
            out = {"text": answer or "Sorry, I couldn't answer that.", "table": None, "links": []}
            if last_result:
                out.update(table=last_result.get("table"), links=last_result.get("links", []))
            return {**out, "skill": used, "suggestions": suggestions_after(user["role"], used)}
        results = []
        for call in calls:
            skill = skills.get(call["name"])
            args = clean_args(call["name"], call.get("input"))
            if call["name"] == "symptom_check" and not args.get("symptoms"):
                args["symptoms"] = text
            try:
                data = skill[2](user, **args) if skill else reply("Unknown tool")
            except HTTPException as e:
                data = reply(str(e.detail))
            last_result, used = data, call["name"]
            results.append({"type": "tool_result", "tool_use_id": call["id"], "content": json.dumps(data, default=str)[:12000]})
        messages.append({"role": "user", "content": results})
    raise ai.AIUnavailable("Too many tool rounds")
from features.family import logic as family  # noqa: E402
from features.follow_ups import logic as follow_ups  # noqa: E402
from features.medicine_reminders import logic as medicine_reminders  # noqa: E402


# Imported last, so slices can depend on each other in any order
from features.admin import api as admin_api  # noqa: E402
from features.admin_analytics import logic as admin_analytics  # noqa: E402
from features.appointments import api as appointments_api  # noqa: E402
from features.doctors import api as doctors_api  # noqa: E402
from features.health_records import logic as health_records  # noqa: E402
from features.labs import api as labs_api  # noqa: E402
from features.labs import logic as labs  # noqa: E402
from features.medicine_courses import api as medicine_courses_api  # noqa: E402
from features.medicine_courses import logic as medicine_courses  # noqa: E402
from features.medicine_orders import logic as medicine_orders_logic  # noqa: E402
from features.patient_history import api as patient_history_api  # noqa: E402
from features.patient_history import logic as patient_history  # noqa: E402
from features.pharma_insights import api as pharma_insights_api  # noqa: E402
from features.pharma_insights import logic as pharma_insights  # noqa: E402
from features.pharmacies import api as pharmacies_api  # noqa: E402
from features.pharmacy_offers import logic as pharmacy_offers  # noqa: E402
from features.prescription_uploads import api as prescription_uploads_api  # noqa: E402
from features.prescription_uploads import logic as prescription_uploads  # noqa: E402
from features.prescriptions import logic as prescriptions_logic  # noqa: E402
from features.settlements import logic as settlements  # noqa: E402

from features.lab_reader import logic as lab_reader  # noqa: E402
from features.pharma_nba import logic as pharma_nba  # noqa: E402
from features.risk_nudges import logic as risk_nudges  # noqa: E402
from features.triage import logic as triage  # noqa: E402

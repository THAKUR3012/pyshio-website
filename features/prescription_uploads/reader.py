"""Prescription uploads: reading the photo, so pharmacies and labs get pre-filled lines to check.

Runs after the upload (background task) or from the 5-minute cron when that didn't finish. Engines, in order:
  1. Claude vision, only when the patient agreed to automatic reading and the file can be sent;
  2. medicine / test names the patient typed;
  3. none ("skipped"): partners read the photo themselves, as before.
Every line is matched to the catalogue here; partners confirm each line and price before quoting. Nothing is sent
to the patient's pharmacy or lab automatically.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Optional

from shared import ai, clinical_terms, salts, settings
from shared.common import stamp
from shared.db import get_db

log = logging.getLogger("pyshio.rx_reader")
STALE_MINUTES = 10
MAX_ATTEMPTS = 3

READ_SCHEMA = {
    "type": "object",
    "properties": {
        "legible": {"type": "boolean", "description": "False when the image isn't a readable prescription."},
        "medicines": {"type": "array", "maxItems": 30, "items": {"type": "object", "properties": {
            "name": {"type": "string", "maxLength": 120}, "strength": {"type": ["string", "null"], "maxLength": 40},
            "form": {"type": ["string", "null"], "maxLength": 30}, "dosage": {"type": ["string", "null"], "maxLength": 60},
            "duration": {"type": ["string", "null"], "maxLength": 40}, "quantity": {"type": ["integer", "null"]}},
            "required": ["name"]}},
        "tests": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 120}},
    },
    "required": ["legible", "medicines", "tests"],
}
SYSTEM = ("You read photos of Indian doctors' prescriptions (often handwritten) for a pharmacy. List only the medicines and "
          "lab tests written on it, exactly as written; if a word is unreadable, leave it out rather than guess. Do not "
          "copy the patient's or doctor's name, phone, address or registration number. Reply only by calling the emit tool.")


def queue(conn, upload_id: str, record_id: int) -> None:
    conn.execute("INSERT OR IGNORE INTO ai_extractions (kind, source_id, record_id, status, created_at, updated_at) "
                 "VALUES ('rx_upload', ?, ?, 'pending', ?, ?)", (upload_id, record_id, stamp(), stamp()))


def _claim(conn, upload_id: str) -> bool:
    stale = stamp(settings.now() - timedelta(minutes=STALE_MINUTES))
    cur = conn.execute("UPDATE ai_extractions SET status = 'running', claimed_at = ?, attempts = attempts + 1, updated_at = ? "
                       "WHERE kind = 'rx_upload' AND source_id = ? AND attempts < ? AND "
                       "(status = 'pending' OR (status = 'running' AND claimed_at < ?))",
                       (stamp(), stamp(), upload_id, MAX_ATTEMPTS, stale))
    return bool(cur.rowcount)


def _finish(conn, upload_id: str, status: str, engine: Optional[str], result: Optional[dict]) -> None:
    conn.execute("UPDATE ai_extractions SET status = ?, engine = ?, result = ?, updated_at = ? WHERE kind = 'rx_upload' AND source_id = ?",
                 (status, engine, json.dumps(result) if result is not None else None, stamp(), upload_id))


def match_lines(conn, medicines: list, tests: list) -> dict:
    lines = []
    for m in medicines:
        found = salts.best_match(conn, m["name"], m.get("strength"), m.get("form"), limit=3)
        best = found[0] if found and found[0]["confidence"] >= 0.75 else None
        daily = daily_units_from(m.get("dosage"))
        days = days_from(m.get("duration"))
        qty = m.get("quantity") or (max(1, min(1000, round(daily * days))) if daily and days else None)
        lines.append({"written_as": m["name"], "name": best["name"] if best else m["name"],
                      "medicine_id": best["medicine_id"] if best else None, "confidence": best["confidence"] if best else None,
                      "generic_name": best["generic_name"] if best else None,
                      "strength": (best["strength"] if best else None) or m.get("strength"), "form": (best or m).get("form"),
                      "dosage": m.get("dosage"), "duration": m.get("duration"), "quantity": qty,
                      "pack_size": best["pack_size"] if best else None,
                      "alternatives": [{"medicine_id": f["medicine_id"], "name": f["name"], "strength": f["strength"]} for f in found[1:]]})
    out_tests = []
    for t in tests:
        hit = clinical_terms.match_test(conn, t)
        out_tests.append({"written_as": t, "test_id": hit["id"] if hit else None, "name": hit["name"] if hit else t})
    return {"medicines": lines, "tests": out_tests}


def parse_typed(conn, text: str) -> dict:
    """Names the patient typed: one per line or comma-separated; tests are recognised from the catalogue."""
    medicines, tests = [], []
    for raw in [p.strip() for chunk in (text or "").splitlines() for p in chunk.split(",")]:
        if not raw:
            continue
        if clinical_terms.match_test(conn, raw, cutoff=0.9):
            tests.append(raw)
            continue
        parsed = scribe.parse_medicine(conn, raw)
        medicines.append(parsed if parsed else {"name": raw})
    return {"medicines": medicines, "tests": tests}


def read_upload(conn, upload) -> tuple:
    """(status, engine, result) for one upload."""
    if upload["ai_read_consent"] and ai.enabled("rx_read"):
        record = conn.execute("SELECT file_path FROM health_records WHERE id = ?", (upload["record_id"],)).fetchone()
        data = health_records_logic.read_file(record["file_path"]) if record else None
        if data:
            try:
                out = ai.extract("rx_read", system=SYSTEM, content=[ai.file_block(data), ai.text("Read this prescription.")],
                                 schema=READ_SCHEMA, max_tokens=1500, timeout=60, user_id=upload["patient_id"])
                if out.get("legible") is False and not out.get("medicines") and not out.get("tests"):
                    return "done", "claude", {"legible": False, "medicines": [], "tests": []}
                return "done", "claude", {"legible": True, **match_lines(conn, out.get("medicines", []), out.get("tests", []))}
            except ai.AIUnavailable as e:
                log.info("Photo reading unavailable for %s: %s", upload["reference"], e)
    if upload["typed_lines"]:
        typed = parse_typed(conn, upload["typed_lines"])
        return "done", "typed", {"legible": True, **match_lines(conn, typed["medicines"], typed["tests"])}
    return "skipped", None, None


def run(upload_id: str) -> Optional[str]:
    """Read one upload if nobody else is; returns the final status (None when already taken)."""
    with get_db() as conn:
        if not _claim(conn, upload_id):
            return None
        upload = conn.execute("SELECT * FROM rx_uploads WHERE id = ?", (upload_id,)).fetchone()
        try:
            status, engine, result = read_upload(conn, upload) if upload else ("skipped", None, None)
        except Exception:
            log.exception("Reading upload %s failed", upload_id)
            attempts = conn.execute("SELECT attempts FROM ai_extractions WHERE kind = 'rx_upload' AND source_id = ?",
                                    (upload_id,)).fetchone()["attempts"]
            status, engine, result = ("failed" if attempts >= MAX_ATTEMPTS else "pending"), None, None
        _finish(conn, upload_id, status, engine, result)
        return status


def extraction(conn, upload_id: str) -> Optional[dict]:
    row = conn.execute("SELECT status, engine, result FROM ai_extractions WHERE kind = 'rx_upload' AND source_id = ?",
                       (upload_id,)).fetchone()
    if not row:
        return None
    return {"status": row["status"], "engine": row["engine"], **(json.loads(row["result"]) if row["result"] else {})}


def patient_view(conn, upload, user) -> Optional[dict]:
    """What we read, plus allergy and interaction warnings for the patient (only they see these)."""
    read = extraction(conn, upload["id"])
    if not read or read["status"] != "done":
        return read and {"status": read["status"]}
    profile = own_profile(conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone())
    items = [{"medicine_id": m["medicine_id"], "name": m["name"], "generic_name": m.get("generic_name"),
              "strength": m.get("strength"), "dosage": m.get("dosage")} for m in read.get("medicines", [])]
    safety = rx_safety.check(conn, items=items, patient_id=user["id"], age=profile["age"], allergies=profile["allergies"],
                             who="You") if items else {"alerts": []}
    warnings = [a["message"].replace(rx_safety.VERIFY, " Ask your doctor or pharmacist.")
                for a in safety["alerts"] if a["severity"] in ("major", "moderate")]
    return {"status": "done", "engine": read["engine"], "legible": read.get("legible", True),
            "medicines": [{k: m.get(k) for k in ("name", "written_as", "strength", "dosage", "duration", "quantity")}
                          for m in read.get("medicines", [])],
            "tests": [t["name"] for t in read.get("tests", [])], "warnings": warnings}


def suggested_lines(conn, req, partner) -> dict:
    """Pre-filled quote lines for one pharmacy or lab, priced from its own list (they confirm every line)."""
    read = extraction(conn, req["upload_id"])
    if not read or read["status"] != "done":
        return {"status": read["status"] if read else "none", "lines": []}
    lines = []
    if req["partner_role"] == "pharmacy":
        for m in read.get("medicines", []):
            line = {"name": m["name"], "written_as": m["written_as"], "medicine_id": m["medicine_id"],
                    "confidence": m["confidence"], "quantity": m["quantity"] or m.get("pack_size") or 1,
                    "unit_price": None, "mrp": None, "available": False, "substitute": None}
            if m["medicine_id"]:
                ids = pharmacy_offers.substitutes(conn, {"medicine_id": m["medicine_id"], "allow_substitute": 1})
                marks = ",".join("?" * len(ids))
                best = conn.execute(
                    f"SELECT s.medicine_id, s.price_paise, s.mrp_paise, m.pack_size, m.name FROM pharmacy_stock s "
                    f"JOIN medicines m ON m.id = s.medicine_id WHERE s.pharmacy_id = ? AND s.in_stock = 1 AND s.medicine_id IN ({marks}) "
                    f"ORDER BY CASE WHEN s.medicine_id = ? THEN 0 ELSE 1 END, s.price_paise * 1.0 / m.pack_size LIMIT 1",
                    (req["partner_id"], *ids, m["medicine_id"])).fetchone() if ids else None
                if best:
                    line.update(unit_price=round(best["price_paise"] / max(best["pack_size"], 1) / 100, 2),
                                mrp=round(best["mrp_paise"] / max(best["pack_size"], 1) / 100, 2), available=True,
                                substitute=best["name"] if best["medicine_id"] != m["medicine_id"] else None)
                    if line["substitute"]:
                        line["name"] = f"{best['name']} (for {m['name']})"
                        line["medicine_id"] = best["medicine_id"]
                else:
                    med = conn.execute("SELECT mrp_paise, pack_size FROM medicines WHERE id = ?", (m["medicine_id"],)).fetchone()
                    discount = partner["flat_discount_percent"]
                    if med and med["mrp_paise"] and discount is not None:
                        mrp_unit = med["mrp_paise"] / max(med["pack_size"], 1) / 100
                        line.update(unit_price=round(mrp_unit * (100 - discount) / 100, 2), mrp=round(mrp_unit, 2), available=True)
            lines.append(line)
    else:
        for t in read.get("tests", []):
            price = conn.execute("SELECT price_paise, mrp_paise FROM lab_prices WHERE lab_id = ? AND test_id = ? AND available = 1",
                                 (req["partner_id"], t["test_id"])).fetchone() if t["test_id"] else None
            lines.append({"name": t["name"], "written_as": t["written_as"], "test_id": t["test_id"], "quantity": 1,
                          "unit_price": round(price["price_paise"] / 100, 2) if price else None,
                          "mrp": round(price["mrp_paise"] / 100, 2) if price else None, "available": bool(price)})
    return {"status": "done", "engine": read["engine"], "legible": read.get("legible", True), "lines": lines}


def read_pending() -> int:
    """Cron fallback: read uploads whose background read never finished."""
    stale = stamp(settings.now() - timedelta(minutes=STALE_MINUTES))
    with get_db() as conn:
        ids = [r["source_id"] for r in conn.execute(
            "SELECT source_id FROM ai_extractions WHERE kind = 'rx_upload' AND attempts < ? AND "
            "(status = 'pending' OR (status = 'running' AND claimed_at < ?)) ORDER BY id LIMIT 5", (MAX_ATTEMPTS, stale))]
    return sum(1 for upload_id in ids if run(upload_id) in ("done", "skipped"))


# Imported last, so slices can depend on each other in any order
from features.ai_scribe import logic as scribe  # noqa: E402
from features.health_profile.logic import own_profile  # noqa: E402
from features.health_records import logic as health_records_logic  # noqa: E402
from features.pharmacy_offers import logic as pharmacy_offers  # noqa: E402
from features.prescriptions.logic import daily_units_from, days_from  # noqa: E402
from features.rx_safety import logic as rx_safety  # noqa: E402

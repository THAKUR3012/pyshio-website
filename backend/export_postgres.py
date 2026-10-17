"""Write postgres_schema.sql: every Pyshio table (latest version) as PostgreSQL 14+ DDL,
plus the reference data (conditions, lab tests, starter medicine catalogue).

    python export_postgres.py              # writes postgres_schema.sql
    psql -U USER -d DBNAME -f postgres_schema.sql

It builds a fresh, empty SQLite database with every upgrade applied and translates that, so the schema
always matches the latest code.
"""
from pathlib import Path
import os
import re
import sqlite3
import sys
import tempfile

OUT = Path(__file__).resolve().parent / "postgres_schema.sql"

# Columns holding a uuid / id stored as text
ID_TEXT = {"id", "prescription_id", "appointment_id", "order_id", "upload_id", "booking_id", "lab_booking_id",
           "upload_order_id", "parent_id", "subject_id", "root_id"}
LONG_TEXT = {"bio", "body", "note", "notes", "advice", "diagnosis", "detail", "reason", "instructions", "purpose", "products",
             "doctor_notes", "visit_note", "status_reason", "doctor_note", "value_text", "last_body", "address", "home_address",
             "visit_address", "delivery_address", "payload", "result", "typed_lines", "safety_alerts"}
FLAGS = {"is_active", "active", "available", "home_collection", "offers_home_visits", "accept_mr_meetings", "fasting",
         "delivery_available", "rx_required", "allow_substitute", "share_with_doctors", "want_medicines", "want_tests",
         "accepts_clinic_payment", "accepts_online_payment", "offers_delivery", "is_long_term", "in_stock", "ai_read_consent", "ok"}
DATE_ONLY = {"dob", "since", "record_date", "ends_on", "started_on", "preferred_date", "appointment_date", "block_date"}
TIME_ONLY = {"appointment_time", "preferred_time", "start_time", "end_time"}
SIZES = {"email": 254, "token_hash": 64, "reference": 20, "slug": 120, "code": 20, "role": 20, "status": 24, "kind": 24,
         "phone": 20, "pincode": 6, "state": 60, "city": 60, "category": 40, "sex": 10, "unit": 20, "strength": 40, "form": 20,
         "care_area": 40, "payment_method": 10, "payment_status": 20, "fulfilment": 10, "collection": 10, "visit_type": 10,
         "partner_role": 10, "author_role": 20, "uploader_role": 20, "target_role": 20, "outcome": 20, "flag": 10,
         "content_type": 60, "source": 20, "location_source": 20, "subject_kind": 20, "closed_by_role": 20, "cancelled_by": 20,
         "specialities": 1000, "care_areas": 1000}


def q(names) -> str:
    """Double-quote identifiers for PostgreSQL (preserves case, escapes reserved keywords)."""
    return ", ".join(f'"{n}"' for n in names)


def pg_type(table: str, col: dict, indexed: set, auto: bool) -> str:
    name, decl = col["name"], (col["type"] or "").upper()
    if decl == "INTEGER":
        if auto:
            return "SERIAL"
        if name in FLAGS:
            return "BOOLEAN"
        if name.endswith("_paise"):
            return "BIGINT"
        return "INTEGER"
    if decl == "REAL":
        return "DOUBLE PRECISION"
    # TEXT types
    if name in ID_TEXT or (col["pk"] and name == "id"):
        return "VARCHAR(36)"
    if name in DATE_ONLY:
        return "VARCHAR(10)"   # ISO YYYY-MM-DD
    if name in TIME_ONLY:
        return "VARCHAR(5)"    # HH:MM
    if name.endswith("_at") or name in ("valid_until", "expires_at", "hit_at", "failed_at", "scheduled_for", "refill_until"):
        return "VARCHAR(19)"   # ISO YYYY-MM-DDTHH:MM:SS
    if name in SIZES:
        return f"VARCHAR({SIZES[name]})"
    if name in LONG_TEXT and name not in indexed:
        return "TEXT"
    return "VARCHAR(255)"


def checks_for(sql: str) -> dict:
    """Extract column-level CHECK (...) clauses."""
    out = {}
    for m in re.finditer(r"\n\s*(\w+)\s+[A-Z]+[^,]*?CHECK\s*\(", sql):
        start = m.end() - 1
        depth, i = 0, start
        while i < len(sql):
            if sql[i] == "(":
                depth += 1
            elif sql[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out[m.group(1)] = "CHECK " + " ".join(sql[start:i + 1].split())
    return out


def default_sql(value, ptype: str, col_name: str):
    if value is None or ptype == "SERIAL":
        return ""
    if ptype == "BOOLEAN":
        val_str = str(value).strip().strip("'\"")
        return " DEFAULT TRUE" if val_str in ("1", "true", "TRUE") else " DEFAULT FALSE"
    return f" DEFAULT {value}"


def build() -> tuple[str, int, int]:
    tmp = Path(tempfile.mkdtemp()) / "fresh.db"
    os.environ["PYSHIO_DB"] = str(tmp)
    os.environ.setdefault("PYSHIO_ENV_FILE", str(tmp.parent / "none.env"))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from shared import db
    from features.medicines import logic as medicines
    db.init_db()
    medicines.seed_catalogue()
    conn = sqlite3.connect(tmp)
    conn.row_factory = sqlite3.Row
    tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                                               "AND name NOT LIKE 'legacy_%' ORDER BY name")]
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    lines = [
        "-- Pyshio database schema for PostgreSQL 14+.",
        f"-- Generated by export_postgres.py from schema version {version}. Tables: {len(tables)}.",
        "-- Dates/times are stored as ISO text (YYYY-MM-DDTHH:MM:SS, YYYY-MM-DD, HH:MM).",
        "-- Money columns ending in _paise are integer paise (100 paise = 1 rupee); appointment fees are whole rupees.",
        "",
        "SET client_encoding = 'UTF8';",
        "SET standard_conforming_strings = on;",
        ""
    ]

    post_table_indexes = []
    post_table_foreign_keys = []

    for t in tables:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (t,)).fetchone()["sql"]
        cols = [dict(r) for r in conn.execute(f"PRAGMA table_info({t})")]
        idx_rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL",
                                (t,)).fetchall()
        auto_uniques = [r for r in conn.execute(f"PRAGMA index_list({t})") if r["origin"] == "u"]
        indexed = set()
        
        for r in idx_rows:
            m = re.match(r"CREATE\s+(UNIQUE\s+)?INDEX\s+(\w+)\s+ON\s+\w+\s*\((.*)\)\s*$", " ".join(r["sql"].split()), re.I)
            if not m:
                continue
            unique, name, inner = bool(m.group(1)), m.group(2), m.group(3)
            # handle lower(col) or expressions in postgres
            cleaned_inner = re.sub(r"COLLATE\s+\w+", "", inner, flags=re.I)
            names = re.findall(r"\b([a-z_]+)\b", re.sub(r"(lower|COALESCE|''|')", " ", cleaned_inner))
            names = [n for n in dict.fromkeys(names) if n in {c["name"] for c in cols}]
            indexed.update(names)
            idx_cols = ", ".join(f'"{n}"' for n in names) if names else inner
            unique_str = "UNIQUE " if unique else ""
            post_table_indexes.append(f'CREATE {unique_str}INDEX IF NOT EXISTS "{name}" ON "{t}" ({idx_cols});')

        for u in auto_uniques:
            names = [r["name"] for r in conn.execute(f"PRAGMA index_info({u['name']})")]
            indexed.update(names)

        fks = conn.execute(f"PRAGMA foreign_key_list({t})").fetchall()
        indexed.update(fk["from"] for fk in fks)
        checks = checks_for(sql)
        pk_cols = [c["name"] for c in sorted(cols, key=lambda c: c["pk"]) if c["pk"]]
        auto = "AUTOINCREMENT" in sql.upper() and len(pk_cols) == 1

        defs = []
        for c in cols:
            is_auto_pk = auto and (c["name"] == pk_cols[0])
            ptype = pg_type(t, c, indexed, is_auto_pk)
            part = f'  "{c["name"]}" {ptype}'
            if (c["notnull"] or c["pk"]) and ptype != "SERIAL":
                part += " NOT NULL"
            part += default_sql(c["dflt_value"], ptype, c["name"])
            single_unique = any(len(list(conn.execute(f"PRAGMA index_info({u['name']})"))) == 1 and
                                next(iter(conn.execute(f"PRAGMA index_info({u['name']})")))["name"] == c["name"] for u in auto_uniques)
            if single_unique:
                part += " UNIQUE"
            if c["name"] in checks:
                check_clause = checks[c["name"]]
                # Convert any integer 0/1 in checks for boolean columns
                if ptype == "BOOLEAN":
                    check_clause = check_clause.replace(" IN (0, 1)", " IN (TRUE, FALSE)")
                part += " " + check_clause
            defs.append(part)

        if pk_cols and not auto:
            defs.append(f"  PRIMARY KEY ({q(pk_cols)})")
        elif pk_cols and auto:
            defs.append(f"  PRIMARY KEY ({q(pk_cols)})")

        for u in auto_uniques:
            names = [r["name"] for r in conn.execute(f"PRAGMA index_info({u['name']})")]
            if len(names) > 1:
                key_name = ("uq_" + t + "_" + "_".join(names))[:60]
                defs.append(f'  CONSTRAINT "{key_name}" UNIQUE ({q(names)})')

        groups = {}
        for fk in fks:
            groups.setdefault(fk["id"], []).append(fk)
        for gid, parts in groups.items():
            frm = q([p["from"] for p in parts])
            to = q([p["to"] or "id" for p in parts])
            on_delete = parts[0]["on_delete"]
            post_table_foreign_keys.append(
                f'ALTER TABLE "{t}" ADD CONSTRAINT "fk_{t}_{gid}" '
                f'FOREIGN KEY ({frm}) REFERENCES "{parts[0]["table"]}" ({to})'
                + (f" ON DELETE {on_delete}" if on_delete and on_delete != "NO ACTION" else "")
                + ";"
            )

        lines.append(f'CREATE TABLE "{t}" (\n' + ",\n".join(defs) + "\n);\n")

    lines.append("-- ---------------------------------------------------------------- foreign keys (added after all tables exist)")
    lines.extend(post_table_foreign_keys)
    lines.append("")

    lines.append("-- ---------------------------------------------------------------- indexes")
    lines.extend(post_table_indexes)
    lines.append("")

    # Reference data the app expects to exist
    def inserts(table, columns):
        rows = conn.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY id").fetchall()
        if not rows:
            return
        lines.append(f"-- {table}: {len(rows)} rows")
        values = []

        for r in rows:
            vals = []
            for col, v in zip(columns, r):
                if v is None:
                    vals.append("NULL")
                elif col in {"fasting", "rx_required"}:
                    vals.append("TRUE" if bool(v) else "FALSE")
                elif isinstance(v, bool):
                    vals.append("TRUE" if v else "FALSE")
                elif isinstance(v, (int, float)):
                    vals.append(str(v))
                else:
                    vals.append("'" + str(v).replace("'", "''") + "'")
            values.append("(" + ", ".join(vals) + ")")

         
        lines.append(f'INSERT INTO "{table}" ({q(columns)}) VALUES\n' + ",\n".join(values) + "\nON CONFLICT DO NOTHING;\n")
        # Reset sequence for PostgreSQL SERIAL column
        lines.append(f"SELECT setval(pg_get_serial_sequence('\"{table}\"', 'id'), COALESCE((SELECT MAX(id) FROM \"{table}\"), 1));\n")

    lines.append("-- ---------------------------------------------------------------- reference data")
    inserts("conditions", ["id", "code", "name", "care_area"])
    inserts("lab_tests", ["id", "name", "code", "category", "sample_type", "fasting", "mrp_paise", "unit", "ref_low", "ref_high", "created_at"])
    inserts("medicines", ["id", "name", "generic_name", "strength", "form", "pack_size", "rx_required", "created_at", "care_area"])
    conn.close()
    return "\n".join(lines) + "\n", len(tables), version


if __name__ == "__main__":
    text, n, version = build()
    OUT.write_text(text, encoding="utf-8")
    print(f"Wrote {OUT.name}: {n} tables (schema version {version}), {len(text) // 1024} KB")
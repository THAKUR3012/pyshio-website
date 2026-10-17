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

        lines.append(f'DROP TABLE IF EXISTS "{t}" CASCADE;')
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
            for v in r:
                if v is None:
                    vals.append("NULL")
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

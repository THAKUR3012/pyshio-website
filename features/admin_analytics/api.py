"""Admin analytics: disease, age, city, state and medicine analysis for the super admin.

This file: HTTP endpoints and request models.
"""
import csv
import io
from fastapi import APIRouter, Depends, HTTPException, Response

from shared import reference_data as ref, settings
from shared.auth import admin_user
from shared.common import csv_safe, stamp
from shared.db import get_db

from features.admin_analytics.logic import Filters, filters

router = APIRouter()


@router.get("/api/admin/analytics")
def admin_analytics(f: Filters = Depends(filters), user: dict = Depends(admin_user)):
    with get_db() as conn:
        return build(conn, f)


@router.get("/api/admin/analytics/options")
def analytics_options(user: dict = Depends(admin_user)):
    """Values for the filter drop-downs."""
    with get_db() as conn:
        cities = [r[0] for r in conn.execute(
            "SELECT DISTINCT city FROM (SELECT city FROM users WHERE city IS NOT NULL UNION SELECT city FROM doctors) "
            "ORDER BY city")]
        conds = [dict(r) for r in conn.execute("SELECT id, code, name, care_area FROM conditions ORDER BY name")]
    return {"states": ref.INDIAN_STATES, "cities": cities, "care_areas": ref.CARE_AREAS, "conditions": conds,
            "age_bands": band_labels(), "sexes": SEXES}


@router.get("/api/admin/analytics/{view}.csv")
def analytics_csv(view: str, f: Filters = Depends(filters), user: dict = Depends(admin_user)):
    if view not in EXPORTS:
        raise HTTPException(status_code=404, detail="Unknown report")
    key, columns = EXPORTS[view]
    with get_db() as conn:
        rows = build(conn, f)[key]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Sr No"] + [c.replace("_", " ").title() for c in columns])
    n = 0
    hidden = f"<{settings.MIN_GROUP_SIZE}"
    for r in rows:
        values = [r.get(c) for c in columns]
        # Small groups never leave the platform: rows with a hidden total are dropped, hidden cells left blank
        lead = r.get("cases", r.get("prescriptions", r.get("total")))
        if lead == hidden:
            continue
        n += 1
        writer.writerow([n] + ["" if x == hidden or x is None else csv_safe(x) for x in values])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="analytics-{view}-{stamp()[:10]}.csv"'})


# Imported last, so slices can depend on each other in any order
from features.admin_analytics.logic import band_labels, build, EXPORTS, SEXES  # noqa: E402

"""Web pages: the HTML shells for each stakeholder, plus robots.txt and the sitemap.

This file: HTTP endpoints and request models.
"""
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from typing import Optional

from shared import auth, settings
from shared.db import get_db

router = APIRouter()


# ---------- Pages ----------
PAGES = {"/": "index.html", "/login": "login.html", "/account": "account.html",
         "/doctor": "portal.html", "/admin": "admin.html", "/pharmacy": "pharmacy.html",
         "/invoice": "invoice.html", "/privacy": "privacy.html", "/terms": "terms.html",
         "/company": "company.html", "/mr": "mr.html", "/lab": "lab.html"}


def _page(name: str):
    def handler():
        return FileResponse(settings.WEB_DIR / name)
    return handler


for _path, _file in PAGES.items():
    router.add_api_route(_path, _page(_file), methods=["GET"], include_in_schema=False)


@router.get("/doctors/{slug}", include_in_schema=False)
def doctor_page(slug: str, user: Optional[dict] = Depends(auth.current_user)):
    with get_db() as conn:
        row = conn.execute(DOCTOR_SELECT + " WHERE d.slug = ?", (slug,)).fetchone()
    visible = row and row["status"] == "approved" and row["is_active"]
    owner = row and user and (user["role"] == "admin" or user["id"] == row["user_id"])
    if not (visible or owner):
        raise HTTPException(status_code=404)  # real 404 so search engines drop dead links
    return FileResponse(settings.WEB_DIR / "doctor.html")


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    return RedirectResponse("/assets/favicon.svg", status_code=301)


@router.get("/robots.txt", include_in_schema=False)
def robots():
    lines = ["User-agent: *", "Disallow: /api/", "Disallow: /account", "Disallow: /doctor", "Disallow: /pharmacy",
             "Disallow: /admin", "Disallow: /login", "Disallow: /invoice", "Disallow: /prescriptions/"]
    if settings.SITE_URL:
        lines.append(f"Sitemap: {settings.SITE_URL}/sitemap.xml")
    return PlainTextResponse("\n".join(lines) + "\n")


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap():
    """Public pages + every approved doctor's profile, so search engines can find them."""
    base = settings.SITE_URL or ""
    with get_db() as conn:
        doctors = conn.execute("SELECT d.slug, COALESCE(d.updated_at, d.created_at) AS changed FROM doctors d "
                               "JOIN users u ON u.id = d.user_id WHERE d.status = 'approved' AND u.is_active = 1").fetchall()
    urls = [(f"{base}/", None), (f"{base}/privacy", None), (f"{base}/terms", None)] +            [(f"{base}/doctors/{r['slug']}", r["changed"][:10]) for r in doctors]
    body = "".join(f"<url><loc>{loc}</loc>{f'<lastmod>{mod}</lastmod>' if mod else ''}</url>" for loc, mod in urls)
    return Response(content='<?xml version="1.0" encoding="UTF-8"?>'
                            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>',
                    media_type="application/xml")


@router.get("/prescriptions/{rx_id}", include_in_schema=False)
def prescription_page(rx_id: str):
    return FileResponse(settings.WEB_DIR / "rx.html")


# Imported last, so slices can depend on each other in any order
from features.doctors.logic import DOCTOR_SELECT  # noqa: E402

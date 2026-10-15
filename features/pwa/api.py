"""Installable app: web manifest, service worker, icons and the offline page.

This file: HTTP endpoints.
"""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from shared import settings

router = APIRouter()
STATIC = Path(__file__).resolve().parent / "static"


@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return JSONResponse({
        "name": settings.PLATFORM_NAME,
        "short_name": settings.PLATFORM_NAME,
        "description": "Doctors, medicines, lab tests and your health records in one place.",
        "start_url": "/login?source=app",
        "scope": "/",
        "display": "standalone",
        "background_color": "#f8fafc",
        "theme_color": "#0284c7",
        "icons": [
            {"src": "/features/pwa/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/features/pwa/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/features/pwa/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Find a doctor", "url": "/"},
            {"name": "My appointments", "url": "/account#appointments"},
            {"name": "Today's medicines", "url": "/account#today"},
        ],
    }, media_type="application/manifest+json")


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    """Served from the site root so it can look after every page."""
    return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


@router.get("/offline", include_in_schema=False)
def offline_page():
    return FileResponse(STATIC / "offline.html")

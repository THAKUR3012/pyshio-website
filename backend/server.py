"""Pyshio web app: puts the feature slices together.

Each feature lives in features/<slice>/ (see features/__init__.py); this file only creates the FastAPI app,
registers every slice's routes and static files, handles errors and prepares the database.

    python server.py          # local: http://localhost:8080
"""
import logging
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from shared import auth, settings  # settings loads .env first
from shared.db import init_db
import features
from features.medicines import logic as medicines

log = logging.getLogger("pyshio")
app = FastAPI(title=f"{settings.PLATFORM_NAME} API")

# ---------- CORS (allows frontend on Vercel / custom domains) ----------
cors_origins_env = os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://localhost:8080")
cors_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=r"^https:\/\/.*\.vercel\.app$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Errors ----------
@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    """API callers get JSON; people browsing get a friendly 404 page (with a real 404 status)."""
    if exc.status_code == 404 and not request.url.path.startswith("/api/"):
        not_found_page = settings.WEB_DIR / "404.html"
        if not_found_page.is_file():
            return FileResponse(not_found_page, status_code=404)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    messages = []
    for err in exc.errors():
        msg = err.get("msg", "Invalid value").removeprefix("Value error, ")
        field = err["loc"][-1] if err.get("loc") else ""
        messages.append(msg if err.get("type") == "value_error" else f"{field}: {msg}")
    return JSONResponse(status_code=422, content={"detail": "; ".join(messages)})


# ---------- Slices ----------
for _slice, _api in features.load_all():
    app.include_router(_api.router)
for _slice, _folder in features.static_dirs():
    app.mount(f"/features/{_slice}", StaticFiles(directory=_folder), name=f"static-{_slice}")
assets_dir = settings.WEB_DIR / "assets"
if assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


# ---------- Scheduled Jobs Endpoint (Vercel Cron / Render Webhook) ----------
@app.api_route("/api/cron/jobs", methods=["GET", "POST"], include_in_schema=False)
def cron_jobs(request: Request):
    cron_secret = os.environ.get("CRON_SECRET")
    if cron_secret:
        auth_header = request.headers.get("Authorization", "")
        if auth_header != f"Bearer {cron_secret}" and request.query_params.get("key") != cron_secret:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    import jobs
    summary = jobs.run_all()
    return JSONResponse(content={"ok": True, "summary": summary})


init_db()
auth.ensure_admin()
medicines.seed_catalogue()

def get_free_port(preferred: int) -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    for p in range(preferred + 1, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return preferred


if __name__ == "__main__":
    import uvicorn
    if "PORT" in os.environ:
        port = int(os.environ["PORT"])
    else:
        port = get_free_port(8080)
        if port != 8080:
            print(f"\n--> Port 8080 is in use; automatically using free port http://localhost:{port}\n")
    uvicorn.run(app, host="0.0.0.0", port=port)


"""Password hashing, cookie sessions, password resets and role checks."""
import hashlib
import hmac
import secrets
from datetime import timedelta
from fastapi import Cookie, Depends, HTTPException, Response
from typing import Optional

from shared import settings
from shared.db import get_db, write_txn


COOKIE_NAME = "pyshio_session"
PBKDF2_ITERATIONS = 260_000
MAX_FAILURES = 8          # per email
MAX_IP_FAILURES = 30      # per IP address, so one attacker can't lock out many accounts unnoticed
LOCKOUT_MINUTES = 15
# Used when the email doesn't exist, so a wrong email takes as long as a wrong password
_DUMMY_HASH = None


# ---------- Passwords ----------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
    except (ValueError, AttributeError):
        return False
    if algo != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(candidate.hex(), digest)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _stamp(dt) -> str:
    return dt.isoformat(timespec="seconds")


def create_user(conn, role: str, data, **extra) -> int:
    """Insert a user from a validated registration model (name, email, phone, password)."""
    if conn.execute("SELECT 1 FROM users WHERE email = ?", (data.email,)).fetchone():
        raise HTTPException(status_code=409, detail="An account with this email already exists. Please log in.")
    cols = {"role": role, "name": data.name, "email": data.email, "phone": data.phone,
            "password_hash": hash_password(data.password), "created_at": _stamp(settings.now()), **extra}
    cur = conn.execute(f"INSERT INTO users ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                       tuple(cols.values()))
    return cur.lastrowid


# ---------- Rate limits ----------
def rate_limited(conn, key: str, limit: int, window_minutes: int, record: bool = True) -> bool:
    """True if `key` has already been hit `limit` times in the window; otherwise records this hit."""
    since = _stamp(settings.now() - timedelta(minutes=window_minutes))
    count = conn.execute("SELECT COUNT(*) FROM rate_limits WHERE key = ? AND hit_at > ?", (key, since)).fetchone()[0]
    if count >= limit:
        return True
    if record:
        conn.execute("INSERT INTO rate_limits (key, hit_at) VALUES (?, ?)", (key, _stamp(settings.now())))
    return False


# ---------- Login / sessions ----------
def is_locked_out(conn, email: str) -> bool:
    since = _stamp(settings.now() - timedelta(minutes=LOCKOUT_MINUTES))
    count = conn.execute("SELECT COUNT(*) FROM login_failures WHERE email = ? AND failed_at > ?",
                         (email, since)).fetchone()[0]
    return count >= MAX_FAILURES


def authenticate(email: str, password: str, ip: str = None) -> dict:
    global _DUMMY_HASH
    with get_db() as conn:
        too_many = HTTPException(status_code=429,
                                 detail=f"Too many attempts. Try again in {LOCKOUT_MINUTES} minutes or reset your password.")
        if is_locked_out(conn, email):
            raise too_many
        if ip and rate_limited(conn, f"login-fail:{ip}", MAX_IP_FAILURES, LOCKOUT_MINUTES, record=False):
            raise too_many
        user = conn.execute("SELECT * FROM users WHERE email = ? AND deleted_at IS NULL", (email,)).fetchone()
        if not user:
            _DUMMY_HASH = _DUMMY_HASH or hash_password(secrets.token_hex(8))
            verify_password(password, _DUMMY_HASH)
        if not user or not verify_password(password, user["password_hash"]):
            conn.execute("INSERT INTO login_failures (email, failed_at) VALUES (?, ?)", (email, _stamp(settings.now())))
            if ip:
                rate_limited(conn, f"login-fail:{ip}", MAX_IP_FAILURES, LOCKOUT_MINUTES)
            raise HTTPException(status_code=401, detail="Incorrect email or password")
        if not user["is_active"]:
            raise HTTPException(status_code=403, detail="This account has been disabled")
        conn.execute("DELETE FROM login_failures WHERE email = ?", (email,))
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_stamp(settings.now()), user["id"]))
        return dict(user)


def start_session(response: Response, user_id: int) -> None:
    token = secrets.token_urlsafe(32)
    now = settings.now()
    with get_db() as conn:
        conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                     (_token_hash(token), user_id, _stamp(now), _stamp(now + timedelta(days=settings.SESSION_DAYS))))
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_stamp(now),))
    response.set_cookie(
        COOKIE_NAME, token, max_age=settings.SESSION_DAYS * 86400, httponly=True, samesite="lax",
        secure=settings.SITE_URL.startswith("https://"), path="/",
    )


def end_session(response: Response, token: Optional[str]) -> None:
    if token:
        with get_db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
    response.delete_cookie(COOKIE_NAME, path="/")


def end_other_sessions(user_id: int, keep_token: Optional[str] = None) -> None:
    """Sign the user out everywhere except (optionally) the current browser."""
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                     (user_id, _token_hash(keep_token) if keep_token else ""))


def current_user(pyshio_session: Optional[str] = Cookie(default=None)) -> Optional[dict]:
    if not pyshio_session:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ? AND u.is_active = 1",
            (_token_hash(pyshio_session), _stamp(settings.now()))
        ).fetchone()
    return dict(row) if row else None


def require(*roles: str):
    """Dependency: a logged-in user, optionally limited to some roles."""
    def dependency(user: Optional[dict] = Depends(current_user)) -> dict:
        if not user:
            raise HTTPException(status_code=401, detail="Please log in")
        if roles and user["role"] not in roles:
            raise HTTPException(status_code=403, detail="You don't have access to this")
        return user
    return dependency


# ---------- Password reset ----------
def create_reset_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = settings.now() + timedelta(hours=settings.PASSWORD_RESET_HOURS)
    with get_db() as conn:
        # Only the newest link works
        conn.execute("UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
                     (_stamp(settings.now()), user_id))
        conn.execute("INSERT INTO password_resets (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                     (_token_hash(token), user_id, _stamp(expires)))
    return token


def reset_password(token: str, new_password: str) -> int:
    with get_db() as conn:
        with write_txn(conn):
            row = conn.execute(
                "SELECT * FROM password_resets WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
                (_token_hash(token), _stamp(settings.now()))
            ).fetchone()
            if not row:
                raise HTTPException(status_code=400, detail="This reset link is invalid or has expired")
            conn.execute("UPDATE password_resets SET used_at = ? WHERE token_hash = ?",
                         (_stamp(settings.now()), row["token_hash"]))
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                         (hash_password(new_password), row["user_id"]))
            # Log out everywhere, e.g. if the old password was stolen
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))
            conn.execute("DELETE FROM login_failures WHERE email = (SELECT email FROM users WHERE id = ?)",
                         (row["user_id"],))
    return row["user_id"]


def ensure_admin() -> None:
    """Create the first admin from PYSHIO_ADMIN_EMAIL / PYSHIO_ADMIN_PASSWORD if missing."""
    if not (settings.ADMIN_EMAIL and settings.ADMIN_PASSWORD):
        return
    with get_db() as conn:
        exists = conn.execute("SELECT id FROM users WHERE email = ?", (settings.ADMIN_EMAIL,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO users (role, name, email, password_hash, created_at) VALUES ('admin', 'Administrator', ?, ?, ?)",
                (settings.ADMIN_EMAIL, hash_password(settings.ADMIN_PASSWORD), _stamp(settings.now()))
            )


# ---------- Doctor portal ----------
doctor_user = require("doctor")


# ---------- Admin ----------
admin_user = require("admin")


pharmacy_user = require("pharmacy")
patient_user = require("patient")


lab_user = require("lab")


partner_user = require("pharmacy", "lab")


chat_user = require("patient", "pharmacy", "lab")


company_user = require("company")
mr_user = require("mr")


any_user = require()

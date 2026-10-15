"""Notifications: the bell in the top bar for every role.

This file: logic and queries.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from html import unescape
from typing import Optional

from shared import mailer, settings
from shared.common import stamp
from shared.db import get_db


log = logging.getLogger("pyshio.notifications")


LINK_MARK = re.compile(r"<!--pyshio-link:(/[^\s>]*)-->")
DASHBOARDS = {"patient": "/account", "doctor": "/doctor", "pharmacy": "/pharmacy", "admin": "/admin",
              "company": "/company", "mr": "/mr", "lab": "/lab"}
# Same title for the same person within this window is treated as a repeat (e.g. a retried reminder)
REPEAT_HOURS = 6
KINDS = [("appointment", ("appointment", "home visit", "video", "consult")), ("order", ("order", "medicine", "pharmacy")),
         ("lab", ("lab", "report", "test", "sample")), ("quote", ("quote", "quotation", "offer", "cheaper")),
         ("prescription", ("prescription", "refill", "follow-up")), ("reminder", ("reminder", "today's medicines")),
         ("account", ("approved", "account", "welcome", "profile", "rejected", "suspended"))]


def kind_for(title: str) -> str:
    low = title.lower()
    for kind, words in KINDS:
        if any(w in low for w in words):
            return kind
    return "update"


def summary(text: str) -> str:
    """The email's intro paragraph (or its first details) as a short one-line body."""
    lines = (text or "").splitlines()[2:]  # skip headline + blank
    picked = []
    for line in lines:
        if not line.strip():
            if picked:
                break
            continue
        picked.append(line.strip())
        if len(picked) >= 3:
            break
    body = " · ".join(picked)
    return body[:237] + "…" if len(body) > 240 else body


def add(conn, user_id: int, title: str, body: str = None, link: str = None, kind: str = None) -> Optional[int]:
    """Store one notification (inside the caller's connection). Skips a repeat of the same title within a few hours."""
    since = stamp(settings.now() - timedelta(hours=REPEAT_HOURS))
    if conn.execute("SELECT 1 FROM notifications WHERE user_id = ? AND title = ? AND created_at >= ?",
                    (user_id, title, since)).fetchone():
        return None
    cur = conn.execute("INSERT INTO notifications (user_id, kind, title, body, link, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (user_id, kind or kind_for(title), title[:200], body, link, stamp()))
    return cur.lastrowid


def from_email(to: str, subject: str, text: str, html: str = None) -> None:
    """Mirror an outgoing email into the recipient's notifications. Never raises."""
    if not to:
        return
    try:
        match = LINK_MARK.search(html or "")
        link = unescape(match.group(1)) if match else None
        with get_db() as conn:
            user = conn.execute("SELECT id, role FROM users WHERE lower(email) = lower(?) AND deleted_at IS NULL",
                                (to,)).fetchone()
            if not user:
                return
            add(conn, user["id"], subject, summary(text), link or DASHBOARDS.get(user["role"], "/"))
    except Exception:
        log.exception("Couldn't store notification for %s", to)


def view(row) -> dict:
    return {"id": row["id"], "kind": row["kind"], "title": row["title"], "body": row["body"], "link": row["link"],
            "created_at": row["created_at"], "read": row["read_at"] is not None}


mailer.LISTENERS.append(from_email)

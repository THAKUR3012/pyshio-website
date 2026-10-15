"""Chats between a patient and the pharmacy or lab they chose.

This file: logic and queries.
"""
from __future__ import annotations

from fastapi import HTTPException
from typing import Optional

from shared.common import stamp


def open_conversation(conn, patient_id: int, partner_role: str, partner_id: int, subject_kind: str, subject_id: str,
                      title: str, intro: str) -> int:
    """Start (or find) the chat for an order, inside the caller's transaction. Returns its id."""
    row = conn.execute("SELECT id FROM conversations WHERE subject_kind = ? AND subject_id = ?", (subject_kind, subject_id)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO conversations (patient_id, partner_role, partner_id, subject_kind, subject_id, title, status, "
                       "created_at, last_message_at) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                       (patient_id, partner_role, partner_id, subject_kind, subject_id, title, stamp(), stamp()))
    system_message(conn, cur.lastrowid, intro)
    return cur.lastrowid


def system_message(conn, conversation_id: int, body: str) -> None:
    conn.execute("INSERT INTO conversation_messages (conversation_id, author_role, body, created_at) VALUES (?, 'system', ?, ?)",
                 (conversation_id, body, stamp()))
    conn.execute("UPDATE conversations SET last_message_at = ? WHERE id = ?", (stamp(), conversation_id))


def note_on_order(conn, subject_kind: str, subject_id: str, body: str) -> None:
    """Post an order update (accepted, ready, report uploaded…) into its chat, if there is one."""
    row = conn.execute("SELECT id FROM conversations WHERE subject_kind = ? AND subject_id = ?", (subject_kind, subject_id)).fetchone()
    if row:
        system_message(conn, row["id"], body)


SELECT = """SELECT c.*, pu.name AS patient_name, COALESCE(ph.store_name, l.lab_name) AS partner_name,
                   (SELECT body FROM conversation_messages m WHERE m.conversation_id = c.id ORDER BY m.id DESC LIMIT 1) AS last_body
            FROM conversations c JOIN users pu ON pu.id = c.patient_id
            LEFT JOIN pharmacies ph ON ph.user_id = c.partner_id AND c.partner_role = 'pharmacy'
            LEFT JOIN labs l ON l.user_id = c.partner_id AND c.partner_role = 'lab'"""


def mine(user: dict) -> tuple:
    """(SQL condition, my read-marker column, the other side's read-marker column)."""
    if user["role"] == "patient":
        return "c.patient_id = ?", "patient_read_at", "partner_read_at"
    return "c.partner_id = ?", "partner_read_at", "patient_read_at"


def unread(conn, c, user: dict) -> int:
    # The *_read_at columns hold the id of the last message that side has seen
    my_read = c["patient_read_at"] if user["role"] == "patient" else c["partner_read_at"]
    return conn.execute("SELECT COUNT(*) FROM conversation_messages WHERE conversation_id = ? AND author_role NOT IN (?, 'system') "
                        "AND id > ?", (c["id"], user["role"], int(my_read or 0))).fetchone()[0]


def last_id(conn, conversation_id: int) -> int:
    return conn.execute("SELECT COALESCE(MAX(id), 0) FROM conversation_messages WHERE conversation_id = ?", (conversation_id,)).fetchone()[0]


def view(conn, c, user: dict) -> dict:
    return {"id": c["id"], "title": c["title"], "status": c["status"], "closed_by_role": c["closed_by_role"],
            "subject_kind": c["subject_kind"], "subject_id": c["subject_id"], "created_at": c["created_at"],
            "last_message_at": c["last_message_at"], "last_body": c["last_body"],
            "with_name": c["partner_name"] if user["role"] == "patient" else c["patient_name"],
            "with_role": c["partner_role"] if user["role"] == "patient" else "patient",
            "unread": unread(conn, c, user)}


def conversation_for(conn, conversation_id: int, user: dict):
    cond, _, _ = mine(user)
    row = conn.execute(SELECT + f" WHERE c.id = ? AND {cond}", (conversation_id, user["id"])).fetchone()
    if not row or (user["role"] != "patient" and row["partner_role"] != user["role"]):
        raise HTTPException(status_code=404, detail="Chat not found")
    return row


ORDER_NOTES = {"accepted": "The {who} accepted the order.", "rejected": "The {who} couldn't take the order: {reason}",
               "ready": "Ready. Show your handover code when you collect.", "out_for_delivery": "Out for delivery. Keep your handover code ready.",
               "sample_collected": "Sample collected. The report will be shared here and in Health records.",
               "report_ready": "Report uploaded. Open it in Health records.", "completed": "Completed. Thank you!",
               "cancelled": "The patient cancelled the order."}


def order_note(status: str, who: str, reason: str = None) -> str:
    return ORDER_NOTES.get(status, f"Status: {status}").format(who=who, reason=reason or "")


def chat_for_order(conn, subject_kind: str, subject_id: str) -> Optional[int]:
    row = conn.execute("SELECT id FROM conversations WHERE subject_kind = ? AND subject_id = ?", (subject_kind, subject_id)).fetchone()
    return row["id"] if row else None

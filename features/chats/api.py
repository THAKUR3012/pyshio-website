"""Chats between a patient and the pharmacy or lab they chose.

This file: HTTP endpoints and request models.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from shared import auth, validators as v
from shared.auth import chat_user
from shared.common import stamp
from shared.db import get_db, write_txn

router = APIRouter()


class MessageIn(BaseModel):
    body: str = Field(max_length=2000)

    _body = field_validator("body")(v.required_text("Message", 2000))


@router.get("/api/chats")
def list_chats(user: dict = Depends(chat_user)):
    cond, _, _ = mine(user)
    with get_db() as conn:
        rows = conn.execute(SELECT + f" WHERE {cond} ORDER BY c.status = 'closed', c.last_message_at DESC LIMIT 100",
                            (user["id"],)).fetchall()
        chats = [view(conn, c, user) for c in rows]
    return {"chats": chats, "unread": sum(c["unread"] for c in chats)}


@router.get("/api/chats/{conversation_id}")
def get_chat(conversation_id: int, user: dict = Depends(chat_user)):
    with get_db() as conn:
        c = conversation_for(conn, conversation_id, user)
        _, my_col, their_col = mine(user)
        messages = [dict(m) for m in conn.execute(
            "SELECT id, author_role, body, created_at FROM conversation_messages WHERE conversation_id = ? ORDER BY id", (c["id"],))]
        out = {**view(conn, c, user), "messages": messages, "seen_by_other_up_to": int(c[their_col] or 0)}
        conn.execute(f"UPDATE conversations SET {my_col} = ? WHERE id = ?", (last_id(conn, c["id"]), c["id"]))
    out["unread"] = 0
    return out


@router.post("/api/chats/{conversation_id}/messages")
def send(conversation_id: int, data: MessageIn, user: dict = Depends(chat_user)):
    with get_db() as conn:
        if auth.rate_limited(conn, f"chat:{user['id']}", 60, 10):
            raise HTTPException(status_code=429, detail="You're sending messages very quickly. Please wait a moment.")
        with write_txn(conn):
            c = conversation_for(conn, conversation_id, user)
            if c["status"] != "open":
                raise HTTPException(status_code=400, detail="This chat is closed. Reopen it to send a message.")
            conn.execute("INSERT INTO conversation_messages (conversation_id, author_role, author_id, body, created_at) "
                         "VALUES (?, ?, ?, ?, ?)", (c["id"], user["role"], user["id"], data.body, stamp()))
            _, my_col, _ = mine(user)
            conn.execute(f"UPDATE conversations SET last_message_at = ?, {my_col} = ? WHERE id = ?", (stamp(), last_id(conn, c["id"]), c["id"]))
    return get_chat(conversation_id, user)


@router.post("/api/chats/{conversation_id}/close")
def close(conversation_id: int, user: dict = Depends(chat_user)):
    with get_db() as conn:
        with write_txn(conn):
            c = conversation_for(conn, conversation_id, user)
            if c["status"] == "closed":
                raise HTTPException(status_code=400, detail="This chat is already closed")
            conn.execute("UPDATE conversations SET status = 'closed', closed_by_role = ?, closed_at = ? WHERE id = ?",
                         (user["role"], stamp(), c["id"]))
            who = "The patient" if user["role"] == "patient" else ("The pharmacy" if user["role"] == "pharmacy" else "The lab")
            system_message(conn, c["id"], f"{who} closed this chat.")
    return get_chat(conversation_id, user)


@router.post("/api/chats/{conversation_id}/reopen")
def reopen(conversation_id: int, user: dict = Depends(chat_user)):
    with get_db() as conn:
        with write_txn(conn):
            c = conversation_for(conn, conversation_id, user)
            if c["status"] == "open":
                raise HTTPException(status_code=400, detail="This chat is already open")
            conn.execute("UPDATE conversations SET status = 'open', closed_by_role = NULL, closed_at = NULL WHERE id = ?", (c["id"],))
            who = "The patient" if user["role"] == "patient" else ("The pharmacy" if user["role"] == "pharmacy" else "The lab")
            system_message(conn, c["id"], f"{who} reopened this chat.")
    return get_chat(conversation_id, user)


# Imported last, so slices can depend on each other in any order
from features.chats.logic import conversation_for, last_id, mine, SELECT, system_message, view  # noqa: E402

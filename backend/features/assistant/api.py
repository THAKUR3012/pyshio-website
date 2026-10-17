"""Assistant: the per-role chatbot (built-in answers, optional Claude).

This file: HTTP endpoints and request models.
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Literal

from shared import ai, auth
from shared.db import get_db

router = APIRouter()
log = logging.getLogger("pyshio.assistant")


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=1000)


class AskIn(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    history: List[HistoryItem] = Field(default=[], max_length=8)


# ====================================================================== API
@router.get("/api/assistant")
def assistant_intro(user: dict = Depends(auth.require())):
    out, chips = greeting(user)
    return {**out, "suggestions": chips, "mode": "ai" if ai_enabled() else "built-in"}


@router.post("/api/assistant")
def ask(data: AskIn, user: dict = Depends(auth.require())):
    with get_db() as conn:
        if auth.rate_limited(conn, f"assistant:{user['id']}", 30, 10):
            raise HTTPException(status_code=429, detail="You're asking very quickly. Please wait a few minutes.")
    if ai_enabled():
        try:
            return {**ai_answer(user, data.message, data.history), "mode": "ai"}
        except (ai.AIUnavailable, KeyError, ValueError, TypeError) as e:
            log.warning("AI assistant unavailable, using built-in answers: %s", e)
    return {**builtin_answer(user, data.message), "mode": "built-in"}


# Imported last, so slices can depend on each other in any order
from features.assistant.logic import ai_answer, ai_enabled, builtin_answer, greeting  # noqa: E402

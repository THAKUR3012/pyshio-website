"""The one Claude client every slice uses (optional: everything works without ANTHROPIC_API_KEY).

- `enabled(cap)` says whether a capability may call Claude (key set, not switched off).
- `call()` sends one Messages request with prompt caching and logs usage (never content) to `ai_calls`.
- `extract()` forces a single tool call and returns its validated input: structured output.
- `redact()` strips contact details and given names before free text leaves the server (DPDP: send only what's needed).
Clinical decisions never depend on this module: callers fall back to their built-in rules on `AIUnavailable`.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

log = logging.getLogger("pyshio.ai")

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"
CAPABILITIES = ("assistant", "rx_read", "scribe", "lab_read", "lab_explain", "triage", "mr_brief")
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_INLINE_BYTES = 3_700_000  # base64 grows ~4/3; the API takes ~5 MB per image


class AIUnavailable(Exception):
    """Claude is switched off, unreachable, or answered in a shape we can't use."""


def enabled(capability: str) -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("PYSHIO_AI_DISABLED"):
        return False
    return os.environ.get(f"PYSHIO_AI_{capability.upper()}", "1") != "0"


def model_for(capability: str) -> str:
    if capability == "assistant" and os.environ.get("PYSHIO_ASSISTANT_MODEL"):
        return os.environ["PYSHIO_ASSISTANT_MODEL"]
    return os.environ.get(f"PYSHIO_AI_MODEL_{capability.upper()}") or os.environ.get("PYSHIO_AI_MODEL") or DEFAULT_MODEL


def _post(payload: dict, timeout: float = 30) -> dict:
    """The only network call (tests replace it)."""
    req = urllib.request.Request(API_URL, data=json.dumps(payload).encode(), method="POST",
                                 headers={"content-type": "application/json", "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                                          "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read())


def _cached_system(system) -> list:
    blocks = [{"type": "text", "text": system}] if isinstance(system, str) else [dict(b) for b in system]
    if blocks:
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks


def _cached_tools(tools):
    if not tools:
        return tools
    tools = [dict(t) for t in tools]
    tools[-1]["cache_control"] = {"type": "ephemeral"}
    return tools


def _log_call(capability, user_id, model, ok, ms, usage):
    try:
        from shared.common import stamp
        from shared.db import get_db
        with get_db() as conn:
            conn.execute("INSERT INTO ai_calls (capability, user_id, model, ok, ms, input_tokens, output_tokens, "
                         "cache_read_tokens, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (capability, user_id, model, int(ok), ms, usage.get("input_tokens"), usage.get("output_tokens"),
                          usage.get("cache_read_input_tokens"), stamp()))
    except Exception as e:  # logging must never break a feature
        log.debug("ai_calls log skipped: %s", e)


def call(capability: str, *, system, messages: list, tools=None, tool_choice=None, max_tokens: int = 600,
         timeout: float = 30, user_id=None) -> dict:
    if not enabled(capability):
        raise AIUnavailable(f"{capability} is switched off")
    model = model_for(capability)
    payload = {"model": model, "max_tokens": max_tokens, "system": _cached_system(system), "messages": messages}
    if tools:
        payload["tools"] = _cached_tools(tools)
    if tool_choice:
        payload["tool_choice"] = tool_choice
    started = time.monotonic()
    try:
        res = _post(payload, timeout)
        if not isinstance(res, dict) or not isinstance(res.get("content"), list):
            raise ValueError("Unexpected response shape")
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError, TypeError) as e:
        _log_call(capability, user_id, model, False, int((time.monotonic() - started) * 1000), {})
        log.warning("Claude %s unavailable: %s", capability, e)
        raise AIUnavailable(str(e)) from e
    usage = res.get("usage") or {}
    _log_call(capability, user_id, model, True, int((time.monotonic() - started) * 1000), usage)
    log.info("Claude %s ok model=%s in=%s out=%s cache_read=%s", capability, model, usage.get("input_tokens"),
             usage.get("output_tokens"), usage.get("cache_read_input_tokens"))
    return res


def text_of(res: dict) -> str:
    return " ".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text").strip()


def extract(capability: str, *, system: str, content: list, schema: dict, max_tokens: int = 1500,
            timeout: float = 45, user_id=None) -> dict:
    """Ask for exactly one `emit` tool call shaped by `schema`; return its validated input."""
    tool = {"name": "emit", "description": "Return the result in this exact structure.", "input_schema": schema}
    res = call(capability, system=system, messages=[{"role": "user", "content": content}], tools=[tool],
               tool_choice={"type": "tool", "name": "emit"}, max_tokens=max_tokens, timeout=timeout, user_id=user_id)
    for block in res["content"]:
        if block.get("type") == "tool_use" and block.get("name") == "emit":
            try:
                return validate(block.get("input"), schema)
            except ValueError as e:
                raise AIUnavailable(f"Invalid structured output: {e}") from e
    raise AIUnavailable("No structured output returned")


# ---------- A small JSON-schema subset validator (type, properties, required, items, enum, maxLength, maxItems) ----------
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float)}


def validate(value, schema: dict, path: str = "$"):
    """Check `value` against `schema`; drops unknown object keys; raises ValueError on a mismatch."""
    types = schema.get("type")
    if isinstance(types, list):
        if value is None and "null" in types:
            return None
        types = next(t for t in types if t != "null")
    if value is None:
        if types == "object":
            raise ValueError(f"{path} is missing")
        return None
    if types:
        expected = _TYPES[types]
        if types in ("integer", "number") and isinstance(value, bool):
            raise ValueError(f"{path} should be a {types}")
        if not isinstance(value, expected):
            if types == "number" and isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?", value.strip()):
                value = float(value)
            elif types == "integer" and isinstance(value, (float, str)) and re.fullmatch(r"-?\d+(\.0+)?", str(value).strip()):
                value = int(float(value))
            else:
                raise ValueError(f"{path} should be a {types}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']}")
    if isinstance(value, str) and "maxLength" in schema:
        value = value[:schema["maxLength"]]
    if isinstance(value, list):
        value = value[:schema.get("maxItems", 200)]
        item_schema = schema.get("items")
        if item_schema:
            value = [validate(v, item_schema, f"{path}[{i}]") for i, v in enumerate(value)]
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"{path}.{key} is required")
        value = {k: validate(v, props[k], f"{path}.{k}") for k, v in value.items() if k in props}
    return value


# ---------- Content blocks ----------
def text(t: str) -> dict:
    return {"type": "text", "text": t}


def sniff_media_type(data: bytes):
    if data[:4] == b"%PDF":
        return "application/pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def file_block(data: bytes) -> dict:
    """An image or PDF block for Claude; AIUnavailable when the file can't be sent (type or size)."""
    media = sniff_media_type(data)
    if not media:
        raise AIUnavailable("This file type can't be read automatically")
    if len(data) > MAX_INLINE_BYTES:
        raise AIUnavailable("The file is too large to read automatically")
    encoded = base64.standard_b64encode(data).decode()
    if media == "application/pdf":
        return {"type": "document", "source": {"type": "base64", "media_type": media, "data": encoded}}
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": encoded}}


# ---------- Privacy ----------
_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"(?<!\d)(?:\d[ -]?){12}(?!\d)"), "[id number]"),          # Aadhaar-like
    (re.compile(r"(?:\+?91[ -]?)?(?<!\d)[6-9]\d{4}[ -]?\d{5}(?!\d)"), "[phone]"),
    (re.compile(r"(?<!\d)\d{6}(?!\d)"), "[pincode]"),
]


def redact(value: str, names=()) -> str:
    out = value or ""
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    for name in names:
        for part in {name, *[p for p in (name or "").split() if len(p) >= 3 and not p.lower().startswith("dr")]}:
            if part:
                out = re.sub(re.escape(part), "[name]", out, flags=re.I)
    return out


def age_band(age) -> str:
    if age is None:
        return "unknown"
    for upper, label in ((1, "under 1"), (5, "1-4"), (12, "5-11"), (18, "12-17"), (30, "18-29"), (45, "30-44"),
                         (60, "45-59"), (75, "60-74")):
        if age < upper:
            return label
    return "75+"

"""Rewrite Huawei/DeepSeek SSE so Cursor sees delta.content (not only reasoning)."""
from __future__ import annotations

import json
from typing import Any


def rewrite_openai_message(payload: dict[str, Any]) -> dict[str, Any]:
    """Non-stream: if content empty, lift reasoning_content into content."""
    try:
        choices = payload.get("choices") or []
        if not choices:
            return payload
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        reasoning = msg.get("reasoning_content") or msg.get("reasoning")
        if (not content) and isinstance(reasoning, str) and reasoning.strip():
            msg = dict(msg)
            msg["content"] = reasoning
            msg.pop("reasoning_content", None)
            # keep a short flag for debugging
            choices0 = dict(choices[0])
            choices0["message"] = msg
            out = dict(payload)
            out["choices"] = [choices0] + list(choices[1:])
            return out
    except Exception:
        return payload
    return payload


def rewrite_sse_line(line: str) -> str:
    """
    Stream chunk: Cursor ignores reasoning_content and shows blank.
    Move reasoning → content when content is empty/missing.
    """
    raw = line
    if not line.startswith("data:"):
        return raw
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return raw
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return raw
    try:
        choices = data.get("choices") or []
        if not choices:
            return raw
        choice0 = dict(choices[0])
        delta = dict(choice0.get("delta") or {})
        content = delta.get("content")
        reasoning = delta.get("reasoning_content")
        if reasoning is None and isinstance(delta.get("reasoning"), str):
            reasoning = delta.get("reasoning")

        empty_content = content is None or content == ""
        if empty_content and isinstance(reasoning, str) and reasoning:
            delta["content"] = reasoning
            delta.pop("reasoning_content", None)
            delta.pop("reasoning", None)
            choice0["delta"] = delta
            data = dict(data)
            data["choices"] = [choice0] + list(choices[1:])
            return "data: " + json.dumps(data, ensure_ascii=False)

        # Drop reasoning noise when real content is present
        if (not empty_content) and ("reasoning_content" in delta or "reasoning" in delta):
            delta.pop("reasoning_content", None)
            delta.pop("reasoning", None)
            choice0["delta"] = delta
            data = dict(data)
            data["choices"] = [choice0] + list(choices[1:])
            return "data: " + json.dumps(data, ensure_ascii=False)
    except Exception:
        return raw
    return raw


def rewrite_sse_bytes_chunk(chunk: bytes, carry: bytes) -> tuple[bytes, bytes]:
    """Process a bytes chunk; return (emit_bytes, new_carry)."""
    buf = carry + chunk
    out = bytearray()
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        text = line.decode("utf-8", errors="ignore")
        # preserve empty lines
        if text.strip() == "" and not text.startswith("data:"):
            out.extend(line + b"\n")
            continue
        rewritten = rewrite_sse_line(text.rstrip("\r"))
        out.extend(rewritten.encode("utf-8") + b"\n")
    return bytes(out), buf

"""
OpenAI-compatible proxy: Cursor -> OpenRouter, with usage/balance monitoring API.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from usage_tracker import (
    RequestUsage,
    UsageTracker,
    build_optimization_hints,
    fetch_openrouter_balance,
    parse_usage_from_openai_payload,
    parse_usage_from_sse_chunk,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("or-proxy")

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / "config.env"

if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
PROXY_MASTER_KEY = os.environ.get("PROXY_MASTER_KEY", "sk-local-cursor-proxy").strip()
METRICS_KEY = os.environ.get("METRICS_API_KEY", PROXY_MASTER_KEY).strip()
OPENROUTER_BASE = os.environ.get(
    "OPENROUTER_BASE", "https://openrouter.ai/api/v1"
).rstrip("/")
DB_PATH = Path(os.environ.get("USAGE_DB_PATH", str(ROOT / "data" / "usage.db")))

MODEL_ALIASES = {
    "deepseek-v4-flash": "deepseek/deepseek-v4-flash",
    "deepseek-v4-pro": "deepseek/deepseek-v4-pro",
    "chat": "deepseek/deepseek-v4-flash",
    "coder": "deepseek/deepseek-v4-pro",
}

VERSION = "1.3.0"
tracker = UsageTracker(DB_PATH)
_balance_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_BALANCE_TTL = 30.0

app = FastAPI(
    title="OpenRouter Cursor Proxy",
    version=VERSION,
    description="BYOK bridge + realtime usage/balance monitoring API",
)


def _auth_proxy(authorization: str | None) -> None:
    if not OPENROUTER_API_KEY:
        raise HTTPException(500, "OPENROUTER_API_KEY not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token not in {PROXY_MASTER_KEY, OPENROUTER_API_KEY}:
        raise HTTPException(401, "Invalid proxy API key")


def _auth_metrics(authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token not in {METRICS_KEY, PROXY_MASTER_KEY, OPENROUTER_API_KEY}:
        raise HTTPException(401, "Invalid metrics API key")


def _rewrite_body(body: dict[str, Any]) -> dict[str, Any]:
    out = dict(body)
    model = out.get("model")
    if isinstance(model, str) and model in MODEL_ALIASES:
        out["model"] = MODEL_ALIASES[model]
    if "reasoning" not in out:
        out["reasoning"] = {"effort": "none"}
    if "include_reasoning" not in out:
        out["include_reasoning"] = False
    # Ask OpenRouter to include usage on stream final chunk
    if out.get("stream") and "stream_options" not in out:
        out["stream_options"] = {"include_usage": True}
    for junk in ("prompt_cache_key", "safety_identifier"):
        out.pop(junk, None)
    return out


async def _get_balance(force: bool = False) -> dict[str, Any]:
    now = time.time()
    if (
        not force
        and _balance_cache["data"] is not None
        and now - float(_balance_cache["ts"]) < _BALANCE_TTL
    ):
        return dict(_balance_cache["data"])
    data = await fetch_openrouter_balance(OPENROUTER_API_KEY, OPENROUTER_BASE)
    _balance_cache["ts"] = now
    _balance_cache["data"] = data
    return dict(data)


def _record(
    *,
    model: str,
    stream: bool,
    latency_ms: int,
    status: int,
    usage: dict[str, Any] | None,
) -> None:
    u = usage or {}
    tracker.record(
        RequestUsage(
            ts=time.time(),
            model=model or "unknown",
            prompt_tokens=int(u.get("prompt_tokens") or 0),
            completion_tokens=int(u.get("completion_tokens") or 0),
            reasoning_tokens=int(u.get("reasoning_tokens") or 0),
            total_tokens=int(u.get("total_tokens") or 0),
            cost_usd=float(u.get("cost_usd") or 0.0),
            stream=stream,
            latency_ms=latency_ms,
            status=status,
        )
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "upstream": OPENROUTER_BASE,
        "version": VERSION,
        "usage_db": str(DB_PATH),
    }


# ---------- Monitoring API ----------


@app.get("/api/v1/balance")
async def api_balance(
    authorization: str | None = Header(default=None),
    refresh: bool = Query(False),
) -> dict[str, Any]:
    _auth_metrics(authorization)
    return await _get_balance(force=refresh)


@app.get("/api/v1/usage")
@app.get("/api/v1/usage/summary")
async def api_usage_summary(
    authorization: str | None = Header(default=None),
    window_seconds: int = Query(300, ge=30, le=86400),
) -> dict[str, Any]:
    _auth_metrics(authorization)
    snap = tracker.snapshot(window_seconds=window_seconds)
    balance = await _get_balance()
    return {
        "proxy_version": VERSION,
        "balance": balance,
        "usage": snap,
        "optimization": [
            h.__dict__ for h in build_optimization_hints(snap, balance)
        ],
    }


@app.get("/api/v1/usage/realtime")
async def api_usage_realtime(
    authorization: str | None = Header(default=None),
    window_seconds: int = Query(60, ge=10, le=3600),
) -> dict[str, Any]:
    _auth_metrics(authorization)
    snap = tracker.snapshot(window_seconds=window_seconds)
    return {
        "ts": time.time(),
        "window_seconds": window_seconds,
        "window": snap["window"],
        "recent": snap["recent"],
        "lifetime": snap["lifetime"],
    }


@app.get("/api/v1/usage/series")
async def api_usage_series(
    authorization: str | None = Header(default=None),
    period: str = Query("day", pattern="^(day|month|year)$"),
    limit: int | None = Query(None, ge=1, le=500),
    model: str | None = Query(None, description="Filter by exact model id"),
) -> dict[str, Any]:
    """Historical series from SQLite: period=day|month|year (UTC buckets)."""
    _auth_metrics(authorization)
    try:
        series = tracker.series(period=period, limit=limit, model=model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "ts": time.time(),
        "proxy_version": VERSION,
        **series,
    }


@app.get("/api/v1/optimization")
async def api_optimization(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth_metrics(authorization)
    snap = tracker.snapshot()
    balance = await _get_balance()
    hints = build_optimization_hints(snap, balance)
    return {
        "generated_at": time.time(),
        "hints": [h.__dict__ for h in hints],
        "balance_remaining_credits": balance.get("remaining_credits"),
        "proxy_lifetime_cost_usd": snap["lifetime"]["cost_usd"],
    }


@app.get("/api/v1/monitor")
async def api_monitor(
    authorization: str | None = Header(default=None),
    window_seconds: int = Query(300, ge=30, le=86400),
) -> dict[str, Any]:
    """Single envelope for Grafana/Homarr/n8n/etc."""
    _auth_metrics(authorization)
    snap = tracker.snapshot(window_seconds=window_seconds)
    balance = await _get_balance()
    hints = build_optimization_hints(snap, balance)
    remaining = balance.get("remaining_credits")
    status = "ok"
    if remaining is not None and remaining < 2:
        status = "critical"
    elif any(h.severity == "warn" for h in hints):
        status = "warn"
    return {
        "status": status,
        "version": VERSION,
        "hostname": os.environ.get("PUBLIC_HOSTNAME", "localhost"),
        "balance": balance,
        "usage": {
            "window": snap["window"],
            "lifetime": snap["lifetime"],
            "by_model": snap["by_model"],
        },
        "optimization": [h.__dict__ for h in hints],
        "links": {
            "prometheus": "/metrics",
            "realtime": "/api/v1/usage/realtime",
            "series": "/api/v1/usage/series?period=day",
            "balance": "/api/v1/balance",
            "health": "/health",
        },
    }


@app.get("/metrics")
async def prometheus_metrics(
    authorization: str | None = Header(default=None),
) -> Response:
    _auth_metrics(authorization)
    balance = await _get_balance()
    body = tracker.prometheus(balance)
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")


# ---------- OpenAI-compatible chat ----------


@app.get("/v1/models")
@app.get("/models")
@app.get("/cursor/models")
async def list_models(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth_proxy(authorization)
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.get(
            f"{OPENROUTER_BASE}/models",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        )
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text)
    return r.json()


async def _proxy_chat(request: Request, authorization: str | None) -> Response:
    _auth_proxy(authorization)
    raw = await request.json()
    body = _rewrite_body(raw)
    stream = bool(body.get("stream"))
    model = str(body.get("model") or "unknown")
    started = time.time()
    log.info(
        "chat model=%s stream=%s messages=%s",
        model,
        stream,
        len(body.get("messages") or []),
    )

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://cursor.com",
        "X-Title": "Cursor OpenRouter Proxy",
    }
    url = f"{OPENROUTER_BASE}/chat/completions"

    if stream:
        client = httpx.AsyncClient(timeout=None)

        async def gen():
            usage_acc: dict[str, Any] | None = None
            status = 200
            try:
                async with client.stream("POST", url, headers=headers, json=body) as resp:
                    status = resp.status_code
                    if resp.status_code >= 400:
                        err = await resp.aread()
                        log.error("upstream_stream_error %s", err[:500])
                        yield err
                        return
                    buffer = b""
                    async for chunk in resp.aiter_bytes():
                        yield chunk
                        buffer += chunk
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            try:
                                text = line.decode("utf-8", errors="ignore")
                            except Exception:
                                continue
                            parsed = parse_usage_from_sse_chunk(text)
                            if parsed:
                                usage_acc = parsed
            finally:
                await client.aclose()
                _record(
                    model=model,
                    stream=True,
                    latency_ms=int((time.time() - started) * 1000),
                    status=status,
                    usage=usage_acc,
                )

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(url, headers=headers, json=body)
    usage = None
    if r.status_code < 400:
        try:
            usage = parse_usage_from_openai_payload(r.json())
        except Exception:
            usage = None
    else:
        log.error("upstream_error status=%s body=%s", r.status_code, r.text[:500])
    _record(
        model=model,
        stream=False,
        latency_ms=int((time.time() - started) * 1000),
        status=r.status_code,
        usage=usage,
    )
    return Response(content=r.content, status_code=r.status_code, media_type="application/json")


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
@app.post("/cursor/chat/completions")
async def chat_completions(
    request: Request, authorization: str | None = Header(default=None)
) -> Response:
    return await _proxy_chat(request, authorization)


@app.api_route("/cursor/{path:path}", methods=["GET", "POST", "OPTIONS"])
@app.api_route("/v1/{path:path}", methods=["GET", "POST", "OPTIONS"])
async def catch_all(
    path: str, request: Request, authorization: str | None = Header(default=None)
) -> Response:
    if path.endswith("chat/completions") or path == "chat/completions":
        return await _proxy_chat(request, authorization)
    if path.endswith("models") or path == "models":
        return await list_models(authorization)
    log.warning("unsupported path=%s method=%s", path, request.method)
    raise HTTPException(404, f"Unsupported path: {path}")


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "4010"))
    uvicorn.run("proxy:app", host=host, port=port, reload=False)

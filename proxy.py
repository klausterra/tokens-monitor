"""
OpenAI-compatible multi-provider proxy (OpenRouter + Xiaomi + Huawei MaaS)
with usage monitoring and configurable guardrails.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from guardrails import GuardrailEnforcer, estimate_prompt_tokens
from providers import prepare_route, providers_status, resolve_model
from usage_tracker import (
    RequestUsage,
    UsageTracker,
    build_optimization_hints,
    fetch_openrouter_balance,
    parse_usage_from_openai_payload,
    parse_usage_from_sse_chunk,
)
from stream_rewrite import rewrite_openai_message, rewrite_sse_bytes_chunk

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tokens-monitor")

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
GUARDRAILS_PATH = Path(
    os.environ.get("GUARDRAILS_PATH", str(ROOT / "guardrails.json"))
)

VERSION = "1.6.5"
tracker = UsageTracker(DB_PATH)
guardrails = GuardrailEnforcer(GUARDRAILS_PATH)
_balance_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_BALANCE_TTL = 30.0

app = FastAPI(
    title="tokens-monitor",
    version=VERSION,
    description="Multi-provider BYOK bridge + guardrails + monitoring API",
)


def _label_from_body(body: dict[str, Any]) -> str:
    msgs = body.get("messages") or []
    for m in msgs:
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str) and c.strip():
                return " ".join(c.strip().split())[:120]
            if isinstance(c, list):
                for part in c:
                    if isinstance(part, dict) and part.get("type") == "text":
                        t = str(part.get("text") or "").strip()
                        if t:
                            return " ".join(t.split())[:120]
    return ""


def _auth_proxy(authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token not in {PROXY_MASTER_KEY, OPENROUTER_API_KEY}:
        # also accept if only xiaomi configured and master key matches
        if token != PROXY_MASTER_KEY:
            raise HTTPException(401, "Invalid proxy API key")


def _auth_metrics(authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    allowed = {METRICS_KEY, PROXY_MASTER_KEY}
    if OPENROUTER_API_KEY:
        allowed.add(OPENROUTER_API_KEY)
    if token not in allowed:
        raise HTTPException(401, "Invalid metrics API key")


async def _get_balance(force: bool = False) -> dict[str, Any]:
    if not OPENROUTER_API_KEY:
        return {"configured": False}
    now = time.time()
    if (
        not force
        and _balance_cache["data"] is not None
        and now - float(_balance_cache["ts"]) < _BALANCE_TTL
    ):
        return dict(_balance_cache["data"])
    data = await fetch_openrouter_balance(OPENROUTER_API_KEY, OPENROUTER_BASE)
    data["configured"] = True
    _balance_cache["ts"] = now
    _balance_cache["data"] = data
    return dict(data)


def _period_cost(period: str) -> float:
    try:
        series = tracker.series(period=period, limit=1)
        points = series.get("points") or []
        if not points:
            return 0.0
        return float(points[-1].get("cost_usd") or 0.0)
    except Exception:
        return 0.0


def _record(
    *,
    model: str,
    stream: bool,
    latency_ms: int,
    status: int,
    usage: dict[str, Any] | None,
    provider: str = "",
    error: str = "",
    label: str = "",
    client: str = "",
) -> None:
    u = usage or {}
    tokens = int(u.get("total_tokens") or 0)
    tracker.record(
        RequestUsage(
            ts=time.time(),
            model=model or "unknown",
            prompt_tokens=int(u.get("prompt_tokens") or 0),
            completion_tokens=int(u.get("completion_tokens") or 0),
            reasoning_tokens=int(u.get("reasoning_tokens") or 0),
            total_tokens=tokens,
            cost_usd=float(u.get("cost_usd") or 0.0),
            stream=stream,
            latency_ms=latency_ms,
            status=status,
            provider=provider,
            error=error,
            label=label,
            client=client,
        )
    )
    guardrails.note_request(tokens or estimate_prompt_tokens([]))


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": VERSION,
        "usage_db": str(DB_PATH),
        "providers": providers_status(),
        "guardrails_enabled": guardrails.cfg.enabled,
    }


# ---------- Config / Guardrails ----------


@app.get("/api/v1/config")
async def api_config(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth_metrics(authorization)
    return {
        "version": VERSION,
        "public_hostname": os.environ.get("PUBLIC_HOSTNAME", "localhost"),
        "default_provider": os.environ.get("DEFAULT_PROVIDER", "openrouter"),
        "providers": providers_status(),
        "guardrails": guardrails.cfg.to_public_dict(),
        "guardrails_path": str(GUARDRAILS_PATH),
    }


@app.get("/api/v1/guardrails")
async def api_guardrails_get(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth_metrics(authorization)
    return {"guardrails": guardrails.cfg.to_public_dict()}


@app.put("/api/v1/guardrails")
async def api_guardrails_put(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth_metrics(authorization)
    patch = await request.json()
    if not isinstance(patch, dict):
        raise HTTPException(400, "JSON object required")
    cfg = guardrails.update(patch)
    return {"ok": True, "guardrails": cfg.to_public_dict()}


@app.post("/api/v1/guardrails/reload")
async def api_guardrails_reload(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth_metrics(authorization)
    cfg = guardrails.reload()
    return {"ok": True, "guardrails": cfg.to_public_dict()}


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
        "optimization": [h.__dict__ for h in build_optimization_hints(snap, balance)],
        "guardrails": guardrails.cfg.to_public_dict(),
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
    model: str | None = Query(None),
) -> dict[str, Any]:
    _auth_metrics(authorization)
    try:
        series = tracker.series(period=period, limit=limit, model=model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ts": time.time(), "proxy_version": VERSION, **series}


@app.get("/api/v1/usage/tasks")
async def api_usage_tasks(
    authorization: str | None = Header(default=None),
    gap_seconds: int = Query(180, ge=30, le=3600),
    limit: int = Query(50, ge=1, le=200),
    since_hours: float = Query(48, ge=0.1, le=720),
) -> dict[str, Any]:
    _auth_metrics(authorization)
    return {
        "ts": time.time(),
        "proxy_version": VERSION,
        **tracker.tasks(
            gap_seconds=gap_seconds, limit=limit, since_hours=since_hours
        ),
    }


@app.get("/api/v1/usage/events")
async def api_usage_events(
    authorization: str | None = Header(default=None),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    _auth_metrics(authorization)
    return {
        "ts": time.time(),
        "proxy_version": VERSION,
        "events": tracker.recent_events(limit=limit),
    }


@app.get("/dashboard")
async def dashboard_page() -> FileResponse:
    path = ROOT / "static" / "dashboard.html"
    if not path.exists():
        raise HTTPException(404, "dashboard.html missing")
    return FileResponse(path, media_type="text/html; charset=utf-8")


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
        "providers": providers_status(),
        "guardrails": guardrails.cfg.to_public_dict(),
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
            "tasks": "/api/v1/usage/tasks",
            "events": "/api/v1/usage/events",
            "dashboard": "/dashboard",
            "guardrails": "/api/v1/guardrails",
            "config": "/api/v1/config",
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
    return PlainTextResponse(
        tracker.prometheus(balance), media_type="text/plain; version=0.0.4"
    )


# ---------- OpenAI-compatible chat ----------


@app.get("/v1/models")
@app.get("/models")
@app.get("/cursor/models")
async def list_models(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _auth_proxy(authorization)
    data: list[dict[str, Any]] = []
    if OPENROUTER_API_KEY:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.get(
                f"{OPENROUTER_BASE}/models",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            )
        if r.status_code < 400:
            data.extend((r.json().get("data") or []))
    # Always advertise Xiaomi / Huawei shortcuts when keys present
    from providers import _huawei_key, _xiaomi_key

    if _xiaomi_key():
        for mid in ("mimo-v2.5", "mimo-v2.5-pro", "xiaomi/mimo-v2.5", "xiaomi/mimo-v2.5-pro"):
            data.append({"id": mid, "object": "model", "owned_by": "xiaomi"})
    if _huawei_key():
        from providers import HUAWEI_LITELLM_MODELS

        for mid in sorted(HUAWEI_LITELLM_MODELS):
            data.append({"id": mid, "object": "model", "owned_by": "huawei"})
        for mid in (
            "huawei/glm-5",
            "huawei/glm-5.1",
            "huawei/glm-5.2",
            "hw/glm-5",
            "hw/glm-5.1",
            "hw/glm-5.2",
            "huawei/DeepSeek-V4-Flash",
            "hw/flash",
            "huawei/deepseek-v4-pro",
            "hw/pro",
            "DeepSeek-R1-250528",
            "hw/r1",
            "huawei/r1",
        ):
            data.append({"id": mid, "object": "model", "owned_by": "huawei"})
    return {"object": "list", "data": data}


async def _proxy_chat(request: Request, authorization: str | None) -> Response:
    _auth_proxy(authorization)
    raw = await request.json()
    force_none = guardrails.cfg.force_reasoning_none
    try:
        route = prepare_route(
            raw,
            openrouter_key=OPENROUTER_API_KEY,
            openrouter_base=OPENROUTER_BASE,
            force_reasoning_none=force_none,
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc

    model = resolve_model(str(raw.get("model") or route.model))
    label = _label_from_body(raw if isinstance(raw, dict) else {})
    client_ua = (request.headers.get("user-agent") or "")[:120]
    balance = await _get_balance()
    # OpenRouter credit guard only when routing to OpenRouter
    or_remaining = (
        balance.get("remaining_credits") if route.name == "openrouter" else None
    )
    ok, code, detail = guardrails.check(
        model=route.model if not model.startswith("xiaomi/") else model,
        provider=route.name,
        body=route.body,
        usage_day_cost=_period_cost("day"),
        usage_month_cost=_period_cost("month"),
        openrouter_remaining=or_remaining,
    )
    if not ok:
        log.warning("guardrail_block code=%s detail=%s", code, detail)
        if guardrails.cfg.block_on_guardrail:
            raise HTTPException(
                429 if code in {"rpm_exceeded", "tpm_exceeded"} else 403,
                {"error": {"message": detail, "type": "guardrail", "code": code}},
            )

    stream = bool(route.body.get("stream"))
    started = time.time()
    url = f"{route.base_url}/chat/completions"
    log.info(
        "chat provider=%s model=%s stream=%s label=%s",
        route.name,
        route.model,
        stream,
        (label or "")[:40],
    )

    if stream:
        client = httpx.AsyncClient(timeout=None)
        rewrite = route.name == "huawei"
        max_attempts = 4 if route.name == "huawei" else 1

        async def gen():
            usage_acc: dict[str, Any] | None = None
            status = 200
            err_txt = ""
            carry = b""
            try:
                for attempt in range(1, max_attempts + 1):
                    carry = b""
                    async with client.stream(
                        "POST", url, headers=route.headers, json=route.body
                    ) as resp:
                        status = resp.status_code
                        if resp.status_code == 429 and attempt < max_attempts:
                            err = await resp.aread()
                            err_txt = err.decode("utf-8", errors="ignore")[:500]
                            wait = 22.0 * attempt
                            log.warning(
                                "huawei_429_retry attempt=%s/%s wait=%.0fs",
                                attempt,
                                max_attempts,
                                wait,
                            )
                            await asyncio.sleep(wait)
                            continue
                        if resp.status_code >= 400:
                            err = await resp.aread()
                            err_txt = err.decode("utf-8", errors="ignore")[:500]
                            log.error("upstream_stream_error %s", err_txt[:500])
                            payload = {
                                "error": {
                                    "message": err_txt
                                    or f"upstream {resp.status_code}",
                                    "type": "upstream_error",
                                    "code": str(resp.status_code),
                                    "provider": route.name,
                                }
                            }
                            yield (
                                "data: "
                                + json.dumps(payload, ensure_ascii=False)
                                + "\n\n"
                            ).encode()
                            yield b"data: [DONE]\n\n"
                            return
                        async for chunk in resp.aiter_bytes():
                            parse_buf = chunk
                            while b"\n" in parse_buf:
                                line, parse_buf = parse_buf.split(b"\n", 1)
                                text = line.decode("utf-8", errors="ignore")
                                parsed = parse_usage_from_sse_chunk(text)
                                if parsed:
                                    usage_acc = parsed
                            if rewrite:
                                emit, carry = rewrite_sse_bytes_chunk(chunk, carry)
                                if emit:
                                    yield emit
                            else:
                                yield chunk
                        if rewrite and carry:
                            emit, carry = rewrite_sse_bytes_chunk(b"\n", carry)
                            if emit:
                                yield emit
                        return
            finally:
                await client.aclose()
                _record(
                    model=f"{route.name}/{route.model}",
                    stream=True,
                    latency_ms=int((time.time() - started) * 1000),
                    status=status,
                    usage=usage_acc,
                    provider=route.name,
                    error=err_txt,
                    label=label,
                    client=client_ua,
                )

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Provider": route.name,
            },
        )

    max_attempts = 4 if route.name == "huawei" else 1
    r: httpx.Response | None = None
    async with httpx.AsyncClient(timeout=180.0) as client:
        for attempt in range(1, max_attempts + 1):
            r = await client.post(url, headers=route.headers, json=route.body)
            if r.status_code == 429 and attempt < max_attempts:
                wait = 22.0 * attempt
                log.warning(
                    "huawei_429_retry attempt=%s/%s wait=%.0fs",
                    attempt,
                    max_attempts,
                    wait,
                )
                await asyncio.sleep(wait)
                continue
            break
    assert r is not None
    usage = None
    err_txt = ""
    body_out = r.content
    if r.status_code < 400:
        try:
            data = r.json()
            if route.name == "huawei":
                data = rewrite_openai_message(data)
                body_out = json.dumps(data, ensure_ascii=False).encode("utf-8")
            usage = parse_usage_from_openai_payload(data)
        except Exception:
            usage = None
    else:
        err_txt = r.text[:500]
        log.error("upstream_error status=%s body=%s", r.status_code, err_txt)
    _record(
        model=f"{route.name}/{route.model}",
        stream=False,
        latency_ms=int((time.time() - started) * 1000),
        status=r.status_code,
        usage=usage,
        provider=route.name,
        error=err_txt,
        label=label,
        client=client_ua,
    )
    return Response(
        content=body_out,
        status_code=r.status_code,
        media_type="application/json",
        headers={"X-Provider": route.name},
    )


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
    raise HTTPException(404, f"Unsupported path: {path}")


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "4010"))
    uvicorn.run("proxy:app", host=host, port=port, reload=False)

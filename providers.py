"""Upstream providers: OpenRouter + Xiaomi MiMo (MaaS)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass
class ProviderRoute:
    name: str  # openrouter | xiaomi
    base_url: str
    api_key: str
    model: str
    headers: dict[str, str]
    body: dict[str, Any]


MODEL_ALIASES = {
    "deepseek-v4-flash": "deepseek/deepseek-v4-flash",
    "deepseek-v4-pro": "deepseek/deepseek-v4-pro",
    "chat": "deepseek/deepseek-v4-flash",
    "coder": "deepseek/deepseek-v4-pro",
    # Xiaomi shortcuts
    "mimo": "mimo-v2.5",
    "mimo-pro": "mimo-v2.5-pro",
    "xiaomi/mimo-v2.5": "mimo-v2.5",
    "xiaomi/mimo-v2.5-pro": "mimo-v2.5-pro",
}


def _xiaomi_key() -> str:
    for name in (
        "XIAOMI_MAAS_API_KEY",
        "XIAOMI_MIMO_API_KEY",
        "MIMO_API_KEY",
        "XIAOMI_API_KEY",
    ):
        v = os.environ.get(name, "").strip()
        if v:
            return v
    return ""


def _xiaomi_base() -> str:
    for name in ("XIAOMI_MAAS_BASE_URL", "XIAOMI_MIMO_BASE_URL", "MIMO_BASE_URL"):
        v = os.environ.get(name, "").strip()
        if v:
            return v.rstrip("/")
    key = _xiaomi_key()
    if key.startswith("tp-"):
        # Token Plan default (override via env for cn/sgp/eu)
        return os.environ.get(
            "XIAOMI_TOKEN_PLAN_BASE_URL", "https://token-plan-sgp.xiaomimimo.com/v1"
        ).rstrip("/")
    return "https://api.xiaomimimo.com/v1"


def resolve_model(model: str | None) -> str:
    m = (model or "unknown").strip()
    return MODEL_ALIASES.get(m, m)


def detect_provider(model: str) -> str:
    """Explicit prefix wins; mimo-* → xiaomi; else openrouter."""
    m = model.lower()
    if m.startswith("xiaomi/") or m.startswith("mimo/") or m.startswith("mimo-"):
        return "xiaomi"
    if m.startswith("openrouter/"):
        return "openrouter"
    # default
    default = os.environ.get("DEFAULT_PROVIDER", "openrouter").strip().lower()
    return default if default in {"openrouter", "xiaomi"} else "openrouter"


def prepare_route(
    body: dict[str, Any],
    *,
    openrouter_key: str,
    openrouter_base: str,
    force_reasoning_none: bool = True,
) -> ProviderRoute:
    out = dict(body)
    model = resolve_model(str(out.get("model") or "unknown"))
    # strip provider prefixes for upstream
    upstream_model = model
    for prefix in ("xiaomi/", "mimo/", "openrouter/"):
        if upstream_model.lower().startswith(prefix):
            upstream_model = upstream_model[len(prefix) :]
            break
    out["model"] = upstream_model
    provider = detect_provider(model)

    if provider == "xiaomi":
        key = _xiaomi_key()
        if not key:
            raise RuntimeError(
                "Xiaomi MaaS key not configured "
                "(XIAOMI_MAAS_API_KEY / XIAOMI_MIMO_API_KEY / MIMO_API_KEY)"
            )
        # Xiaomi prefers max_completion_tokens; map max_tokens if needed
        if "max_tokens" in out and "max_completion_tokens" not in out:
            out["max_completion_tokens"] = out.pop("max_tokens")
        # Drop OpenRouter-only fields
        out.pop("reasoning", None)
        out.pop("include_reasoning", None)
        out.pop("stream_options", None)
        headers = {
            "Authorization": f"Bearer {key}",
            "api-key": key,
            "Content-Type": "application/json",
        }
        return ProviderRoute(
            name="xiaomi",
            base_url=_xiaomi_base(),
            api_key=key,
            model=upstream_model,
            headers=headers,
            body=out,
        )

    # OpenRouter
    if not openrouter_key:
        raise RuntimeError("OPENROUTER_API_KEY not configured")
    if force_reasoning_none:
        if "reasoning" not in out:
            out["reasoning"] = {"effort": "none"}
        if "include_reasoning" not in out:
            out["include_reasoning"] = False
    if out.get("stream") and "stream_options" not in out:
        out["stream_options"] = {"include_usage": True}
    for junk in ("prompt_cache_key", "safety_identifier"):
        out.pop(junk, None)
    headers = {
        "Authorization": f"Bearer {openrouter_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://cursor.com",
        "X-Title": "tokens-monitor",
    }
    return ProviderRoute(
        name="openrouter",
        base_url=openrouter_base.rstrip("/"),
        api_key=openrouter_key,
        model=upstream_model,
        headers=headers,
        body=out,
    )


def providers_status() -> dict[str, Any]:
    xk = _xiaomi_key()
    ok = os.environ.get("OPENROUTER_API_KEY", "").strip()
    return {
        "openrouter": {
            "configured": bool(ok),
            "base_url": os.environ.get("OPENROUTER_BASE", "https://openrouter.ai/api/v1"),
            "key_hint": (ok[:8] + "…") if ok else None,
        },
        "xiaomi": {
            "configured": bool(xk),
            "base_url": _xiaomi_base() if xk else None,
            "key_hint": (xk[:6] + "…") if xk else None,
            "mode": "token-plan" if xk.startswith("tp-") else ("payg" if xk else None),
        },
        "default_provider": os.environ.get("DEFAULT_PROVIDER", "openrouter"),
    }

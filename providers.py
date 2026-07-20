"""Upstream providers: OpenRouter + Xiaomi MiMo + Huawei Cloud MaaS."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass
class ProviderRoute:
    name: str  # openrouter | xiaomi | huawei
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
    # Xiaomi
    "mimo": "mimo-v2.5",
    "mimo-pro": "mimo-v2.5-pro",
    "xiaomi/mimo-v2.5": "mimo-v2.5",
    "xiaomi/mimo-v2.5-pro": "mimo-v2.5-pro",
    # Huawei MaaS shortcuts
    "glm-5": "glm-5",
    "huawei/glm-5": "glm-5",
    "huawei/deepseek-v3.2": "deepseek-v3.2",
    "hw/glm-5": "glm-5",
    "hw/deepseek-v3.2": "deepseek-v3.2",
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
        return os.environ.get(
            "XIAOMI_TOKEN_PLAN_BASE_URL", "https://token-plan-sgp.xiaomimimo.com/v1"
        ).rstrip("/")
    return "https://api.xiaomimimo.com/v1"


def _huawei_key() -> str:
    for name in (
        "HUAWEI_MAAS_API_KEY",
        "HUAWEI_CLOUD_MAAS_API_KEY",
        "MODELARTS_MAAS_API_KEY",
        "HW_MAAS_API_KEY",
    ):
        v = os.environ.get(name, "").strip()
        if v:
            return v
    return ""


def _huawei_base() -> str:
    """OpenAI-compatible base (…/v2 or …/openai/v1)."""
    for name in (
        "HUAWEI_MAAS_BASE_URL",
        "HUAWEI_CLOUD_MAAS_BASE_URL",
        "MODELARTS_MAAS_BASE_URL",
    ):
        v = os.environ.get(name, "").strip()
        if v:
            return v.rstrip("/")
    # Docs (OpenClaw / Getting Started): /v2 ; OpenAI SDK style: /openai/v1
    style = os.environ.get("HUAWEI_MAAS_API_STYLE", "v2").strip().lower()
    host = "https://api-ap-southeast-1.modelarts-maas.com"
    if style in {"openai", "openai/v1", "openai-v1"}:
        return f"{host}/openai/v1"
    return f"{host}/v2"


def resolve_model(model: str | None) -> str:
    m = (model or "unknown").strip()
    return MODEL_ALIASES.get(m, m)


def detect_provider(model: str) -> str:
    m = model.lower()
    if m.startswith(("huawei/", "hw/", "modelarts/", "hw-maas")):
        return "huawei"
    # Native Huawei MaaS model ids (when key is present)
    if _huawei_key() and m in {"glm-5", "glm-5.2", "deepseek-v3.2"}:
        return "huawei"
    if m.startswith(("xiaomi/", "mimo/", "mimo-")):
        return "xiaomi"
    if m.startswith("openrouter/"):
        return "openrouter"
    default = os.environ.get("DEFAULT_PROVIDER", "openrouter").strip().lower()
    if default in {"openrouter", "xiaomi", "huawei"}:
        return default
    return "openrouter"


def prepare_route(
    body: dict[str, Any],
    *,
    openrouter_key: str,
    openrouter_base: str,
    force_reasoning_none: bool = True,
) -> ProviderRoute:
    out = dict(body)
    model = resolve_model(str(out.get("model") or "unknown"))
    upstream_model = model
    for prefix in (
        "xiaomi/",
        "mimo/",
        "openrouter/",
        "huawei/",
        "hw/",
        "modelarts/",
        "hw-maas-pan/",
    ):
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
        if "max_tokens" in out and "max_completion_tokens" not in out:
            out["max_completion_tokens"] = out.pop("max_tokens")
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

    if provider == "huawei":
        key = _huawei_key()
        if not key:
            raise RuntimeError(
                "Huawei MaaS key not configured "
                "(HUAWEI_MAAS_API_KEY / MODELARTS_MAAS_API_KEY)"
            )
        out.pop("reasoning", None)
        out.pop("include_reasoning", None)
        out.pop("stream_options", None)
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        return ProviderRoute(
            name="huawei",
            base_url=_huawei_base(),
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
    hk = _huawei_key()
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
        "huawei": {
            "configured": bool(hk),
            "base_url": _huawei_base() if hk else None,
            "key_hint": (hk[:8] + "…") if hk else None,
            "region": "ap-southeast-1",
        },
        "default_provider": os.environ.get("DEFAULT_PROVIDER", "openrouter"),
    }

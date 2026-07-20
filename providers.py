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


# Exact ids on Huawei research LiteLLM (case-sensitive upstream)
HUAWEI_LITELLM_MODELS = {
    "glm-5",
    "glm-5.1",
    "glm-5.2",
    "DeepSeek-V4-Flash",
    "deepseek-v4-pro",
    "DeepSeek-V3.2",
    "DeepSeek-V3",
    "deepseek-v3.1-terminus",
    "DeepSeek-R1-250528",
}
_HUAWEI_LITELLM_LOWER = {m.lower(): m for m in HUAWEI_LITELLM_MODELS}

def _prefer_huawei_deepseek() -> bool:
    """When Huawei trial key is set, Cursor's deepseek/* ids go to LiteLLM (not OpenRouter)."""
    flag = os.environ.get("PREFER_HUAWEI_DEEPSEEK", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    return bool(_huawei_key())


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
    # Huawei Token Service / LiteLLM shortcuts → exact upstream ids
    "glm-5": "glm-5",
    "glm-5.1": "glm-5.1",
    "glm-5.2": "glm-5.2",
    "huawei/glm-5": "glm-5",
    "huawei/glm-5.1": "glm-5.1",
    "huawei/glm-5.2": "glm-5.2",
    "hw/glm-5": "glm-5",
    "hw/glm-5.2": "glm-5.2",
    "huawei/flash": "DeepSeek-V4-Flash",
    "hw/flash": "DeepSeek-V4-Flash",
    "huawei/DeepSeek-V4-Flash": "DeepSeek-V4-Flash",
    "huawei/deepseek-v4-flash": "DeepSeek-V4-Flash",
    "huawei/pro": "deepseek-v4-pro",
    "hw/pro": "deepseek-v4-pro",
    "huawei/deepseek-v4-pro": "deepseek-v4-pro",
    "huawei/DeepSeek-V3.2": "DeepSeek-V3.2",
    "huawei/deepseek-v3.2": "DeepSeek-V3.2",
    "hw/deepseek-v3.2": "DeepSeek-V3.2",
    "huawei/DeepSeek-V3": "DeepSeek-V3",
    "huawei/DeepSeek-R1-250528": "DeepSeek-R1-250528",
    "huawei/deepseek-v3.1-terminus": "deepseek-v3.1-terminus",
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
    """OpenAI-compatible base URL for Huawei.

    Research / Token Service trial uses a LiteLLM gateway (default).
    Production console keys use ModelArts host (/v2 or /openai/v1).
    """
    for name in (
        "HUAWEI_MAAS_BASE_URL",
        "HUAWEI_CLOUD_MAAS_BASE_URL",
        "MODELARTS_MAAS_BASE_URL",
    ):
        v = os.environ.get(name, "").strip()
        if v:
            return v.rstrip("/")
    style = os.environ.get("HUAWEI_MAAS_API_STYLE", "litellm").strip().lower()
    if style in {"v2", "openclaw"}:
        return "https://api-ap-southeast-1.modelarts-maas.com/v2"
    if style in {"openai", "openai/v1", "openai-v1", "modelarts"}:
        return "https://api-ap-southeast-1.modelarts-maas.com/openai/v1"
    # Default: Huawei research LiteLLM Token Service
    return os.environ.get(
        "HUAWEI_LITELLM_BASE_URL", "http://176.52.143.34:4000/v1"
    ).rstrip("/")


def _normalize_huawei_model(model: str) -> str:
    """Map to exact LiteLLM id casing when known."""
    if model in HUAWEI_LITELLM_MODELS:
        return model
    return _HUAWEI_LITELLM_LOWER.get(model.lower(), model)


def resolve_model(model: str | None) -> str:
    m = (model or "unknown").strip()
    # Prefer Huawei LiteLLM when Cursor still has deepseek/* selected
    if _prefer_huawei_deepseek():
        low = m.lower()
        if low in {
            "deepseek/deepseek-v4-flash",
            "deepseek-v4-flash",
            "chat",
        }:
            return "DeepSeek-V4-Flash"
        if low in {
            "deepseek/deepseek-v4-pro",
            "deepseek-v4-pro",
            "coder",
        }:
            return "deepseek-v4-pro"
    return MODEL_ALIASES.get(m, m)


def detect_provider(model: str) -> str:
    m = model.lower()
    if m.startswith(("huawei/", "hw/", "modelarts/", "hw-maas")):
        return "huawei"
    bare = m
    for prefix in ("huawei/", "hw/", "modelarts/", "hw-maas-pan/", "hw-maas/"):
        if bare.startswith(prefix):
            bare = bare[len(prefix) :]
            break
    # Cursor default ids remapped to Huawei
    if _prefer_huawei_deepseek() and bare in {
        "deepseek/deepseek-v4-flash",
        "deepseek-v4-flash",
        "deepseek/deepseek-v4-pro",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    }:
        return "huawei"
    if _huawei_key() and (
        bare.startswith("glm-")
        or bare in _HUAWEI_LITELLM_LOWER
        or model in HUAWEI_LITELLM_MODELS
        or bare.startswith("deepseek-v")
        or bare.startswith("deepseek-r")
    ):
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
        "hw-maas/",
    ):
        if upstream_model.lower().startswith(prefix):
            upstream_model = upstream_model[len(prefix) :]
            break
    provider = detect_provider(model)

    if provider == "xiaomi":
        key = _xiaomi_key()
        if not key:
            raise RuntimeError(
                "Xiaomi MaaS key not configured "
                "(XIAOMI_MAAS_API_KEY / XIAOMI_MIMO_API_KEY / MIMO_API_KEY)"
            )
        out["model"] = upstream_model
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
        upstream_model = _normalize_huawei_model(upstream_model)
        out["model"] = upstream_model
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
    out["model"] = upstream_model
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
            "style": os.environ.get("HUAWEI_MAAS_API_STYLE", "litellm"),
            "models": sorted(HUAWEI_LITELLM_MODELS),
        },
        "default_provider": os.environ.get("DEFAULT_PROVIDER", "openrouter"),
    }

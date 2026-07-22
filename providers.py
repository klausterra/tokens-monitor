"""Upstream providers: OpenRouter + Xiaomi + Huawei MaaS + NVIDIA NIM."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass
class ProviderRoute:
    name: str  # openrouter | xiaomi | huawei | nvidia
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

# Common NVIDIA build.nvidia.com / integrate.api models
NVIDIA_MODELS = {
    "nvidia/nemotron-3-nano-30b-a3b",
    "nvidia/nemotron-3-super-120b-a12b",
    # Additional NIM models currently available on the public catalog
    "nvidia/nemotron-3.5-content-safety:free",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "nvidia/nemotron-nano-9b-v2:free",
    # Third-party models hosted on NVIDIA NIMs
    "meta/llama3-8b-instruct",
    "meta/llama3-70b-instruct",
    "mistralai/mistral-7b-instruct-v0.2",
    "mistralai/mixtral-8x7b-instruct-v0.1",
    "google/gemma-2-9b",
    "google/gemma-2-27b",
    "microsoft/phi-3-mini-128k-instruct",
    "microsoft/phi-3-medium-128k-instruct",
}

def _prefer_huawei_deepseek() -> bool:
    """When Huawei trial key is set, Cursor's deepseek/* ids go to LiteLLM (not OpenRouter)."""
    flag = os.environ.get("PREFER_HUAWEI_DEEPSEEK", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    return bool(_huawei_key())


MODEL_ALIASES = {
    # ---- Bare shortcuts (Cursor convenience) ----
    "chat": "deepseek/deepseek-v4-flash",
    "coder": "deepseek/deepseek-v4-pro",
    "glm-5": "glm-5",
    "glm-5.1": "glm-5.1",
    "glm-5.2": "glm-5.2",
    "r1": "DeepSeek-R1-250528",
    # ---- Xiaomi ----
    "mimo": "mimo-v2.5",
    "mimo-pro": "mimo-v2.5-pro",
    "xiaomi/mimo-v2.5": "mimo-v2.5",
    "xiaomi/mimo-v2.5-pro": "mimo-v2.5-pro",
    # ---- Huawei (hw/) ----
    "hw/glm-5": "glm-5",
    "hw/glm-5.1": "glm-5.1",
    "hw/glm-5.2": "glm-5.2",
    "hw/flash": "DeepSeek-V4-Flash",
    "hw/pro": "deepseek-v4-pro",
    "hw/r1": "DeepSeek-R1-250528",
    "hw/v3": "DeepSeek-V3",
    "hw/v3.2": "DeepSeek-V3.2",
    "hw/terminus": "deepseek-v3.1-terminus",
    # ---- NVIDIA NIM (nv/) ----
    "nv/nano": "nvidia/nemotron-3-nano-30b-a3b",
    "nv/super": "nvidia/nemotron-3-super-120b-a12b",
    "nv/ultra": "nvidia/nemotron-3-ultra-550b-a55b",
    "nv/9b": "nvidia/nemotron-nano-9b-v2:free",
    "nv/12b": "nvidia/nemotron-nano-12b-v2-vl:free",
    "nv/llama3-8b": "meta/llama3-8b-instruct",
    "nv/llama3-70b": "meta/llama3-70b-instruct",
    "nv/mistral-7b": "mistralai/mistral-7b-instruct-v0.2",
    "nv/mixtral-8x7b": "mistralai/mixtral-8x7b-instruct-v0.1",
    "nv/gemma-2-9b": "google/gemma-2-9b",
    "nv/gemma-2-27b": "google/gemma-2-27b",
    "nv/phi-3-mini": "microsoft/phi-3-mini-128k-instruct",
    "nv/phi-3-medium": "microsoft/phi-3-medium-128k-instruct",
    # ---- OpenRouter (or/) — force OpenRouter even when Huawei/NIM default exists ----
    "or/flash": "openrouter/deepseek/deepseek-v4-flash",
    "or/pro": "openrouter/deepseek/deepseek-v4-pro",
    "or/r1": "openrouter/deepseek/deepseek-r1",
    "or/glm-5.2": "openrouter/z-ai/glm-5.2",
    "or/gemma-4-26b": "openrouter/google/gemma-4-26b-a4b-it",
    "or/qwen3.5-9b": "openrouter/qwen/qwen3.5-9b",
    "or/ultra-free": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
    "or/nano": "openrouter/nvidia/nemotron-3-nano-30b-a3b",
    "or/super": "openrouter/nvidia/nemotron-3-super-120b-a12b",
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
    return os.environ.get(
        "HUAWEI_LITELLM_BASE_URL", "http://176.52.143.34:4000/v1"
    ).rstrip("/")


def _nvidia_key() -> str:
    for name in (
        "NVIDIA_API_KEY",
        "NVIDIA_NIM_API_KEY",
        "NVCF_API_KEY",
        "NGC_API_KEY",
    ):
        v = os.environ.get(name, "").strip()
        if v:
            return v
    return ""


def _nvidia_base() -> str:
    for name in ("NVIDIA_BASE_URL", "NVIDIA_NIM_BASE_URL"):
        v = os.environ.get(name, "").strip()
        if v:
            return v.rstrip("/")
    return "https://integrate.api.nvidia.com/v1"


def _normalize_huawei_model(model: str) -> str:
    """Map to exact LiteLLM id casing when known."""
    if model in HUAWEI_LITELLM_MODELS:
        return model
    return _HUAWEI_LITELLM_LOWER.get(model.lower(), model)


def resolve_model(model: str | None) -> str:
    m = (model or "unknown").strip()
    # or/ prefix → force OpenRouter, never remap to Huawei/NIM
    if m.lower().startswith("or/"):
        return MODEL_ALIASES.get(m, MODEL_ALIASES.get(m.lower(), m))
    # Prefer Huawei LiteLLM when Cursor still has deepseek/* / z-ai/glm* selected
    if _prefer_huawei_deepseek() or _huawei_key():
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
        # OpenRouter Z.ai catalog → Huawei trial GLM
        if low in MODEL_ALIASES and low.startswith("z-ai/glm"):
            return MODEL_ALIASES[low]
        if low.startswith("z-ai/glm-5"):
            # glm-5.2, glm-5.1, glm-5, turbo → closest trial id
            if "5.2" in low or "turbo" in low:
                return "glm-5.2"
            if "5.1" in low:
                return "glm-5.1"
            return "glm-5"
    return MODEL_ALIASES.get(m, MODEL_ALIASES.get(m.lower(), m))


def detect_provider(model: str) -> str:
    m = model.lower()
    # or/ prefix → force OpenRouter
    if m.startswith("or/"):
        return "openrouter"
    # openrouter/ prefix → OpenRouter
    if m.startswith("openrouter/"):
        return "openrouter"
    # NVIDIA :free variants → OpenRouter (NIM direct doesn't accept :free)
    if m.startswith("nvidia/") and m.endswith(":free"):
        return "openrouter"
    # NVIDIA NIM / build.nvidia.com (keep nvidia/… ids upstream)
    if m.startswith(("nvidia/", "nv/", "nemotron-", "nemotron/")):
        return "nvidia"
    if m in {x.lower() for x in NVIDIA_MODELS if not x.endswith(":free")}:
        return "nvidia"
    if m.startswith(("huawei/", "hw/", "modelarts/", "hw-maas", "z-ai/glm")):
        if m.startswith("z-ai/glm") and not _huawei_key():
            return "openrouter"
        if m.startswith("z-ai/glm") and _huawei_key():
            return "huawei"
        if m.startswith(("huawei/", "hw/", "modelarts/", "hw-maas")):
            return "huawei"
    bare = m
    for prefix in (
        "huawei/",
        "hw/",
        "modelarts/",
        "hw-maas-pan/",
        "hw-maas/",
        "z-ai/",
        "nv/",
    ):
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
    default = os.environ.get("DEFAULT_PROVIDER", "openrouter").strip().lower()
    if default in {"openrouter", "xiaomi", "huawei", "nvidia"}:
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
        "or/",
        "huawei/",
        "hw/",
        "modelarts/",
        "hw-maas-pan/",
        "hw-maas/",
        "z-ai/",
        "nv/",
    ):
        if upstream_model.lower().startswith(prefix):
            upstream_model = upstream_model[len(prefix) :]
            break
    provider = detect_provider(model)

    if provider == "nvidia":
        key = _nvidia_key()
        if not key:
            raise RuntimeError(
                "NVIDIA API key not configured "
                "(NVIDIA_API_KEY / NVIDIA_NIM_API_KEY / NGC_API_KEY)"
            )
        # Upstream expects full catalog ids like nvidia/nemotron-…
        if not upstream_model.lower().startswith("nvidia/"):
            if upstream_model.lower().startswith("nemotron"):
                upstream_model = f"nvidia/{upstream_model}"
            elif model.lower().startswith("nvidia/"):
                upstream_model = model
        out["model"] = upstream_model
        out.pop("reasoning", None)
        out.pop("include_reasoning", None)
        out.pop("stream_options", None)
        out.pop("thinking", None)
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        return ProviderRoute(
            name="nvidia",
            base_url=_nvidia_base(),
            api_key=key,
            model=upstream_model,
            headers=headers,
            body=out,
        )

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
        # LiteLLM rejects `thinking` for these models — stream_rewrite maps reasoning→content
        out.pop("thinking", None)
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
    nk = _nvidia_key()
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
        "nvidia": {
            "configured": bool(nk),
            "base_url": _nvidia_base() if nk else None,
            "key_hint": (nk[:8] + "…") if nk else None,
            "models": sorted(NVIDIA_MODELS),
        },
        "default_provider": os.environ.get("DEFAULT_PROVIDER", "openrouter"),
    }

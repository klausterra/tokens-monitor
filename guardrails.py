"""Configurable guardrails for tokens-monitor."""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GuardrailsConfig:
    enabled: bool = True
    # Rate limits (rolling window)
    max_requests_per_minute: int | None = None
    max_tokens_per_minute: int | None = None
    # Spend caps (proxy-tracked USD)
    max_cost_usd_per_day: float | None = None
    max_cost_usd_per_month: float | None = None
    # Per-request limits
    max_prompt_tokens: int | None = None  # estimated from messages length if set
    max_completion_tokens: int | None = 8192
    # Model allow/deny (exact ids or prefixes ending with *)
    allowed_models: list[str] = field(default_factory=list)
    blocked_models: list[str] = field(default_factory=list)
    # Providers: openrouter, xiaomi
    allowed_providers: list[str] = field(
        default_factory=lambda: ["openrouter", "xiaomi", "huawei", "nvidia"]
    )
    # OpenRouter balance floor (credits remaining)
    min_openrouter_credits: float | None = None
    # Force cheap defaults
    force_reasoning_none: bool = True
    block_on_guardrail: bool = True

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_list(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def _env_float(name: str) -> float | None:
    v = os.environ.get(name, "").strip()
    if not v:
        return None
    return float(v)


def _env_int(name: str) -> int | None:
    v = os.environ.get(name, "").strip()
    if not v:
        return None
    return int(v)


def load_guardrails(path: Path) -> GuardrailsConfig:
    cfg = GuardrailsConfig()
    # File overrides defaults
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    # Env overrides file (ops-friendly)
    if os.environ.get("GUARDRAILS_ENABLED", "").strip() != "":
        cfg.enabled = os.environ.get("GUARDRAILS_ENABLED", "true").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    if (v := _env_int("GUARDRAILS_MAX_RPM")) is not None:
        cfg.max_requests_per_minute = v
    if (v := _env_int("GUARDRAILS_MAX_TPM")) is not None:
        cfg.max_tokens_per_minute = v
    if (v := _env_float("GUARDRAILS_MAX_COST_USD_DAY")) is not None:
        cfg.max_cost_usd_per_day = v
    if (v := _env_float("GUARDRAILS_MAX_COST_USD_MONTH")) is not None:
        cfg.max_cost_usd_per_month = v
    if (v := _env_int("GUARDRAILS_MAX_COMPLETION_TOKENS")) is not None:
        cfg.max_completion_tokens = v
    if (v := _env_float("GUARDRAILS_MIN_OPENROUTER_CREDITS")) is not None:
        cfg.min_openrouter_credits = v
    if os.environ.get("GUARDRAILS_ALLOWED_MODELS", "").strip():
        cfg.allowed_models = _parse_list(os.environ["GUARDRAILS_ALLOWED_MODELS"])
    if os.environ.get("GUARDRAILS_BLOCKED_MODELS", "").strip():
        cfg.blocked_models = _parse_list(os.environ["GUARDRAILS_BLOCKED_MODELS"])
    if os.environ.get("GUARDRAILS_ALLOWED_PROVIDERS", "").strip():
        cfg.allowed_providers = _parse_list(os.environ["GUARDRAILS_ALLOWED_PROVIDERS"])
    return cfg


def save_guardrails(path: Path, cfg: GuardrailsConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cfg.to_public_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _match_model(model: str, patterns: list[str]) -> bool:
    for p in patterns:
        if p.endswith("*"):
            if model.startswith(p[:-1]):
                return True
        elif model == p:
            return True
    return False


def estimate_prompt_tokens(messages: list[Any]) -> int:
    """Rough char/4 estimate — enough for soft guardrails."""
    total = 0
    for m in messages or []:
        if isinstance(m, dict):
            c = m.get("content")
            if isinstance(c, str):
                total += len(c)
            elif isinstance(c, list):
                for part in c:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        total += len(part["text"])
    return max(1, total // 4)


class GuardrailEnforcer:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.cfg = load_guardrails(path)
        self._window: list[tuple[float, int]] = []  # (ts, tokens)

    def reload(self) -> GuardrailsConfig:
        with self._lock:
            self.cfg = load_guardrails(self.path)
            return self.cfg

    def update(self, patch: dict[str, Any]) -> GuardrailsConfig:
        with self._lock:
            data = self.cfg.to_public_dict()
            for k, v in patch.items():
                if k in data:
                    data[k] = v
            # rewrite file then reload (env still wins on next process start;
            # for live update we apply patch onto object after save)
            save_guardrails(self.path, GuardrailsConfig(**{**asdict(GuardrailsConfig()), **data}))
            self.cfg = load_guardrails(self.path)
            # re-apply patch so file+live match even if env overrides exist
            for k, v in patch.items():
                if hasattr(self.cfg, k):
                    setattr(self.cfg, k, v)
            return self.cfg

    def _prune_window(self, now: float) -> None:
        cutoff = now - 60.0
        self._window = [(t, n) for t, n in self._window if t >= cutoff]

    def note_request(self, tokens: int = 0) -> None:
        now = time.time()
        with self._lock:
            self._prune_window(now)
            self._window.append((now, max(0, int(tokens))))

    def check(
        self,
        *,
        model: str,
        provider: str,
        body: dict[str, Any],
        usage_day_cost: float,
        usage_month_cost: float,
        openrouter_remaining: float | None,
    ) -> tuple[bool, str | None, str | None]:
        """Returns (ok, code, detail)."""
        cfg = self.cfg
        if not cfg.enabled:
            return True, None, None

        if cfg.allowed_providers and provider not in cfg.allowed_providers:
            return False, "provider_blocked", f"Provider '{provider}' not in allowed_providers"

        if cfg.blocked_models and _match_model(model, cfg.blocked_models):
            return False, "model_blocked", f"Model '{model}' is blocked"

        if cfg.allowed_models and not _match_model(model, cfg.allowed_models):
            return False, "model_not_allowed", f"Model '{model}' not in allowed_models"

        max_out = body.get("max_tokens") or body.get("max_completion_tokens")
        if cfg.max_completion_tokens is not None and max_out is not None:
            try:
                if int(max_out) > cfg.max_completion_tokens:
                    return (
                        False,
                        "max_completion_tokens",
                        f"max_tokens {max_out} > {cfg.max_completion_tokens}",
                    )
            except (TypeError, ValueError):
                pass

        if cfg.max_prompt_tokens is not None:
            est = estimate_prompt_tokens(body.get("messages") or [])
            if est > cfg.max_prompt_tokens:
                return (
                    False,
                    "max_prompt_tokens",
                    f"estimated prompt tokens {est} > {cfg.max_prompt_tokens}",
                )

        if cfg.max_cost_usd_per_day is not None and usage_day_cost >= cfg.max_cost_usd_per_day:
            return (
                False,
                "daily_cost_cap",
                f"daily cost ${usage_day_cost:.4f} >= ${cfg.max_cost_usd_per_day}",
            )

        if (
            cfg.max_cost_usd_per_month is not None
            and usage_month_cost >= cfg.max_cost_usd_per_month
        ):
            return (
                False,
                "monthly_cost_cap",
                f"monthly cost ${usage_month_cost:.4f} >= ${cfg.max_cost_usd_per_month}",
            )

        if (
            provider == "openrouter"
            and cfg.min_openrouter_credits is not None
            and openrouter_remaining is not None
            and openrouter_remaining < cfg.min_openrouter_credits
        ):
            return (
                False,
                "openrouter_credits_low",
                f"remaining {openrouter_remaining:.4f} < {cfg.min_openrouter_credits}",
            )

        now = time.time()
        with self._lock:
            self._prune_window(now)
            rpm = len(self._window)
            tpm = sum(n for _, n in self._window)
        if cfg.max_requests_per_minute is not None and rpm >= cfg.max_requests_per_minute:
            return False, "rpm_exceeded", f"RPM {rpm} >= {cfg.max_requests_per_minute}"
        if cfg.max_tokens_per_minute is not None and tpm >= cfg.max_tokens_per_minute:
            return False, "tpm_exceeded", f"TPM {tpm} >= {cfg.max_tokens_per_minute}"

        return True, None, None

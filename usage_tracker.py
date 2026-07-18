"""Real-time OpenRouter usage tracking + optimization hints."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RequestUsage:
    ts: float
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    stream: bool = False
    latency_ms: int = 0
    status: int = 200


@dataclass
class OptimizationHint:
    code: str
    severity: str  # info | warn | critical
    title: str
    detail: str
    estimated_savings_pct: float | None = None


class UsageTracker:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._recent: list[RequestUsage] = []
        self._recent_max = 500
        self._totals = {
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "errors": 0,
        }
        self._by_model: dict[str, dict[str, float]] = defaultdict(
            lambda: {
                "requests": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
            }
        )
        self._started_at = time.time()
        self._init_db()
        self._load_totals_from_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    model TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL,
                    completion_tokens INTEGER NOT NULL,
                    reasoning_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    cost_usd REAL NOT NULL,
                    stream INTEGER NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    status INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(ts)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_events(model)"
            )

    def _load_totals_from_db(self) -> None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS requests,
                    COALESCE(SUM(prompt_tokens),0),
                    COALESCE(SUM(completion_tokens),0),
                    COALESCE(SUM(reasoning_tokens),0),
                    COALESCE(SUM(total_tokens),0),
                    COALESCE(SUM(cost_usd),0.0),
                    COALESCE(SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END),0)
                FROM usage_events
                """
            ).fetchone()
            if row:
                self._totals = {
                    "requests": int(row[0]),
                    "prompt_tokens": int(row[1]),
                    "completion_tokens": int(row[2]),
                    "reasoning_tokens": int(row[3]),
                    "total_tokens": int(row[4]),
                    "cost_usd": float(row[5]),
                    "errors": int(row[6]),
                }
            for r in conn.execute(
                """
                SELECT model,
                       COUNT(*),
                       COALESCE(SUM(prompt_tokens),0),
                       COALESCE(SUM(completion_tokens),0),
                       COALESCE(SUM(total_tokens),0),
                       COALESCE(SUM(cost_usd),0.0)
                FROM usage_events GROUP BY model
                """
            ):
                self._by_model[r[0]] = {
                    "requests": float(r[1]),
                    "prompt_tokens": float(r[2]),
                    "completion_tokens": float(r[3]),
                    "total_tokens": float(r[4]),
                    "cost_usd": float(r[5]),
                }

    def record(self, event: RequestUsage) -> None:
        with self._lock:
            self._recent.append(event)
            if len(self._recent) > self._recent_max:
                self._recent = self._recent[-self._recent_max :]
            self._totals["requests"] += 1
            self._totals["prompt_tokens"] += event.prompt_tokens
            self._totals["completion_tokens"] += event.completion_tokens
            self._totals["reasoning_tokens"] += event.reasoning_tokens
            self._totals["total_tokens"] += event.total_tokens
            self._totals["cost_usd"] += event.cost_usd
            if event.status >= 400:
                self._totals["errors"] += 1
            m = self._by_model[event.model]
            m["requests"] += 1
            m["prompt_tokens"] += event.prompt_tokens
            m["completion_tokens"] += event.completion_tokens
            m["total_tokens"] += event.total_tokens
            m["cost_usd"] += event.cost_usd
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO usage_events
                    (ts, model, prompt_tokens, completion_tokens, reasoning_tokens,
                     total_tokens, cost_usd, stream, latency_ms, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.ts,
                        event.model,
                        event.prompt_tokens,
                        event.completion_tokens,
                        event.reasoning_tokens,
                        event.total_tokens,
                        event.cost_usd,
                        1 if event.stream else 0,
                        event.latency_ms,
                        event.status,
                    ),
                )

    def snapshot(self, window_seconds: int = 300) -> dict[str, Any]:
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            recent = [e for e in self._recent if e.ts >= cutoff]
            totals = dict(self._totals)
            by_model = {k: dict(v) for k, v in self._by_model.items()}
            recent_dicts = [asdict(e) for e in recent[-50:]]

        window_tokens = sum(e.total_tokens for e in recent)
        window_cost = sum(e.cost_usd for e in recent)
        window_req = len(recent)
        rpm = (window_req / window_seconds) * 60 if window_seconds else 0
        tpm = (window_tokens / window_seconds) * 60 if window_seconds else 0

        return {
            "started_at": self._started_at,
            "uptime_seconds": int(now - self._started_at),
            "window_seconds": window_seconds,
            "window": {
                "requests": window_req,
                "tokens": window_tokens,
                "cost_usd": round(window_cost, 8),
                "requests_per_minute": round(rpm, 3),
                "tokens_per_minute": round(tpm, 3),
            },
            "lifetime": {
                **totals,
                "cost_usd": round(float(totals["cost_usd"]), 8),
            },
            "by_model": {
                k: {
                    **v,
                    "cost_usd": round(float(v["cost_usd"]), 8),
                    "requests": int(v["requests"]),
                    "prompt_tokens": int(v["prompt_tokens"]),
                    "completion_tokens": int(v["completion_tokens"]),
                    "total_tokens": int(v["total_tokens"]),
                }
                for k, v in sorted(by_model.items(), key=lambda x: -x[1]["cost_usd"])
            },
            "recent": recent_dicts,
        }

    def prometheus(self, balance: dict[str, Any] | None = None) -> str:
        snap = self.snapshot()
        lines: list[str] = []
        life = snap["lifetime"]
        win = snap["window"]

        def g(name: str, value: float, labels: str = "", help_txt: str = "") -> None:
            if help_txt:
                lines.append(f"# HELP {name} {help_txt}")
                lines.append(f"# TYPE {name} gauge")
            metric = f"{name}{{{labels}}} {value}" if labels else f"{name} {value}"
            lines.append(metric)

        g("openrouter_proxy_uptime_seconds", snap["uptime_seconds"], help_txt="Proxy uptime")
        g("openrouter_proxy_requests_total", life["requests"], help_txt="Lifetime requests")
        g("openrouter_proxy_errors_total", life["errors"], help_txt="Lifetime HTTP errors")
        g("openrouter_proxy_prompt_tokens_total", life["prompt_tokens"], help_txt="Prompt tokens")
        g(
            "openrouter_proxy_completion_tokens_total",
            life["completion_tokens"],
            help_txt="Completion tokens",
        )
        g("openrouter_proxy_tokens_total", life["total_tokens"], help_txt="Total tokens")
        g("openrouter_proxy_cost_usd_total", life["cost_usd"], help_txt="Estimated cost USD via proxy")
        g("openrouter_proxy_window_rpm", win["requests_per_minute"], help_txt="RPM in window")
        g("openrouter_proxy_window_tpm", win["tokens_per_minute"], help_txt="TPM in window")
        g("openrouter_proxy_window_cost_usd", win["cost_usd"], help_txt="Cost in window")

        for model, stats in snap["by_model"].items():
            safe = model.replace("\\", "\\\\").replace('"', '\\"')
            g(
                "openrouter_proxy_model_cost_usd_total",
                stats["cost_usd"],
                f'model="{safe}"',
            )
            g(
                "openrouter_proxy_model_tokens_total",
                stats["total_tokens"],
                f'model="{safe}"',
            )
            g(
                "openrouter_proxy_model_requests_total",
                stats["requests"],
                f'model="{safe}"',
            )

        if balance:
            rem = balance.get("remaining_credits")
            if rem is not None:
                g("openrouter_remaining_credits", float(rem), help_txt="Credits remaining")
            tot = balance.get("total_credits")
            if tot is not None:
                g("openrouter_total_credits", float(tot), help_txt="Total credits purchased")
            usage = balance.get("total_usage")
            if usage is not None:
                g("openrouter_account_usage_usd", float(usage), help_txt="Account lifetime usage USD")
            pct = balance.get("remaining_pct")
            if pct is not None:
                g("openrouter_remaining_pct", float(pct), help_txt="Remaining credits percent")

        return "\n".join(lines) + "\n"


def parse_usage_from_openai_payload(data: dict[str, Any]) -> dict[str, Any]:
    usage = data.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    cost = usage.get("cost")
    if cost is None:
        cost = (usage.get("cost_details") or {}).get("upstream_inference_cost") or 0.0
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "reasoning_tokens": int(details.get("reasoning_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "cost_usd": float(cost or 0.0),
    }


def parse_usage_from_sse_chunk(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not data.get("usage"):
        return None
    return parse_usage_from_openai_payload(data)


def build_optimization_hints(
    snapshot: dict[str, Any],
    balance: dict[str, Any] | None,
) -> list[OptimizationHint]:
    hints: list[OptimizationHint] = []
    life = snapshot["lifetime"]
    win = snapshot["window"]
    by_model = snapshot.get("by_model") or {}

    if balance:
        rem_pct = balance.get("remaining_pct")
        rem = balance.get("remaining_credits")
        if rem is not None and rem < 2:
            hints.append(
                OptimizationHint(
                    code="credits_critical",
                    severity="critical",
                    title="Saldo OpenRouter crítico",
                    detail=f"Restam ~{rem:.4f} créditos. Recarregue para evitar interrupção.",
                )
            )
        elif rem_pct is not None and rem_pct < 15:
            hints.append(
                OptimizationHint(
                    code="credits_low",
                    severity="warn",
                    title="Saldo OpenRouter baixo",
                    detail=f"Restam {rem_pct:.1f}% dos créditos. Planeje recarga.",
                )
            )

    # Prefer flash over pro when pro dominates cost
    pro_cost = float((by_model.get("deepseek/deepseek-v4-pro") or {}).get("cost_usd") or 0)
    flash_cost = float((by_model.get("deepseek/deepseek-v4-flash") or {}).get("cost_usd") or 0)
    if pro_cost > 0 and pro_cost >= flash_cost * 2:
        hints.append(
            OptimizationHint(
                code="prefer_flash",
                severity="warn",
                title="Preferir deepseek-v4-flash no dia a dia",
                detail="V4 Pro está consumindo bem mais que Flash. Reserve Pro para tarefas difíceis.",
                estimated_savings_pct=40.0,
            )
        )

    prompt = int(life.get("prompt_tokens") or 0)
    completion = int(life.get("completion_tokens") or 0)
    if prompt > 0 and prompt > completion * 8 and life.get("requests", 0) >= 10:
        hints.append(
            OptimizationHint(
                code="prompt_heavy",
                severity="info",
                title="Prompts muito maiores que completions",
                detail="Contexto enviado é grande vs resposta. Reduza histórico/regras ou use chat novo.",
                estimated_savings_pct=20.0,
            )
        )

    reasoning = int(life.get("reasoning_tokens") or 0)
    if reasoning > completion and completion > 0:
        hints.append(
            OptimizationHint(
                code="reasoning_tokens",
                severity="warn",
                title="Tokens de reasoning altos",
                detail="Thinking consome tokens sem aparecer no Cursor. Mantém reasoning.effort=none no proxy.",
                estimated_savings_pct=30.0,
            )
        )

    if win.get("requests_per_minute", 0) > 30:
        hints.append(
            OptimizationHint(
                code="high_rpm",
                severity="info",
                title="Alta taxa de requests",
                detail="RPM elevado (retries do Cursor?). Evite Agent com BYOK se houver loops.",
            )
        )

    if life.get("errors", 0) > 0 and life.get("requests", 0) > 0:
        err_rate = life["errors"] / max(life["requests"], 1)
        if err_rate > 0.1:
            hints.append(
                OptimizationHint(
                    code="error_rate",
                    severity="warn",
                    title="Taxa de erro elevada",
                    detail=f"{err_rate:.0%} das requests falharam. Verifique logs do proxy.",
                )
            )

    if not hints:
        hints.append(
            OptimizationHint(
                code="healthy",
                severity="info",
                title="Uso saudável",
                detail="Sem anomalias claras. Flash + reasoning off é o caminho econômico.",
            )
        )
    return hints


async def fetch_openrouter_balance(api_key: str, base: str = "https://openrouter.ai/api/v1") -> dict[str, Any]:
    import httpx

    headers = {"Authorization": f"Bearer {api_key}"}
    out: dict[str, Any] = {"fetched_at": time.time()}
    async with httpx.AsyncClient(timeout=30.0) as client:
        credits = await client.get(f"{base.rstrip('/')}/credits", headers=headers)
        keyinfo = await client.get(f"{base.rstrip('/')}/auth/key", headers=headers)
        if credits.status_code < 400:
            data = credits.json().get("data") or {}
            total_credits = float(data.get("total_credits") or 0)
            total_usage = float(data.get("total_usage") or 0)
            remaining = total_credits - total_usage
            out.update(
                {
                    "total_credits": total_credits,
                    "total_usage": total_usage,
                    "remaining_credits": remaining,
                    "remaining_pct": (remaining / total_credits * 100.0) if total_credits else None,
                }
            )
        else:
            out["credits_error"] = credits.text[:300]
        if keyinfo.status_code < 400:
            data = keyinfo.json().get("data") or {}
            out["key"] = {
                "label": data.get("label"),
                "usage": data.get("usage"),
                "usage_daily": data.get("usage_daily"),
                "usage_weekly": data.get("usage_weekly"),
                "usage_monthly": data.get("usage_monthly"),
                "limit": data.get("limit"),
                "limit_remaining": data.get("limit_remaining"),
                "is_free_tier": data.get("is_free_tier"),
            }
        else:
            out["key_error"] = keyinfo.text[:300]
    return out

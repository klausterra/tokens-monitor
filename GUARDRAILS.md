# Guardrails + Xiaomi MaaS

## Guardrails

File: `guardrails.json` (copy from `guardrails.example.json`)  
Or env vars `GUARDRAILS_*` (see `config.example.env`).

Live API (Bearer = metrics key):

```bash
# read
curl -s -H "Authorization: Bearer $METRICS_API_KEY" \
  https://YOUR_HOST/api/v1/guardrails | jq

# update (partial patch)
curl -s -X PUT -H "Authorization: Bearer $METRICS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"max_requests_per_minute":30,"max_cost_usd_per_day":2,"allowed_models":["deepseek/deepseek-v4-flash","mimo-v2.5*"]}' \
  https://YOUR_HOST/api/v1/guardrails | jq

# reload from disk
curl -s -X POST -H "Authorization: Bearer $METRICS_API_KEY" \
  https://YOUR_HOST/api/v1/guardrails/reload | jq
```

| Field | Effect |
|-------|--------|
| `max_requests_per_minute` | Cap RPM |
| `max_tokens_per_minute` | Cap TPM (tracked) |
| `max_cost_usd_per_day` / `_month` | Cap spend (proxy SQLite) |
| `max_completion_tokens` | Cap `max_tokens` per request |
| `allowed_models` / `blocked_models` | Exact id or prefix `foo*` |
| `allowed_providers` | `openrouter`, `xiaomi`, `huawei` |
| `min_openrouter_credits` | Block OpenRouter if balance too low |
| `force_reasoning_none` | OpenRouter: disable thinking |
| `block_on_guardrail` | If false, only log |

## Xiaomi MiMo / MaaS

In `config.env`:

```env
XIAOMI_MAAS_API_KEY=sk-...   # or tp-... for Token Plan
XIAOMI_MAAS_BASE_URL=https://api.xiaomimimo.com/v1
# Token Plan example:
# XIAOMI_MAAS_BASE_URL=https://token-plan-sgp.xiaomimimo.com/v1
```

Cursor model ids:

| Model in Cursor | Upstream |
|-----------------|----------|
| `mimo-v2.5` | Xiaomi |
| `mimo-v2.5-pro` | Xiaomi |
| `xiaomi/mimo-v2.5-pro` | Xiaomi |
| `deepseek/deepseek-v4-flash` | OpenRouter |

Routing is automatic by model name (`mimo-*` / `xiaomi/*` → Xiaomi).

## Huawei Cloud MaaS (Token Service)

Docs: [Calling models via API](https://support.huaweicloud.com/intl/en-us/model-call-maas/maas-modelarts-0908.html)

```env
HUAWEI_MAAS_API_KEY=sk-...
# default OpenClaw-style /v2:
# HUAWEI_MAAS_API_STYLE=v2
# or OpenAI SDK path:
# HUAWEI_MAAS_BASE_URL=https://api-ap-southeast-1.modelarts-maas.com/openai/v1
```

| Model in Cursor | Upstream |
|-----------------|----------|
| `glm-5` | Huawei |
| `huawei/glm-5` | Huawei |
| `deepseek-v3.2` | Huawei |
| `huawei/deepseek-v3.2` | Huawei |

Keys from research / console may take a few minutes to activate. `401 ModelArts.81003` usually means key not ready or wrong region/base URL.

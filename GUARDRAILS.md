# Guardrails + Xiaomi MaaS + Huawei Token Service

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

| Model in Cursor | Upstream |
|-----------------|----------|
| `mimo-v2.5` | Xiaomi |
| `mimo-v2.5-pro` | Xiaomi |
| `xiaomi/mimo-v2.5-pro` | Xiaomi |
| `deepseek/deepseek-v4-flash` | OpenRouter |

Routing is automatic by model name (`mimo-*` / `xiaomi/*` → Xiaomi).

## Huawei Cloud MaaS Token Service (research LiteLLM)

Trial keys from Huawei Channel Sales authenticate against their **LiteLLM gateway**, not
`api-ap-southeast-1.modelarts-maas.com` (that host returns `ModelArts.81003` for trial keys).

```env
HUAWEI_MAAS_API_KEY=sk-...
HUAWEI_MAAS_API_STYLE=litellm
HUAWEI_MAAS_BASE_URL=http://176.52.143.34:4000/v1
# Production console keys instead:
# HUAWEI_MAAS_API_STYLE=v2
# HUAWEI_MAAS_BASE_URL=https://api-ap-southeast-1.modelarts-maas.com/v2
```

Docs:
- OpenClaw: https://support.huaweicloud.com/intl/en-us/model-call-maas/maas-modelarts-0908.html
- Cursor (console keys): https://support.huaweicloud.com/intl/en-us/model-call-maas/model-call-040.html

**Test direto (email Huawei):**

```bash
curl http://176.52.143.34:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $HUAWEI_MAAS_API_KEY" \
  -d '{"model":"glm-5","messages":[{"role":"user","content":"Ola?"}]}'
```

**Via tokens-monitor (HTTPS):** Base URL `https://tokens.hipercube.ia.br/v1`, key do proxy.

| Model in Cursor | Upstream LiteLLM |
|-----------------|------------------|
| `glm-5` / `glm-5.2` | Huawei |
| `huawei/glm-5.2` | Huawei |
| `DeepSeek-V4-Flash` / `hw/flash` | Huawei |
| `deepseek-v4-pro` (OpenRouter alias) | OpenRouter — use `hw/pro` ou `huawei/deepseek-v4-pro` |
| `deepseek/deepseek-v4-flash` | OpenRouter |

LiteLLM model ids: `glm-5`, `glm-5.1`, `glm-5.2`, `DeepSeek-V4-Flash`, `deepseek-v4-pro`,
`DeepSeek-V3.2`, `DeepSeek-V3`, `deepseek-v3.1-terminus`, `DeepSeek-R1-250528`.

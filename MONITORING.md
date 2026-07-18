# Monitoring API Ã¢â‚¬â€ OpenRouter Cursor Proxy

Base: `https://YOUR_HOST`  
Auth: `Authorization: Bearer <METRICS_API_KEY>`

## Endpoints

| MÃƒÂ©todo | Path | Uso |
|--------|------|-----|
| GET | `/health` | Liveness (sem auth) |
| GET | `/api/v1/balance?refresh=true` | Saldo OpenRouter |
| GET | `/api/v1/usage/summary?window_seconds=300` | Uso + saldo + otimizaÃƒÂ§ÃƒÂ£o |
| GET | `/api/v1/usage/realtime?window_seconds=60` | Tokens/RPM em tempo real |
| GET | `/api/v1/usage/series?period=day|month|year` | Serie historica (UTC) do SQLite |
| GET | `/api/v1/optimization` | Hints de economia |
| GET | `/api/v1/monitor` | Envelope ÃƒÂºnico p/ Homarr/n8n/Grafana JSON |
| GET | `/metrics` | Prometheus |

## Exemplos

```bash
curl -s -H "Authorization: Bearer <METRICS_API_KEY>" \
  https://YOUR_HOST/api/v1/monitor | jq

curl -s -H "Authorization: Bearer <METRICS_API_KEY>" \
  https://YOUR_HOST/metrics
```

## Prometheus scrape

```yaml
- job_name: tokens-monitor
  scheme: https
  metrics_path: /metrics
  bearer_token: <METRICS_API_KEY>
  static_configs:
    - targets: ["YOUR_HOST"]
```

## Campos ÃƒÂºteis

- `balance.remaining_credits` / `remaining_pct`
- `usage.window.tokens_per_minute` / `requests_per_minute` / `cost_usd`
- `usage.lifetime.*` e `usage.by_model`
- `optimization[].severity` = `info|warn|critical`

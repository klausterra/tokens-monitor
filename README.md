# tokens-monitor

OpenAI-compatible **Cursor → OpenRouter** bridge with **realtime token usage**, **balance checks**, and a **monitoring API** (JSON + Prometheus).

Built for environments where Cursor BYOK + OpenRouter is unreliable: this proxy normalizes requests (forces `reasoning.effort=none` so Cursor does not see empty `content`), tracks usage, and exposes metrics for Homarr / Grafana / n8n / Prometheus.

## Features

- `/v1/chat/completions` OpenAI-compatible proxy to OpenRouter
- Realtime usage accounting (SQLite) from non-stream and stream responses
- OpenRouter credits/balance polling
- Optimization hints (prefer Flash, prompt-heavy, low credits, …)
- `/api/v1/monitor` envelope + `/metrics` Prometheus

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.env config.env
# edit config.env — set OPENROUTER_API_KEY and keys

python proxy.py
# listens on 0.0.0.0:4010
```

Point Cursor:

| Setting | Value |
|---------|--------|
| OpenAI API Key | your `PROXY_MASTER_KEY` |
| Override OpenAI Base URL | `https://YOUR_PUBLIC_HOST/v1` |
| Model | `deepseek/deepseek-v4-flash` |

Expose with Cloudflare Tunnel (or any HTTPS reverse proxy). Cursor rejects `localhost` for custom base URLs.

## Monitoring

See [MONITORING.md](./MONITORING.md).

```bash
curl -s -H "Authorization: Bearer $METRICS_API_KEY" http://127.0.0.1:4010/api/v1/monitor
```

## Deploy (systemd)

See `deploy/tokens-monitor.service`. Typical layout: `/opt/tokens-monitor` + Cloudflare Tunnel to `http://127.0.0.1:4010`.

## Security

- Never commit `config.env`
- Rotate keys if leaked
- Prefer a dedicated `METRICS_API_KEY` for scrapers
- Keep the proxy behind HTTPS; treat metrics as sensitive (spend data)

## License

MIT

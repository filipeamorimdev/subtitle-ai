# Configuration

Settings are stored in SQLite under `/config/subtitle-ai.db`. Secrets are Fernet-encrypted using `/config/secret.key`.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `SUBTITLE_AI_CONFIG_DIR` | `/config` | Persistent config directory |
| `SUBTITLE_AI_MEDIA_ROOTS` | _(auto)_ | Optional override. When unset, roots are discovered from container mounts under `/data` and `/media` |
| `SUBTITLE_AI_LOG_LEVEL` | `INFO` | Log level |
| `SUBTITLE_AI_HOST` / `PORT` | `0.0.0.0` / `6768` | Bind address (uvicorn CLI usually sets this) |
| `SUBTITLE_AI_FRONTEND_DIST` | auto-detected | Built Vue assets directory |

Prefer configuring Bazarr/OpenRouter credentials in the UI rather than env vars.

Media roots are **not** configured in Settings. On deploy, Subtitle AI reads `/proc/self/mountinfo` and uses bind mounts under `/data` and `/media` (e.g. compose volumes `/data/tv` and `/data/movies`). Only set `SUBTITLE_AI_MEDIA_ROOTS` if you need an explicit override.

## Job OpenRouter logs

Each translation job writes a JSONL exchange log under:

```text
/config/logs/jobs/job-{id}-openrouter.jsonl
```

By default only metadata is stored (model, attempt, status, token usage, errors). Enable **Log full OpenRouter exchanges** under **Settings → Providers** to persist full request/response bodies for debugging. The toggle defaults to off. API keys are never written.

## Settings UI sections

The Settings sidebar has **General**, **Providers**, **Models**, **Language**, and **Glossary**.

### General

- Automatic fallback: enable, scan interval, Bazarr grace period, retries, run scan now
- Job concurrency (translate / extract / request)
- Advanced cleanup (jobs, glossaries, usage stats)

When automatic fallback is disabled, Media stays click-only. When enabled, newly missing wanted items are processed automatically after the grace period and **can incur AI costs**.

API:

- `GET /api/automation/status`
- `POST /api/automation/run`

### Providers

Media:

- Bazarr URL, API key, Test Connection

AI:

- OpenRouter API key, connection test, catalog refresh, diagnostic exchange logging
- Anthropic / OpenAI placeholders (not implemented)

### Models

- Catalog, free/paid pools, routing strategy, cost caps, monthly budget
- Batch size (subtitle blocks per translation request)

### Language

- Source language preference
- Target language (wanted matching and new localize requests)

### Glossary

- Term memory and suggested-term review

## AI dashboard and model settings

AI **observability** lives on the AI dashboard (opened from Dashboard):

| Page | Purpose |
| --- | --- |
| Overview | Status, monthly usage, budget, display-only adaptive ranking, cost over time, recent routing |
| Usage | Period selector, requests/tokens/cost, success and failure rates, cost/requests by model, free vs paid, latency, paginated request history |

AI **control** lives under Settings:

| Page | Purpose |
| --- | --- |
| Providers | Bazarr connection; AI API keys (masked), connection test, catalog refresh, OpenRouter diagnostics |
| Models | Catalog, free/paid pools, priority, routing strategy, fallback, cost caps, monthly budget, batch size |

`GET /api/settings/openrouter/models` and `GET /api/ai/models` use a 6-hour catalog cache. Missing prices are **unknown**, never treated as free.

`ai_usage_records` is the authoritative historical AI cost source. Dashboard, AI Usage, AI cost charts, and Job Detail usage read those snapshots. They are never repriced against the current catalogue.

## Masking

`GET /api/settings` returns masked keys such as `sk-or-v1-••••••••••••1234` and never the full secret.

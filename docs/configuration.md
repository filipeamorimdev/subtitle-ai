# Configuration

Settings are stored in SQLite under `/config/subtitle-ai.db`. Secrets are Fernet-encrypted using `/config/secret.key`.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `SUBTITLE_AI_CONFIG_DIR` | `/config` | Persistent config directory |
| `SUBTITLE_AI_MEDIA_ROOTS` | _(auto)_ | Optional override. When unset, roots are discovered from container mounts under `/data` and `/media` |
| `SUBTITLE_AI_LOG_LEVEL` | `INFO` | Log level |
| `SUBTITLE_AI_DEBUG_TRACE` | `false` | When `true`, each audio-separation run writes a persistent trace under `/config/debug/audio-separation/<task-id>/trace.log`. Independent of `SUBTITLE_AI_LOG_LEVEL`. Off by default. |
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

## Audio separation debug traces

Audio stem separation is used by the default background-preserved dubbing mode. Enable traces with `SUBTITLE_AI_DEBUG_TRACE=true`. Each run writes:

```text
/config/debug/audio-separation/<task-id>/trace.log
```

Copy that file out of the `/config` volume to debug a run without shelling into a live container. Traces are not rotated automatically.

Manual smoke test inside the container:

```bash
docker compose exec subtitle-ai python -m app.localization.audio \
  --input /data/movies/example.mkv \
  --duration 45 \
  --debug
```

## Settings UI sections

The Settings sidebar has **General**, **Providers**, and **Models**.

### General

- Source and target language defaults
- Automatic fallback: enable, scan interval, Bazarr grace period, retries, run scan now
- Job concurrency (translate / extract / request / transcribe / dub)
- Advanced cleanup (jobs, usage stats)

When automatic fallback is disabled, Media stays click-only. When enabled, newly missing wanted items are processed automatically after the grace period and **can incur AI costs**.

API:

- `GET /api/automation/status`
- `POST /api/automation/run`

### Providers

Media:

- Jellyfin URL, API key, Test Connection (preferred movie/episode catalog when reachable)
- Bazarr URL, API key, Test Connection

The request dialogs and AI operator search Jellyfin when both Jellyfin settings are saved and the
server is reachable. They automatically fall back to Bazarr when Jellyfin is not configured or a
Jellyfin request fails. Bazarr remains responsible for subtitle requests, rescans, and verification.

AI:

- OpenRouter API key, connection test, diagnostic exchange logging

### Models

- Catalog, free/paid pools, routing strategy, cost caps, monthly budget
- Batch size (subtitle blocks per translation request)

## AI reports and model settings

AI **observability** lives on the Dashboard **AI** tab:

| Report | Purpose |
| --- | --- |
| Overview | Status, monthly usage, budget, display-only adaptive ranking, cost over time, recent routing |
| Usage | Period selector, requests/tokens/cost, success and failure rates, cost/requests by model, free vs paid, latency, paginated request history |

Per-job token/cost breakdown lives on the **job detail** page (`#usage`) for translate jobs.

AI **control** lives under Settings:

| Page | Purpose |
| --- | --- |
| Providers | Bazarr connection; AI API keys (masked), connection test, OpenRouter diagnostics |
| Models | Catalog, free/paid pools, priority, routing strategy, fallback, cost caps, monthly budget, batch size |

`GET /api/settings/openrouter/models` and `GET /api/ai/models` use a 6-hour catalog cache. Missing prices are **unknown**, never treated as free.

`ai_usage_records` is the authoritative historical AI cost source. Dashboard AI reports, cost charts, and Job Detail usage read those snapshots. They are never repriced against the current catalogue.

## Masking

`GET /api/settings` returns masked keys such as `sk-or-v1-••••••••••••1234` and never the full secret.

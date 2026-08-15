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

By default only metadata is stored (model, attempt, status, token usage, errors). Enable **Log full OpenRouter exchanges** under **AI → Models & Routing → Diagnostics** to persist full request/response bodies for debugging. The toggle defaults to off. API keys are never written.

## Settings UI sections

### Bazarr

- URL
- API key (optional depending on Bazarr setup)
- Test Connection

### Automatic Subtitle Fallback

- Enable automatic fallback (default **off**)
- Scan interval (1–1440 minutes, default 5)
- Bazarr grace period (0–1440 minutes, default 10)
- Automatic retries (0–20, default 3)
- Run automatic scan now

When disabled, Candidates stay click-only. When enabled, newly missing wanted items are processed automatically after the grace period and **can incur OpenRouter costs**.

API:

- `GET /api/automation/status`
- `POST /api/automation/run`

### Translation

- Target language code/name
- Source language preference list
- Batch size (subtitle blocks per OpenRouter request; used by translation jobs)

### Media

- Media roots are **read-only** in the UI — auto-discovered from Docker mounts under `/data` and `/media`
- Path mappings: `bazarr_prefix => local_prefix`

### Job concurrency

- Translate / extract / request limits (1–20, default 1 each)

### Advanced

- Clear jobs, glossaries, and usage stats
- OpenRouter diagnostic exchange logging is **not** here; it lives under AI → Models & Routing

## AI Control Center

All AI behavior is configured under **AI**:

| Page | Purpose |
| --- | --- |
| Overview | Status, monthly usage, budget, display-only adaptive ranking, cost over time, recent routing |
| Models & Routing | API key, catalog refresh, free/paid pools, priority, routing strategy, paid/free fallback, unknown-pricing policy, per-job cap, monthly budget, manual override, exchange-log diagnostics |
| Usage | Period selector, requests/tokens/cost, success and failure rates, cost/requests by model, free vs paid, latency, paginated request history |

`GET /api/settings/openrouter/models` and `GET /api/ai/models` use a 6-hour catalog cache. Missing prices are **unknown**, never treated as free.

`ai_usage_records` is the authoritative historical AI cost source. Dashboard, AI Usage, AI cost charts, and Job Detail usage read those snapshots. They are never repriced against the current catalogue.

## Masking

`GET /api/settings` returns masked keys such as `sk-or-v1-••••••••••••1234` and never the full secret.

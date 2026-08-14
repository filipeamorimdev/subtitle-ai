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

By default only metadata is stored (model, attempt, status, token usage, errors). Enable **Log full OpenRouter exchanges** in Settings to persist full request/response bodies for debugging. API keys are never written.

## Settings UI sections

### Bazarr

- URL
- API key (optional depending on Bazarr setup)
- Test Connection

### OpenRouter

- API key
- Searchable model picker (fetched from OpenRouter `GET /api/v1/models`, sorted by price)
- Test Connection
- Optional full exchange logging (off by default)

`GET /api/settings/openrouter/models` proxies the OpenRouter models catalog (text models), converts token prices to USD per million tokens, and returns them cheapest-first.

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

## Masking

`GET /api/settings` returns masked keys such as `sk-or-v1-••••••••••••1234` and never the full secret.

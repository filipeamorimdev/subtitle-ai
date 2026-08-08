# Configuration

Settings are stored in SQLite under `/config/subtitle-ai.db`. Secrets are Fernet-encrypted using `/config/secret.key`.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `SUBTITLE_AI_CONFIG_DIR` | `/config` | Persistent config directory |
| `SUBTITLE_AI_LOG_LEVEL` | `INFO` | Log level |
| `SUBTITLE_AI_HOST` / `PORT` | `0.0.0.0` / `6768` | Bind address (uvicorn CLI usually sets this) |
| `SUBTITLE_AI_FRONTEND_DIST` | auto-detected | Built Vue assets directory |

Prefer configuring Bazarr/OpenRouter credentials in the UI rather than env vars.

## Settings UI sections

### Bazarr

- URL
- API key (optional depending on Bazarr setup)
- Test Connection

### OpenRouter

- API key
- Model string (not hard-coded in logic)
- Test Connection

### Translation

- Target language code/name
- Source language preference list
- Batch size

### Media

- Media roots (default `/media`)
- Path mappings: `bazarr_prefix => local_prefix`

## Masking

`GET /api/settings` returns masked keys such as `sk-or-v1-••••••••••••1234` and never the full secret.

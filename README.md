# Subtitle AI

AI subtitle-translation fallback for [Bazarr](https://github.com/morpheus65535/bazarr).

When Bazarr cannot find a subtitle in your target language but a source subtitle (usually English) already exists — or can be requested / extracted — Subtitle AI translates that SRT with an LLM through [OpenRouter](https://openrouter.ai/), writes the result beside the media file, and asks Bazarr to rescan.

## Why it exists

Bazarr is excellent at finding existing subtitles. It is not a translator. Subtitle AI fills that gap for self-hosted libraries without replacing Bazarr.

## Workflow

1. Configure Bazarr, languages, batch size, automation, and path mappings under **Settings**. Media library mounts come from Docker volumes and are auto-discovered.
2. Optionally enable **Automatic Subtitle Fallback** under Settings (off by default). When enabled, Subtitle AI periodically scans Bazarr wanted items, waits a configurable grace period, then automatically request/extract/translate missing target subtitles. This can incur OpenRouter API costs.
3. Open **Settings → Providers** to set the OpenRouter API key (and test connection / refresh models). Then open **Settings → Models** for free/paid pools, routing strategy, per-job cost caps, a monthly budget, and batch size. Paid fallback stays off unless you enable it.
4. Open **Dashboard** for current activity, or **Media** to see titles that need work, are in progress, or already have history.
5. Click a title to open the media file page (languages, localize, history). Use **Request subtitles** / **Localize** to create a localization task; the planner chooses request, extract, or translate.
6. Use **Localize selected** or **Localize all missing** on the Media list when you want to queue several titles at once.
7. The worker runs jobs in the background: glossary prep → routed translation via the AI provider layer (OpenRouter in v0.3-alpha1, with technical model fallback) → structure validation → atomic write → Bazarr rescan → verify Bazarr no longer reports the target missing.
8. Track progress on the media file page and the **AI dashboard** (from Dashboard). Review suggested terms under **Settings → Glossary**.

When automatic fallback is **off**, nothing is scheduled — only clicks create jobs.

## Pages

| Page | Purpose |
| --- | --- |
| **Dashboard** | Command center: status, missing titles, AI snapshot, glossary counters; opens the AI dashboard |
| **Media** | Library / work queue: wanted titles plus anything with a localization task |
| **Media detail** | One file: languages, localize, source/tracks, request/extract/translate history |
| **Job detail** | Progress, action timeline, OpenRouter exchange log, Retry / Cancel / Retry Bazarr sync |
| **Usage stats** | Per-job token/cost breakdown (from `ai_usage_records` snapshots) |
| **AI dashboard** | Observability: Overview and Usage (opened from Dashboard) |
| **Settings** | General, Providers (Bazarr and AI), Models, Language, Glossary |

See [docs/localization-tasks.md](docs/localization-tasks.md) for the media-centric task architecture.

Semantic split:

- **Dashboard** → what is happening? (includes AI and glossary snapshots)
- **AI dashboard** → detailed AI cost/quality/routing (from Dashboard)
- **Settings** → how is Subtitle AI configured? (General, Providers, Models, Language, Glossary)

OpenRouter keys live under **Settings → Providers**. Model pools, routing, and budgets live under **Settings → Models**. Glossary edit/review lives under **Settings → Glossary**.

## Architecture

```text
Vue UI  →  FastAPI
        →  CandidateService (Bazarr wanted + disk SRT + embedded probe)
        →  AutomaticScanner (opt-in) → FallbackPlanner → JobService
        →  JobService / Worker
             · request  → Bazarr search (+ optional ffmpeg/PGS-OCR extract fallback)
             · extract  → ffmpeg text dump or Tesseract PGS OCR → sidecar SRT → Bazarr rescan
             · translate → ModelRouter → ProviderRegistry → TranslationService → validate → atomic write → rescan → verify
        →  GlossaryService (scopes, terms, suggested review)
        →  AI usage / budget / catalog cache
SQLite under /config
ffmpeg / ffprobe / tesseract in the Docker image
```

v0.3-alpha1 adds a provider-agnostic AI layer (`(provider_id, model_id)` identity). Only OpenRouter is implemented; see [docs/ai-providers.md](docs/ai-providers.md).

The application owns subtitle structure (IDs, timing, markup). The model only translates dialogue text. Glossaries keep character names and recurring terms consistent across a series or universe.

## Automatic Subtitle Fallback

Disabled by default. When enabled in Settings:

```text
Bazarr Wanted
      |
      v
Automatic Scanner
      |
      v
Grace Period
      |
      v
Source Resolver (external SRT / extract / Bazarr request)
      |
      v
Translate → Validate → Write → Bazarr Rescan → Verify
```

Automatic translation uses the same OpenRouter pipeline as manual jobs and **can incur API costs**. Use **Run automatic scan now** to trigger one scan immediately for testing.

## Filename convention

Target files are written as `{mediaStem}.{targetLang}.srt`. Source language tags are not stacked on the output (no `movie.en.pt-PT.srt`).

Example:

```text
/data/movies/Example Movie (2026)/Example Movie (2026).en.srt      (source, unchanged)
/data/movies/Example Movie (2026)/Example Movie (2026).pt-PT.srt   (translated)
```

## Requirements

- Docker (recommended), or Python 3.12+ and Node 20+ for local development
- A running Bazarr instance with API access
- An OpenRouter API key
- Media volumes mounted so Subtitle AI can read source / embedded tracks and write targets
- ffmpeg and Tesseract (included in the Docker image) for embedded text extract and PGS OCR

## Docker installation

```bash
cp .env.example .env
# Edit docker-compose.yml media volume to your library path
docker compose up -d --build
```

Open http://localhost:6768

### Portainer + GHCR (recommended)

Images are published to GitHub Container Registry on pushes to `main`:

```text
ghcr.io/filipeamorimdev/subtitle-ai:latest
```

1. Push to `main` (or run the **Publish Docker image to GHCR** workflow).
2. Under the repo **Packages**, open the image and set visibility to **Public** (or add `ghcr.io` in Portainer **Registries** with a PAT that has `read:packages`).
3. In Portainer, create/update a stack using [`docker-compose.portainer.yml`](docker-compose.portainer.yml) (Web editor or Git compose path).
4. Adjust host volume paths if needed, deploy, open port **6768**.
5. In the UI set Bazarr to `http://bazarr:6767`. Media roots are auto-discovered from the `/data/tv` and `/data/movies` volume mounts.

If Subtitle AI is in a **different stack** than Bazarr, attach it to the media stack network (see comments in the Portainer compose file).

### Volumes

| Mount | Purpose |
| --- | --- |
| `/config` | SQLite DB, encryption key, logs |
| `/media` or `/data/movies` + `/data/tv` | Media library (must match Bazarr paths) |

### Path mapping

Bazarr and Subtitle AI must agree on paths. If mounts already match (e.g. both use `/data/movies`), leave path mappings empty. Otherwise set a mapping in Settings:

```text
/movies => /media/movies
```

## Configuration

### Bazarr

- URL, e.g. `http://bazarr:6767`
- API key if required
- Use **Test Connection**

### OpenRouter

- API key (stored encrypted; UI shows a masked value after save) — configure under **Settings → Providers**
- Models, routing, cost/budget controls live under **Settings → Models** (`openrouter_model` is kept as a compatibility field)
- Use per-model **Test** on the AI page

### Translation

- Source language (default English / `en`)
- Target language (default Portuguese Portugal / `pt-PT`; also `pt-BR`, `es`, `fr`, `de`, `it`, …)
- Batch size (subtitle blocks per OpenRouter request; used by translation jobs)

### Media

- Media roots auto-discovered from Docker volume mounts under `/data` and `/media` (optional `SUBTITLE_AI_MEDIA_ROOTS` override)
- Path mappings in Settings (`bazarrPath => localPath`) when Bazarr and Subtitle AI mounts differ

### Automatic fallback

- Enable / disable (default off)
- Scan interval (minutes)
- Bazarr grace period (minutes) before acting on a newly missing item
- Automatic retry count for temporary failures
- Run automatic scan now

## Development

See [docs/development.md](docs/development.md).

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
SUBTITLE_AI_CONFIG_DIR=../config uvicorn app.main:app --reload --port 6768

# Frontend
cd frontend
npm install
npm run dev
```

```bash
# Full backend test suite
cd backend && pytest -q

# Frontend production build (Vue + TypeScript)
cd frontend && npx vue-tsc --noEmit && npm run build
```

## Upgrade / migration

Docker startup runs `init_db()`:

1. Creates any missing tables (`openrouter_model_preferences`, `ai_usage_records`, …).
2. Adds missing settings/jobs columns with safe defaults.
3. Seeds the legacy `openrouter_model` into a model preference if pools are empty (`:free` → `free_only`, otherwise `paid_only`). Paid fallback is never enabled by migration.

Existing jobs, settings, glossaries, and automatic-fallback state are preserved. Historical costs are not rewritten against the current OpenRouter catalogue.

Keep `/config` mounted across image upgrades. Recreate the container on the new image; do not copy the SQLite file between multiple running app processes.

## Security notes

- Designed for a trusted local network. No UI auth yet.
- API keys are never returned in full from the API.
- Paths are restricted to configured media roots.
- Do not expose this service to the public internet without additional hardening.

## Current limitations

- SRT output. Embedded **text** tracks extract with ffmpeg. Blu-ray **PGS** image tracks are OCR'd with Tesseract (best-effort). DVD VobSub is not OCR'd yet.
- OpenRouter only
- No Whisper / speech-to-text
- Bazarr rescan uses the same Scan Disk action as the Bazarr UI (`PATCH` `action=scan-disk`)

## Roadmap

- Stronger Bazarr subtitle registration via upload API
- ASS/SSA support
- UI authentication

## Docs

- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [AI model routing](docs/ai-model-routing.md)
- [Bazarr integration](docs/bazarr-integration.md)
- [Translation pipeline](docs/translation-pipeline.md)
- [Development](docs/development.md)

## License

MIT

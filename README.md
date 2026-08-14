# Subtitle AI

AI subtitle-translation fallback for [Bazarr](https://github.com/morpheus65535/bazarr).

When Bazarr cannot find a subtitle in your target language but a source subtitle (usually English) already exists — or can be requested / extracted — Subtitle AI translates that SRT with an LLM through [OpenRouter](https://openrouter.ai/), writes the result beside the media file, and asks Bazarr to rescan.

## Why it exists

Bazarr is excellent at finding existing subtitles. It is not a translator. Subtitle AI fills that gap for self-hosted libraries without replacing Bazarr.

## Workflow

1. Configure Bazarr, OpenRouter, languages, batch size, and path mappings under **Settings**. Media library mounts come from Docker volumes and are auto-discovered.
2. Optionally enable **Automatic Subtitle Fallback** under Settings (off by default). When enabled, Subtitle AI periodically scans Bazarr wanted items, waits a configurable grace period, then automatically request/extract/translate missing target subtitles. This can incur OpenRouter API costs.
3. Open **AI → Models** to configure free/paid OpenRouter pools, routing strategy, per-job cost caps, and a monthly budget. Paid fallback stays off unless you enable it.
4. Open **Home** (logo) for pipeline and job status, or **Candidates** and click **Refresh** (loads Bazarr wanted movies/episodes).
5. For each item, use the action that matches its state (manual workflow still works even when automatic fallback is enabled):
   - **Request EN** (or your source language) — ask Bazarr to search for a source SRT; if none is found and an embedded text track exists, Subtitle AI falls back to ffmpeg extract.
   - **Extract** — pull an embedded text subtitle track to a sidecar SRT via ffmpeg, then rescan Bazarr.
   - **Translate** — enqueue a translation job from the source SRT to your target language.
6. Use the batch toolbar (**Request all** / **Extract all** / **Translate all**) when you want to process the list in bulk.
7. The worker runs jobs in the background: glossary prep → routed OpenRouter translation (with technical model fallback) → structure validation → atomic write → Bazarr rescan → verify Bazarr no longer reports the target missing.
8. Track progress under **Jobs** and **AI** (overview, models, usage). Review suggested terms under **Glossaries**.

When automatic fallback is **off**, nothing is scheduled — only clicks create jobs.

## Pages

| Page | Purpose |
| --- | --- |
| **Home** | Ops dashboard: pipeline counts, job status, failed/running lists, system health |
| **Candidates** | Bazarr wanted list; Request / Extract / Translate (per-row and batch); embedded-track badges |
| **Jobs** | Job history for `translate`, `extract`, and `request`; status filters |
| **Job detail** | Progress, action timeline, OpenRouter exchange log, Retry / Cancel / Retry Bazarr sync |
| **Usage stats** | Token and cost breakdown by model and action |
| **AI** | Overview (cost, budget, ranking), Models (pools + routing + caps), Usage (paginated history) |
| **Glossaries** | Universe / series / movie term scopes; lock terms; review suggested terms |
| **Settings** | Bazarr, OpenRouter API key, languages, batch size, automatic fallback, path mappings |

## Architecture

```text
Vue UI  →  FastAPI
        →  CandidateService (Bazarr wanted + disk SRT + embedded probe)
        →  AutomaticScanner (opt-in) → FallbackPlanner → JobService
        →  JobService / Worker
             · request  → Bazarr search (+ optional ffmpeg extract fallback)
             · extract  → ffmpeg → sidecar SRT → Bazarr rescan
             · translate → ModelRouter → glossary prep → OpenRouter batches → validate → atomic write → rescan → verify
        →  GlossaryService (scopes, terms, suggested review)
        →  AI usage / budget / catalog cache
SQLite under /config
ffmpeg / ffprobe in the Docker image
```

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
- ffmpeg (included in the Docker image) for embedded extract

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

- API key (stored encrypted; UI shows a masked value after save)
- Models, routing, and cost controls under **AI → Models** (free/paid pools; `openrouter_model` is kept as a compatibility field)
- Use **Test Connection** on Settings or per-model Test on the AI page

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
cd backend && pytest
```

## Security notes

- Designed for a trusted local network. No UI auth yet.
- API keys are never returned in full from the API.
- Paths are restricted to configured media roots.
- Do not expose this service to the public internet without additional hardening.

## Current limitations

- SRT only (no ASS/VTT/PGS/OCR for image-based subs)
- OpenRouter only
- No Whisper / speech-to-text
- Bazarr rescan/verification is best-effort across versions

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

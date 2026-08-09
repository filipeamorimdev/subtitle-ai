# Subtitle AI

AI subtitle-translation fallback for [Bazarr](https://github.com/morpheus65535/bazarr).

When Bazarr cannot find a subtitle in your target language but a source subtitle (usually English) already exists, Subtitle AI can translate that SRT with an LLM through [OpenRouter](https://openrouter.ai/), write the result beside the media file, and ask Bazarr to rescan.

## Why it exists

Bazarr is excellent at finding existing subtitles. It is not a translator. Subtitle AI fills that gap for self-hosted libraries without replacing Bazarr.

## v0.1 workflow

1. Configure Bazarr, OpenRouter, target language, and media path mapping.
2. Open **Candidates** and click **Refresh** (queries Bazarr wanted movies/episodes).
3. Review the list. Rows with a usable source SRT show **Translate**.
4. Click **Translate** to create a job (nothing runs automatically).
5. The worker translates in batches, validates structure, writes `*.pt-PT.srt` (or your target), and asks Bazarr to rescan.
6. Track progress under **Jobs**.

Automatic enqueue/scheduling is intentionally deferred.

## Architecture

```text
Vue UI  →  FastAPI  →  CandidateService (Bazarr wanted)
                    →  JobService / Worker
                    →  SRT parse → OpenRouter batches → validate → atomic write
                    →  Bazarr rescan
SQLite under /config
```

The application owns subtitle structure (IDs, timing, markup). The model only translates dialogue text.

## Requirements

- Docker (recommended), or Python 3.12+ and Node 20+ for local development
- A running Bazarr instance with API access
- An OpenRouter API key
- Media volumes mounted so Subtitle AI can read source SRTs and write targets

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
5. In the UI set Bazarr to `http://bazarr:6767` and media roots to `/data/movies,/data/tv`.

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
- Model id, e.g. `openai/gpt-4o-mini` (any OpenRouter chat model)

### Translation

- Target language (default Portuguese Portugal / `pt-PT`)
- Preferred source languages (default `en`)

## Example

Input:

```text
/media/movies/Example Movie (2026)/Example Movie (2026).en.srt
```

After Translate to `pt-PT`:

```text
.../Example Movie (2026).en.srt      (unchanged)
.../Example Movie (2026).pt-PT.srt   (new)
```

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

- Designed for a trusted local network. No UI auth in v0.1.
- API keys are never returned in full from the API.
- Paths are restricted to configured media roots.
- Do not expose this service to the public internet without additional hardening.

## Current limitations

- SRT only (no ASS/VTT/PGS/OCR)
- OpenRouter only
- No Whisper / speech-to-text
- No automatic translation jobs (manual Translate only)
- Bazarr rescan is best-effort across versions

## Roadmap

- Optional automatic processing / scan interval
- Stronger Bazarr subtitle registration via upload API
- ASS/SSA support
- UI authentication

## License

MIT
# subtitle-ai
# subtitle-ai

# Development

## Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# Optional, for local PGS OCR: brew install tesseract tesseract-lang
mkdir -p ../config
SUBTITLE_AI_CONFIG_DIR=../config uvicorn app.main:app --reload --port 6768
```

Tests:

```bash
cd backend
pytest -q
```

Run the full suite (not only AI tests) before a release. Do not remove or weaken tests to make the suite green.

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:6768`.

Production build (TypeScript check + Vue compile):

```bash
cd frontend
npx vue-tsc --noEmit
npm run build
```

The Docker image runs `npm run build` and FastAPI serves `frontend/dist`.

## Audio separation smoke test

Inside the running container (Demucs is installed in the image, not on the host):

```bash
docker compose exec subtitle-ai python -m app.localization.audio \
  --input /data/movies/example.mkv \
  --duration 45 \
  --debug
```

Outputs `dialogue.wav` and `background.wav` plus a trace under `/config/debug/audio-separation/`. The default background-preserved dubbing mode uses the same service before it muxes the Portuguese audio.

## Docker

```bash
docker compose build
docker compose up
```

### Upgrade an existing install

1. Stop the container (keep the `/config` volume).
2. Pull/build the new image.
3. Start the same compose stack with the same `/config` mount.
4. On startup the app runs `init_db()` then **Alembic** (`alembic upgrade head`). Pre-Alembic databases are stamped at revision `0012` first, then upgraded.
5. Open Dashboard (Ops and AI tabs) and **Settings → Models**, and confirm the previous model is still selected.

Do not run multiple Subtitle AI containers against the same SQLite file.

Alembic revisions live under `backend/alembic/versions`. Runtime schema changes go through Alembic; `init_db()` remains a compatibility layer for very old installs.

## Project layout

See the repository root README and `docs/architecture.md`.

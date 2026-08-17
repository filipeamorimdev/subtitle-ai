# Development

## Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
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

## Docker

```bash
docker compose build
docker compose up
```

### Upgrade an existing install

1. Stop the container (keep the `/config` volume).
2. Pull/build the new image.
3. Start the same compose stack with the same `/config` mount.
4. On startup the app runs `init_db()`: missing tables/columns are added, and a lone legacy `openrouter_model` is seeded into the model pool without enabling paid fallback.
5. Open Dashboard, then **Open AI dashboard** and **Settings → Models**, and confirm the previous model is still selected.

Do not run multiple Subtitle AI containers against the same SQLite file.

Alembic revisions exist under `backend/alembic/versions` for schema history. Docker deployments rely on `init_db()` rather than `alembic upgrade` at runtime.

## Project layout

See the repository root README and `docs/architecture.md`.

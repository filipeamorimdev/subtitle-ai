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

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:6768`.

Production build is copied into the Docker image and served by FastAPI.

## Docker

```bash
docker compose build
docker compose up
```

## Project layout

See the repository root README and `docs/architecture.md`.

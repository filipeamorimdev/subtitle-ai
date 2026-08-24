# syntax=docker/dockerfile:1

FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SUBTITLE_AI_CONFIG_DIR=/config \
    SUBTITLE_AI_FRONTEND_DIST=/app/frontend/dist

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ffmpeg \
        libsndfile1 \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-por \
        tesseract-ocr-spa \
        tesseract-ocr-fra \
        tesseract-ocr-deu \
        tesseract-ocr-ita \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml /app/backend/pyproject.toml
COPY backend/app /app/backend/app
COPY backend/alembic.ini /app/backend/alembic.ini
COPY backend/alembic /app/backend/alembic
WORKDIR /app/backend
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        make \
        python3-dev \
    && pip install --no-cache-dir . \
    && pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir demucs \
    && apt-get purge -y gcc g++ make python3-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /root/.cache/pip

COPY --from=frontend-build /frontend/dist /app/frontend/dist

EXPOSE 6768
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:6768/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "6768"]

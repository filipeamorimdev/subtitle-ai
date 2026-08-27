"""Upgrade an existing v0.1 SQLite database to the current schema."""

from __future__ import annotations

import json
import sqlite3

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.config import get_app_config
from app.db.models import (
    AiModelPreferenceRow,
    AiProviderAccountRow,
    GlossaryEntryRow,
    JobRow,
    OpenRouterModelPreferenceRow,
    SettingsRow,
)
from app.db import get_db, init_db
from app.main import create_app
from app.services.model_preferences import seed_legacy_model_preference


V01_SCHEMA = """
CREATE TABLE settings (
    id INTEGER PRIMARY KEY,
    bazarr_url VARCHAR(512),
    bazarr_api_key_encrypted TEXT,
    openrouter_api_key_encrypted TEXT,
    openrouter_model VARCHAR(256) NOT NULL,
    target_language_code VARCHAR(32) NOT NULL,
    target_language_name VARCHAR(128) NOT NULL,
    source_languages JSON NOT NULL,
    media_roots JSON NOT NULL,
    path_mappings JSON NOT NULL,
    batch_size INTEGER NOT NULL,
    updated_at DATETIME,
    max_concurrent_translate INTEGER NOT NULL DEFAULT 1,
    max_concurrent_extract INTEGER NOT NULL DEFAULT 1,
    max_concurrent_request INTEGER NOT NULL DEFAULT 1,
    automatic_fallback_enabled BOOLEAN NOT NULL DEFAULT 0,
    automatic_scan_interval_minutes INTEGER NOT NULL DEFAULT 5,
    bazarr_grace_period_minutes INTEGER NOT NULL DEFAULT 10,
    automatic_retry_enabled BOOLEAN NOT NULL DEFAULT 1,
    maximum_automatic_retries INTEGER NOT NULL DEFAULT 3,
    openrouter_log_full_exchanges BOOLEAN NOT NULL DEFAULT 0
);
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_key VARCHAR(512),
    media_type VARCHAR(32) NOT NULL,
    media_path VARCHAR(1024) NOT NULL,
    media_title VARCHAR(512),
    bazarr_movie_id INTEGER,
    bazarr_episode_id INTEGER,
    bazarr_series_id INTEGER,
    source_subtitle_path VARCHAR(1024) NOT NULL,
    target_subtitle_path VARCHAR(1024) NOT NULL,
    source_language VARCHAR(32) NOT NULL,
    target_language VARCHAR(32) NOT NULL,
    model VARCHAR(256) NOT NULL,
    status VARCHAR(32) NOT NULL,
    progress FLOAT NOT NULL,
    progress_detail VARCHAR(256),
    error TEXT,
    warning TEXT,
    reason_code VARCHAR(64),
    dedupe_key VARCHAR(128),
    source_hash VARCHAR(64),
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    created_at DATETIME,
    started_at DATETIME,
    completed_at DATETIME,
    job_kind VARCHAR(32) NOT NULL DEFAULT 'translate',
    extract_stream_index INTEGER,
    trigger_type VARCHAR(16) NOT NULL DEFAULT 'manual'
);
CREATE TABLE translation_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_hash VARCHAR(64) NOT NULL,
    target_language VARCHAR(32) NOT NULL,
    model VARCHAR(256) NOT NULL,
    target_subtitle_path VARCHAR(1024) NOT NULL,
    job_id INTEGER,
    created_at DATETIME
);
CREATE TABLE glossary_scopes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind VARCHAR(32) NOT NULL,
    key VARCHAR(256) NOT NULL,
    display_name VARCHAR(512) NOT NULL,
    target_language VARCHAR(32) NOT NULL,
    parent_scope_id INTEGER,
    bazarr_series_id INTEGER,
    bazarr_movie_id INTEGER,
    created_at DATETIME,
    updated_at DATETIME
);
CREATE TABLE glossary_terms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_id INTEGER NOT NULL,
    source VARCHAR(512) NOT NULL,
    source_normalized VARCHAR(512) NOT NULL,
    target VARCHAR(512) NOT NULL,
    term_type VARCHAR(32) NOT NULL DEFAULT 'other',
    policy VARCHAR(32) NOT NULL DEFAULT 'keep',
    status VARCHAR(32) NOT NULL DEFAULT 'suggested',
    locked BOOLEAN NOT NULL DEFAULT 0,
    source_origin VARCHAR(32) NOT NULL DEFAULT 'llm',
    notes TEXT,
    created_at DATETIME,
    updated_at DATETIME
);
CREATE TABLE observed_candidates (
    candidate_key VARCHAR(512) PRIMARY KEY,
    media_type VARCHAR(32) NOT NULL,
    media_path VARCHAR(1024) NOT NULL,
    media_title VARCHAR(512),
    target_language VARCHAR(32) NOT NULL,
    bazarr_movie_id INTEGER,
    bazarr_episode_id INTEGER,
    bazarr_series_id INTEGER,
    first_seen_at DATETIME,
    last_seen_at DATETIME,
    last_automatic_attempt_at DATETIME,
    automatic_attempts INTEGER NOT NULL DEFAULT 0,
    last_outcome VARCHAR(64),
    last_reason_code VARCHAR(64),
    currently_wanted BOOLEAN NOT NULL DEFAULT 1
);
"""


def _write_v01_database(path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(V01_SCHEMA)
        conn.execute(
            """
            INSERT INTO settings (
                id, bazarr_url, openrouter_model, target_language_code, target_language_name,
                source_languages, media_roots, path_mappings, batch_size,
                automatic_fallback_enabled, automatic_scan_interval_minutes,
                bazarr_grace_period_minutes, automatic_retry_enabled,
                maximum_automatic_retries, openrouter_log_full_exchanges
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "http://bazarr:6767",
                "openai/gpt-4o-mini",
                "pt-PT",
                "Portuguese (Portugal)",
                json.dumps(["en"]),
                json.dumps(["/data/movies"]),
                json.dumps([{"bazarr_prefix": "/movies", "local_prefix": "/data/movies"}]),
                25,
                0,
                5,
                10,
                1,
                3,
                0,
            ),
        )
        conn.execute(
            """
            INSERT INTO jobs (
                candidate_key, media_type, media_path, media_title, bazarr_movie_id,
                source_subtitle_path, target_subtitle_path, source_language, target_language,
                model, status, progress, input_tokens, output_tokens, total_tokens,
                job_kind, trigger_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "movie:10:pt-PT",
                "movie",
                "/data/movies/Example.mkv",
                "Example Movie",
                10,
                "/data/movies/Example.en.srt",
                "/data/movies/Example.pt-PT.srt",
                "en",
                "pt-PT",
                "openai/gpt-4o-mini",
                "completed",
                100.0,
                1200,
                800,
                2000,
                "translate",
                "manual",
            ),
        )
        conn.execute(
            """
            INSERT INTO observed_candidates (
                candidate_key, media_type, media_path, media_title, target_language,
                bazarr_movie_id, currently_wanted, automatic_attempts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "movie:10:pt-PT",
                "movie",
                "/data/movies/Example.mkv",
                "Example Movie",
                "pt-PT",
                10,
                1,
                0,
            ),
        )
        conn.execute(
            """
            INSERT INTO glossary_scopes (kind, key, display_name, target_language)
            VALUES (?, ?, ?, ?)
            """,
            ("movie", "movie:10", "Example Movie", "pt-PT"),
        )
        scope_id = conn.execute("SELECT id FROM glossary_scopes WHERE key = ?", ("movie:10",)).fetchone()[0]
        conn.execute(
            """
            INSERT INTO glossary_terms (
                scope_id, source, source_normalized, target, locked
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (scope_id, "Neo", "neo", "Neo", 1),
        )
        conn.commit()
    finally:
        conn.close()


def test_v01_database_upgrades_safely(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    db_path = config_dir / "subtitle-ai.db"
    _write_v01_database(db_path)

    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SUBTITLE_AI_MEDIA_ROOTS", str(tmp_path / "media"))
    get_app_config.cache_clear()

    import app.db as db_module

    db_module._engine = None
    db_module._SessionLocal = None

    async def noop_start():
        return None

    async def noop_stop():
        return None

    monkeypatch.setattr("app.jobs.worker.worker.start", noop_start)
    monkeypatch.setattr("app.jobs.worker.worker.stop", noop_stop)
    monkeypatch.setattr("app.jobs.scanner.scanner.start", noop_start)
    monkeypatch.setattr("app.jobs.scanner.scanner.stop", noop_stop)

    from app.translation.openrouter.client import OpenRouterError

    async def no_catalog(*, api_key=None, base_url=None, timeout=60.0, usage_hook=None):  # noqa: ARG001
        raise OpenRouterError("catalog skipped in upgrade test")

    monkeypatch.setattr(
        "app.translation.openrouter.client.OpenRouterClient.list_models",
        no_catalog,
    )

    init_db()

    # ``init_db`` must preserve legacy glossary rows until Alembic performs
    # the data migration during startup.
    session = db_module.get_session_factory()()
    try:
        assert session.execute(select(GlossaryEntryRow)).all() == []
        assert session.execute(text("SELECT count(*) FROM glossary_terms")).scalar_one() == 1
    finally:
        session.close()

    session = db_module.get_session_factory()()
    try:
        settings = session.get(SettingsRow, 1)
        assert settings is not None
        assert settings.openrouter_model == "openai/gpt-4o-mini"
        assert settings.bazarr_url == "http://bazarr:6767"
        assert not settings.automatic_fallback_enabled
        assert settings.batch_size == 25
        assert settings.path_mappings == [
            {"bazarr_prefix": "/movies", "local_prefix": "/data/movies"}
        ]
        assert not settings.allow_paid_fallback
        assert not settings.allow_unknown_pricing
        assert not settings.monthly_budget_enabled
        assert not settings.openrouter_log_full_exchanges
        assert settings.openrouter_temperature == 0
        assert settings.routing_strategy == "paid_only"

        prefs = list(session.scalars(select(OpenRouterModelPreferenceRow)).all())
        assert len(prefs) == 1
        assert prefs[0].model_id == "openai/gpt-4o-mini"
        assert prefs[0].tier == "paid"
        assert prefs[0].enabled is True

        jobs = list(session.scalars(select(JobRow)).all())
        assert len(jobs) == 1
        assert jobs[0].media_title == "Example Movie"
        assert jobs[0].status == "completed"
        assert jobs[0].total_tokens == 2000
        assert jobs[0].provider_id == "openrouter"

        ai_prefs = list(session.scalars(select(AiModelPreferenceRow)).all())
        assert len(ai_prefs) == 1
        assert ai_prefs[0].provider_id == "openrouter"
        assert ai_prefs[0].model_id == "openai/gpt-4o-mini"

        # Account row is always seeded for the Providers UI (may have no key yet).
        accounts = list(session.scalars(select(AiProviderAccountRow)).all())
        assert len(accounts) == 1
        assert accounts[0].provider_id == "openrouter"
        assert accounts[0].api_key_encrypted is None

        assert seed_legacy_model_preference(session) is None
    finally:
        session.close()

    app = create_app()

    def override_db():
        db = db_module.get_session_factory()()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["version"] == "0.3.0a2"

        settings = client.get("/api/settings")
        assert settings.status_code == 200
        body = settings.json()
        assert body["openrouter_model"] == "openai/gpt-4o-mini"
        assert body["allow_paid_fallback"] is False
        assert body["automatic_fallback_enabled"] is False
        assert body["openrouter_log_full_exchanges"] is False
        assert body["openrouter_temperature"] == 0
        assert body["path_mappings"] == [
            {"bazarr_prefix": "/movies", "local_prefix": "/data/movies"}
        ]

        jobs = client.get("/api/jobs")
        assert jobs.status_code == 200
        assert len(jobs.json()) == 1
        assert jobs.json()[0]["media_title"] == "Example Movie"

        overview = client.get("/api/ai/overview")
        assert overview.status_code == 200
        assert overview.json()["empty"] is True

        models = client.get("/api/ai/models")
        assert models.status_code == 200
        payload = models.json()
        assert payload["routing"]["allow_paid_fallback"] is False
        assert payload["routing"]["openrouter_log_full_exchanges"] is False
        assert any(p["model_id"] == "openai/gpt-4o-mini" for p in payload["preferences"])
        assert any(p.get("provider_id") == "openrouter" for p in payload["preferences"])

        providers = client.get("/api/ai/providers")
        assert providers.status_code == 200
        assert any(p["provider_id"] == "openrouter" for p in providers.json()["providers"])

        usage = client.get("/api/ai/usage")
        assert usage.status_code == 200
        assert usage.json()["total"] == 0

        job_usage = client.get("/api/jobs/1/usage")
        assert job_usage.status_code == 200
        # Legacy job has token totals but no usage records / live reprice.
        assert job_usage.json()["totals"]["total_tokens"] == 2000
        assert job_usage.json()["totals"]["cost_usd"] is None

    session = db_module.get_session_factory()()
    try:
        glossary = list(session.scalars(select(GlossaryEntryRow)).all())
        assert [(row.scope_key, row.target_language, row.source, row.target) for row in glossary] == [
            ("movie:10", "pt-PT", "Neo", "Neo")
        ]
    finally:
        session.close()

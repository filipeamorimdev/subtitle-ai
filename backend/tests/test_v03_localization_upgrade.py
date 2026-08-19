"""Upgrade a v0.3-alpha1 SQLite database with localization-task tables."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from app.core.config import get_app_config
from app.db import init_db
from app.main import create_app


V03_ALPHA1_SCHEMA = """
CREATE TABLE settings (
    id INTEGER PRIMARY KEY,
    bazarr_url VARCHAR(512),
    bazarr_api_key_encrypted TEXT,
    openrouter_api_key_encrypted TEXT,
    openrouter_model VARCHAR(256) NOT NULL DEFAULT 'openai/gpt-4o-mini',
    target_language_code VARCHAR(32) NOT NULL DEFAULT 'pt-PT',
    target_language_name VARCHAR(128) NOT NULL DEFAULT 'Portuguese (Portugal)',
    source_languages JSON NOT NULL,
    media_roots JSON NOT NULL,
    path_mappings JSON NOT NULL,
    batch_size INTEGER NOT NULL DEFAULT 25,
    max_concurrent_translate INTEGER NOT NULL DEFAULT 1,
    max_concurrent_extract INTEGER NOT NULL DEFAULT 1,
    max_concurrent_request INTEGER NOT NULL DEFAULT 1,
    automatic_fallback_enabled BOOLEAN NOT NULL DEFAULT 0,
    automatic_scan_interval_minutes INTEGER NOT NULL DEFAULT 5,
    bazarr_grace_period_minutes INTEGER NOT NULL DEFAULT 10,
    automatic_retry_enabled BOOLEAN NOT NULL DEFAULT 1,
    maximum_automatic_retries INTEGER NOT NULL DEFAULT 3,
    openrouter_log_full_exchanges BOOLEAN NOT NULL DEFAULT 0,
    routing_strategy VARCHAR(32) NOT NULL DEFAULT 'free_first',
    allow_paid_fallback BOOLEAN NOT NULL DEFAULT 0,
    allow_free_fallback BOOLEAN NOT NULL DEFAULT 1,
    allow_unknown_pricing BOOLEAN NOT NULL DEFAULT 0,
    maximum_cost_per_job_micro_usd INTEGER,
    monthly_budget_enabled BOOLEAN NOT NULL DEFAULT 0,
    monthly_budget_amount_micro_usd INTEGER,
    allow_manual_budget_override BOOLEAN NOT NULL DEFAULT 0,
    updated_at DATETIME
);
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_key VARCHAR(512),
    job_kind VARCHAR(32) NOT NULL DEFAULT 'translate',
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
    provider_id VARCHAR(64) NOT NULL DEFAULT 'openrouter',
    model VARCHAR(256) NOT NULL,
    status VARCHAR(32) NOT NULL,
    progress FLOAT NOT NULL DEFAULT 0,
    progress_detail VARCHAR(256),
    error TEXT,
    warning TEXT,
    reason_code VARCHAR(64),
    extract_stream_index INTEGER,
    trigger_type VARCHAR(16) NOT NULL DEFAULT 'manual',
    dedupe_key VARCHAR(128),
    source_hash VARCHAR(64),
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    created_at DATETIME,
    started_at DATETIME,
    completed_at DATETIME
);
CREATE TABLE translation_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_hash VARCHAR(64) NOT NULL,
    target_language VARCHAR(32) NOT NULL,
    provider_id VARCHAR(64) NOT NULL DEFAULT 'openrouter',
    model VARCHAR(256) NOT NULL,
    target_subtitle_path VARCHAR(1024) NOT NULL,
    job_id INTEGER,
    created_at DATETIME
);
"""


def test_v03_alpha1_upgrade_adds_localization_tables(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    db_path = config_dir / "subtitle-ai.db"

    conn = sqlite3.connect(db_path)
    conn.executescript(V03_ALPHA1_SCHEMA)
    conn.execute(
        "INSERT INTO settings (id, source_languages, media_roots, path_mappings) VALUES (1, ?, ?, ?)",
        ('["en"]', '["/media"]', "[]"),
    )
    conn.execute(
        """
        INSERT INTO jobs (
            media_type, media_path, source_subtitle_path, target_subtitle_path,
            source_language, target_language, model, status, progress
        ) VALUES ('movie', '/media/a.mkv', '/media/a.en.srt', '/media/a.pt-PT.srt',
                  'en', 'pt-PT', 'test', 'completed', 100)
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("SUBTITLE_AI_CONFIG_DIR", str(config_dir))
    get_app_config.cache_clear()

    import app.db as db_module

    db_module._engine = None
    db_module._SessionLocal = None

    init_db()

    conn = sqlite3.connect(db_path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "media_items" in tables
    assert "localization_tasks" in tables

    job_cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    assert "task_id" in job_cols

    # Legacy job still present and readable without task_id.
    row = conn.execute("SELECT id, task_id, status FROM jobs").fetchone()
    assert row[0] == 1
    assert row[1] is None
    assert row[2] == "completed"

    indexes = {
        row[1] for row in conn.execute("PRAGMA index_list(localization_tasks)").fetchall()
    }
    assert "uq_localization_tasks_active" in indexes
    conn.close()

    client = TestClient(create_app())
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["version"] == "0.3.0a2"

    jobs = client.get("/api/jobs")
    assert jobs.status_code == 200
    assert any(j["id"] == 1 and j.get("task_id") is None for j in jobs.json())

    langs = client.get("/api/languages")
    assert langs.status_code == 200
    assert any(lang["code"] == "pt-PT" for lang in langs.json())

    get_app_config.cache_clear()

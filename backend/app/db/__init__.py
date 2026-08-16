"""Database session and engine."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_app_config


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        config = get_app_config()
        config.ensure_directories()
        _engine = create_engine(
            config.database_url,
            connect_args={"check_same_thread": False},
            future=True,
        )

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ARG001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    # Import models so metadata is registered.
    from app.db import models  # noqa: F401
    from sqlalchemy import text

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    # Lightweight SQLite column ensure for existing deployments without alembic upgrade.
    with engine.begin() as conn:
        job_rows = conn.execute(text("PRAGMA table_info(jobs)")).fetchall()
        job_columns = {row[1] for row in job_rows}
        if "job_kind" not in job_columns:
            conn.execute(
                text("ALTER TABLE jobs ADD COLUMN job_kind VARCHAR(32) NOT NULL DEFAULT 'translate'")
            )
        if "extract_stream_index" not in job_columns:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN extract_stream_index INTEGER"))
        if "trigger_type" not in job_columns:
            conn.execute(
                text("ALTER TABLE jobs ADD COLUMN trigger_type VARCHAR(16) NOT NULL DEFAULT 'manual'")
            )
        if "provider_id" not in job_columns:
            conn.execute(
                text(
                    "ALTER TABLE jobs ADD COLUMN provider_id VARCHAR(64) NOT NULL DEFAULT 'openrouter'"
                )
            )
        if "task_id" not in job_columns:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN task_id INTEGER"))

        # Active-task uniqueness (partial unique index) for deployments using create_all.
        task_indexes = {
            row[1]
            for row in conn.execute(text("PRAGMA index_list(localization_tasks)")).fetchall()
        }
        if "localization_tasks" in {
            r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
        } and "uq_localization_tasks_active" not in task_indexes:
            conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX uq_localization_tasks_active
                    ON localization_tasks (media_item_id, target_language_code, capability)
                    WHERE status IN (
                        'requested', 'planning', 'waiting_for_source', 'processing', 'verifying'
                    )
                    """
                )
            )

        settings_rows = conn.execute(text("PRAGMA table_info(settings)")).fetchall()
        settings_columns = {row[1] for row in settings_rows}
        for column, ddl in (
            ("max_concurrent_translate", "ALTER TABLE settings ADD COLUMN max_concurrent_translate INTEGER NOT NULL DEFAULT 1"),
            ("max_concurrent_extract", "ALTER TABLE settings ADD COLUMN max_concurrent_extract INTEGER NOT NULL DEFAULT 1"),
            ("max_concurrent_request", "ALTER TABLE settings ADD COLUMN max_concurrent_request INTEGER NOT NULL DEFAULT 1"),
            ("automatic_fallback_enabled", "ALTER TABLE settings ADD COLUMN automatic_fallback_enabled BOOLEAN NOT NULL DEFAULT 0"),
            ("automatic_scan_interval_minutes", "ALTER TABLE settings ADD COLUMN automatic_scan_interval_minutes INTEGER NOT NULL DEFAULT 5"),
            ("bazarr_grace_period_minutes", "ALTER TABLE settings ADD COLUMN bazarr_grace_period_minutes INTEGER NOT NULL DEFAULT 10"),
            ("automatic_retry_enabled", "ALTER TABLE settings ADD COLUMN automatic_retry_enabled BOOLEAN NOT NULL DEFAULT 1"),
            ("maximum_automatic_retries", "ALTER TABLE settings ADD COLUMN maximum_automatic_retries INTEGER NOT NULL DEFAULT 3"),
            ("openrouter_log_full_exchanges", "ALTER TABLE settings ADD COLUMN openrouter_log_full_exchanges BOOLEAN NOT NULL DEFAULT 0"),
            ("routing_strategy", "ALTER TABLE settings ADD COLUMN routing_strategy VARCHAR(32) NOT NULL DEFAULT 'free_first'"),
            ("allow_paid_fallback", "ALTER TABLE settings ADD COLUMN allow_paid_fallback BOOLEAN NOT NULL DEFAULT 0"),
            ("allow_free_fallback", "ALTER TABLE settings ADD COLUMN allow_free_fallback BOOLEAN NOT NULL DEFAULT 1"),
            ("allow_unknown_pricing", "ALTER TABLE settings ADD COLUMN allow_unknown_pricing BOOLEAN NOT NULL DEFAULT 0"),
            ("maximum_cost_per_job_micro_usd", "ALTER TABLE settings ADD COLUMN maximum_cost_per_job_micro_usd INTEGER"),
            ("monthly_budget_enabled", "ALTER TABLE settings ADD COLUMN monthly_budget_enabled BOOLEAN NOT NULL DEFAULT 0"),
            ("monthly_budget_amount_micro_usd", "ALTER TABLE settings ADD COLUMN monthly_budget_amount_micro_usd INTEGER"),
            ("allow_manual_budget_override", "ALTER TABLE settings ADD COLUMN allow_manual_budget_override BOOLEAN NOT NULL DEFAULT 0"),
        ):
            if column not in settings_columns:
                conn.execute(text(ddl))

        # Provider-aware columns on existing AI / cache tables.
        usage_cols = {
            row[1] for row in conn.execute(text("PRAGMA table_info(ai_usage_records)")).fetchall()
        }
        if usage_cols:
            for column, ddl in (
                (
                    "provider_id",
                    "ALTER TABLE ai_usage_records ADD COLUMN provider_id VARCHAR(64) NOT NULL DEFAULT 'openrouter'",
                ),
                ("request_id", "ALTER TABLE ai_usage_records ADD COLUMN request_id VARCHAR(64)"),
                (
                    "attempt_number",
                    "ALTER TABLE ai_usage_records ADD COLUMN attempt_number INTEGER",
                ),
                ("cost_source", "ALTER TABLE ai_usage_records ADD COLUMN cost_source VARCHAR(32)"),
            ):
                if column not in usage_cols:
                    conn.execute(text(ddl))

        routing_cols = {
            row[1] for row in conn.execute(text("PRAGMA table_info(ai_routing_events)")).fetchall()
        }
        if routing_cols:
            for column, ddl in (
                ("provider_id", "ALTER TABLE ai_routing_events ADD COLUMN provider_id VARCHAR(64)"),
                (
                    "next_provider_id",
                    "ALTER TABLE ai_routing_events ADD COLUMN next_provider_id VARCHAR(64)",
                ),
            ):
                if column not in routing_cols:
                    conn.execute(text(ddl))

        cache_cols = {
            row[1] for row in conn.execute(text("PRAGMA table_info(translation_cache)")).fetchall()
        }
        if cache_cols and "provider_id" not in cache_cols:
            conn.execute(
                text(
                    "ALTER TABLE translation_cache ADD COLUMN provider_id VARCHAR(64) NOT NULL DEFAULT 'openrouter'"
                )
            )

    # Seed legacy openrouter_model into preferences if pools are empty,
    # then migrate into provider-aware tables.
    from app.ai.migration import migrate_legacy_openrouter
    from app.services.model_preferences import seed_legacy_model_preference

    session = get_session_factory()()
    try:
        seed_legacy_model_preference(session)
        session.flush()
        migrate_legacy_openrouter(session)
        session.commit()
    except Exception:  # noqa: BLE001
        session.rollback()
        raise
    finally:
        session.close()

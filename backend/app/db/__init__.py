"""Database session and engine."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

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
        # SQLite cannot share a small QueuePool across FastAPI threads, the
        # job worker, and long Bazarr/extract awaits. NullPool checks out a
        # fresh connection per session and returns it on close.
        _engine = create_engine(
            config.database_url,
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=NullPool,
            future=True,
        )

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ARG001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
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


def release_session_connection(session: Session) -> None:
    """End the current transaction so SQLite is not pinned across awaits.

    WAL checkpoints cannot finish while any session holds a transaction.
    Copy ORM attribute values needed during I/O *before* calling this —
    accessing expired attributes afterwards starts a new transaction.
    """
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise


def _ensure_jobs_provider_id_nullable(conn) -> None:
    """SQLite cannot ALTER COLUMN; rebuild jobs if provider_id is still NOT NULL."""
    from sqlalchemy import text

    info = conn.execute(text("PRAGMA table_info(jobs)")).fetchall()
    if not info:
        return
    provider = next((row for row in info if row[1] == "provider_id"), None)
    if provider is None or int(provider[3] or 0) == 0:
        return

    col_sql: list[str] = []
    names: list[str] = []
    for _cid, name, ctype, notnull, dflt, pk in info:
        names.append(name)
        sql_type = ctype or "TEXT"
        parts = [f'"{name}" {sql_type}']
        if pk:
            parts.append("PRIMARY KEY")
            if name == "id" and sql_type.upper().startswith("INT"):
                parts.append("AUTOINCREMENT")
        elif name != "provider_id":
            if notnull:
                parts.append("NOT NULL")
            if dflt is not None:
                parts.append(f"DEFAULT {dflt}")
        col_sql.append(" ".join(parts))

    indexes = list(
        conn.execute(
            text(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='index' AND tbl_name='jobs' AND sql IS NOT NULL"
            )
        ).fetchall()
    )
    conn.execute(text("PRAGMA foreign_keys=OFF"))
    conn.execute(text(f"CREATE TABLE jobs__p9 ({', '.join(col_sql)})"))
    quoted = ", ".join(f'"{n}"' for n in names)
    conn.execute(text(f"INSERT INTO jobs__p9 ({quoted}) SELECT {quoted} FROM jobs"))
    conn.execute(text("DROP TABLE jobs"))
    conn.execute(text("ALTER TABLE jobs__p9 RENAME TO jobs"))
    for _name, sql in indexes:
        if sql:
            conn.execute(text(sql))
    conn.execute(text("PRAGMA foreign_keys=ON"))


def _ensure_ai_model_preferences_purpose(conn) -> None:
    """Upgrade the preference uniqueness rule for the audio-analysis pool.

    ``create_all`` cannot alter a SQLite unique constraint. Existing installs
    therefore need a small table rebuild so the same multimodal model may be
    selected for translation and audio analysis independently.
    """
    from sqlalchemy import text

    tables = {
        row[0]
        for row in conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    }
    if "ai_model_preferences" not in tables:
        return

    info = conn.execute(text("PRAGMA table_info(ai_model_preferences)")).fetchall()
    columns = {row[1] for row in info}
    target_unique = False
    for index in conn.execute(text("PRAGMA index_list(ai_model_preferences)")).fetchall():
        if not int(index[2] or 0):
            continue
        index_name = str(index[1]).replace("'", "''")
        indexed_columns = [
            row[2]
            for row in conn.execute(text(f"PRAGMA index_info('{index_name}')")).fetchall()
        ]
        if indexed_columns == ["provider_id", "model_id", "purpose"]:
            target_unique = True
            break
    if "purpose" in columns and target_unique:
        return

    purpose_value = 'COALESCE("purpose", \'translation\')' if "purpose" in columns else "'translation'"
    conn.execute(text("PRAGMA foreign_keys=OFF"))
    conn.execute(
        text(
            """
            CREATE TABLE ai_model_preferences__p18 (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                provider_id VARCHAR(64) NOT NULL,
                model_id VARCHAR(256) NOT NULL,
                tier VARCHAR(16) NOT NULL,
                purpose VARCHAR(32) NOT NULL DEFAULT 'translation',
                priority INTEGER NOT NULL DEFAULT 1,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_ai_model_preferences_provider_model_purpose
                    UNIQUE (provider_id, model_id, purpose)
            )
            """
        )
    )
    conn.execute(
        text(
            f"""
            INSERT INTO ai_model_preferences__p18
                (id, provider_id, model_id, tier, purpose, priority, enabled, created_at, updated_at)
            SELECT id, provider_id, model_id, tier, {purpose_value}, priority, enabled,
                   created_at, updated_at
            FROM ai_model_preferences
            """
        )
    )
    conn.execute(text("DROP TABLE ai_model_preferences"))
    conn.execute(text("ALTER TABLE ai_model_preferences__p18 RENAME TO ai_model_preferences"))
    for name, columns_sql in (
        ("ix_ai_model_preferences_provider_id", "provider_id"),
        ("ix_ai_model_preferences_model_id", "model_id"),
        ("ix_ai_model_preferences_tier", "tier"),
        ("ix_ai_model_preferences_purpose", "purpose"),
    ):
        conn.execute(text(f"CREATE INDEX {name} ON ai_model_preferences ({columns_sql})"))
    conn.execute(text("PRAGMA foreign_keys=ON"))


def init_db() -> None:
    # Import models so metadata is registered.
    from app.db import models  # noqa: F401
    from sqlalchemy import text

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    # Lightweight SQLite column ensure for existing deployments without alembic upgrade.
    with engine.begin() as conn:
        _ensure_ai_model_preferences_purpose(conn)
        # Keep legacy glossary tables until Alembic has copied their data into
        # ``glossary_entries`` (revision 0016).  Dropping them here used to
        # erase user-maintained terms before the migration could preserve them.

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
            conn.execute(text("ALTER TABLE jobs ADD COLUMN provider_id VARCHAR(64)"))
        _ensure_jobs_provider_id_nullable(conn)
        if "task_id" not in job_columns:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN task_id INTEGER"))
        if "dub_mix_mode" not in job_columns:
            conn.execute(
                text(
                    "ALTER TABLE jobs ADD COLUMN dub_mix_mode VARCHAR(32) "
                    "NOT NULL DEFAULT 'background_preserved'"
                )
            )
        if "dub_speaker_voices" not in job_columns:
            conn.execute(
                text("ALTER TABLE jobs ADD COLUMN dub_speaker_voices JSON NOT NULL DEFAULT '{}'"))

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
                        'requested', 'planning', 'waiting_for_source', 'processing',
                        'verifying', 'awaiting_approval'
                    )
                    """
                )
            )

        settings_rows = conn.execute(text("PRAGMA table_info(settings)")).fetchall()
        settings_columns = {row[1] for row in settings_rows}
        for column, ddl in (
            ("jellyfin_url", "ALTER TABLE settings ADD COLUMN jellyfin_url VARCHAR(512)"),
            ("jellyfin_api_key_encrypted", "ALTER TABLE settings ADD COLUMN jellyfin_api_key_encrypted TEXT"),
            ("max_concurrent_translate", "ALTER TABLE settings ADD COLUMN max_concurrent_translate INTEGER NOT NULL DEFAULT 1"),
            ("max_concurrent_extract", "ALTER TABLE settings ADD COLUMN max_concurrent_extract INTEGER NOT NULL DEFAULT 1"),
            ("max_concurrent_request", "ALTER TABLE settings ADD COLUMN max_concurrent_request INTEGER NOT NULL DEFAULT 1"),
            ("max_concurrent_transcribe", "ALTER TABLE settings ADD COLUMN max_concurrent_transcribe INTEGER NOT NULL DEFAULT 1"),
            ("max_concurrent_dub", "ALTER TABLE settings ADD COLUMN max_concurrent_dub INTEGER NOT NULL DEFAULT 1"),
            ("asr_provider", "ALTER TABLE settings ADD COLUMN asr_provider VARCHAR(32) NOT NULL DEFAULT 'local_then_openai'"),
            ("asr_local_model", "ALTER TABLE settings ADD COLUMN asr_local_model VARCHAR(32) NOT NULL DEFAULT 'small'"),
            ("openai_api_key_encrypted", "ALTER TABLE settings ADD COLUMN openai_api_key_encrypted TEXT"),
            ("automatic_fallback_enabled", "ALTER TABLE settings ADD COLUMN automatic_fallback_enabled BOOLEAN NOT NULL DEFAULT 0"),
            ("automatic_scan_interval_minutes", "ALTER TABLE settings ADD COLUMN automatic_scan_interval_minutes INTEGER NOT NULL DEFAULT 5"),
            ("bazarr_grace_period_minutes", "ALTER TABLE settings ADD COLUMN bazarr_grace_period_minutes INTEGER NOT NULL DEFAULT 10"),
            ("automatic_retry_enabled", "ALTER TABLE settings ADD COLUMN automatic_retry_enabled BOOLEAN NOT NULL DEFAULT 1"),
            ("maximum_automatic_retries", "ALTER TABLE settings ADD COLUMN maximum_automatic_retries INTEGER NOT NULL DEFAULT 3"),
            ("openrouter_log_full_exchanges", "ALTER TABLE settings ADD COLUMN openrouter_log_full_exchanges BOOLEAN NOT NULL DEFAULT 0"),
            ("openrouter_temperature", "ALTER TABLE settings ADD COLUMN openrouter_temperature FLOAT NOT NULL DEFAULT 0"),
            ("routing_strategy", "ALTER TABLE settings ADD COLUMN routing_strategy VARCHAR(32) NOT NULL DEFAULT 'free_first'"),
            ("allow_paid_fallback", "ALTER TABLE settings ADD COLUMN allow_paid_fallback BOOLEAN NOT NULL DEFAULT 0"),
            ("allow_free_fallback", "ALTER TABLE settings ADD COLUMN allow_free_fallback BOOLEAN NOT NULL DEFAULT 1"),
            ("allow_unknown_pricing", "ALTER TABLE settings ADD COLUMN allow_unknown_pricing BOOLEAN NOT NULL DEFAULT 0"),
            ("maximum_cost_per_job_micro_usd", "ALTER TABLE settings ADD COLUMN maximum_cost_per_job_micro_usd INTEGER"),
            ("monthly_budget_enabled", "ALTER TABLE settings ADD COLUMN monthly_budget_enabled BOOLEAN NOT NULL DEFAULT 0"),
            ("monthly_budget_amount_micro_usd", "ALTER TABLE settings ADD COLUMN monthly_budget_amount_micro_usd INTEGER"),
            ("allow_manual_budget_override", "ALTER TABLE settings ADD COLUMN allow_manual_budget_override BOOLEAN NOT NULL DEFAULT 0"),
            ("require_translation_approval", "ALTER TABLE settings ADD COLUMN require_translation_approval BOOLEAN NOT NULL DEFAULT 0"),
            ("operator_model_id", "ALTER TABLE settings ADD COLUMN operator_model_id VARCHAR(256)"),
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
            # Legacy classifier matched "ping" inside "mapping" on translate prompts.
            conn.execute(
                text(
                    """
                    UPDATE ai_usage_records
                    SET operation_type = 'translation'
                    WHERE operation_type = 'model_test' AND job_id IS NOT NULL
                    """
                )
            )

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

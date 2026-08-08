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
        rows = conn.execute(text("PRAGMA table_info(jobs)")).fetchall()
        columns = {row[1] for row in rows}
        if "job_kind" not in columns:
            conn.execute(
                text("ALTER TABLE jobs ADD COLUMN job_kind VARCHAR(32) NOT NULL DEFAULT 'translate'")
            )
        if "extract_stream_index" not in columns:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN extract_stream_index INTEGER"))

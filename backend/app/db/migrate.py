"""Run Alembic upgrades on startup.

Existing installs created via ``init_db()`` have no ``alembic_version`` row.
Those are stamped at the last revision that ``init_db`` already applied
(0012), then upgraded so *new* schema only comes from Alembic.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text

from app.core.logging import get_logger

logger = get_logger("migrate")

# Last revision covered by the historical init_db() ALTER TABLE soup.
LEGACY_STAMP_REVISION = "0012"


def _alembic_config():
    from alembic.config import Config

    ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(ini))
    from app.core.config import get_app_config

    cfg.set_main_option("sqlalchemy.url", get_app_config().database_url)
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "alembic"))
    return cfg


def run_schema_migrations() -> None:
    """Stamp legacy DBs, then ``alembic upgrade head`` on the app engine."""
    from alembic import command

    from app.db import get_engine

    engine = get_engine()
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    cfg = _alembic_config()
    cfg.set_main_option("sqlalchemy.url", str(engine.url))

    with engine.connect() as connection:
        cfg.attributes["connection"] = connection
        if "alembic_version" not in tables and tables.intersection({"settings", "jobs"}):
            logger.info(
                "Stamping pre-Alembic database at %s before upgrade",
                LEGACY_STAMP_REVISION,
            )
            command.stamp(cfg, LEGACY_STAMP_REVISION)
        command.upgrade(cfg, "head")
        row = connection.execute(text("SELECT version_num FROM alembic_version")).fetchone()
        version = row[0] if row else None
    logger.info("Schema at Alembic revision %s", version)

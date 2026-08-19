"""Add max concurrent dub setting."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_max_concurrent_dub"
down_revision = "0013_quality_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = {
        row[0]
        for row in conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    }
    if "settings" not in tables:
        return
    settings_cols = {
        row[1] for row in conn.execute(sa.text("PRAGMA table_info(settings)")).fetchall()
    }
    if "max_concurrent_dub" in settings_cols:
        return
    with op.batch_alter_table("settings") as batch:
        batch.add_column(
            sa.Column(
                "max_concurrent_dub",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    settings_cols = {
        row[1] for row in conn.execute(sa.text("PRAGMA table_info(settings)")).fetchall()
    }
    if "max_concurrent_dub" not in settings_cols:
        return
    with op.batch_alter_table("settings") as batch:
        batch.drop_column("max_concurrent_dub")

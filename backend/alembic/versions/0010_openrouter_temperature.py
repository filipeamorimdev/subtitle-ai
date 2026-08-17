"""Add OpenRouter request temperature setting."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_openrouter_temperature"
down_revision = "0009_nullable_job_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.add_column(
            sa.Column(
                "openrouter_temperature",
                sa.Float(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.drop_column("openrouter_temperature")

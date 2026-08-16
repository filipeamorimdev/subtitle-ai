"""Allow jobs.provider_id to be NULL until ModelRouter selects a candidate."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_nullable_job_provider"
down_revision = "0008_localization_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.alter_column(
            "provider_id",
            existing_type=sa.String(length=64),
            nullable=True,
            existing_nullable=False,
            server_default=None,
            existing_server_default="openrouter",
        )


def downgrade() -> None:
    op.execute("UPDATE jobs SET provider_id = 'openrouter' WHERE provider_id IS NULL")
    with op.batch_alter_table("jobs") as batch:
        batch.alter_column(
            "provider_id",
            existing_type=sa.String(length=64),
            nullable=False,
            existing_nullable=True,
            server_default="openrouter",
        )

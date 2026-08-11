"""Add per-job-kind concurrency settings."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_job_concurrency"
down_revision = "0003_glossary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.add_column(
            sa.Column(
                "max_concurrent_translate",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )
        batch.add_column(
            sa.Column(
                "max_concurrent_extract",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )
        batch.add_column(
            sa.Column(
                "max_concurrent_request",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.drop_column("max_concurrent_request")
        batch.drop_column("max_concurrent_extract")
        batch.drop_column("max_concurrent_translate")

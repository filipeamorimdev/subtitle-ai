"""Add automatic subtitle fallback settings and observation state."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_automatic_fallback"
down_revision = "0004_job_concurrency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.add_column(
            sa.Column(
                "automatic_fallback_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "automatic_scan_interval_minutes",
                sa.Integer(),
                nullable=False,
                server_default="5",
            )
        )
        batch.add_column(
            sa.Column(
                "bazarr_grace_period_minutes",
                sa.Integer(),
                nullable=False,
                server_default="10",
            )
        )
        batch.add_column(
            sa.Column(
                "automatic_retry_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(
            sa.Column(
                "maximum_automatic_retries",
                sa.Integer(),
                nullable=False,
                server_default="3",
            )
        )
        batch.add_column(
            sa.Column(
                "openrouter_log_full_exchanges",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    with op.batch_alter_table("jobs") as batch:
        batch.add_column(
            sa.Column(
                "trigger_type",
                sa.String(length=16),
                nullable=False,
                server_default="manual",
            )
        )
        batch.create_index("ix_jobs_trigger_type", ["trigger_type"])

    op.create_table(
        "observed_candidates",
        sa.Column("candidate_key", sa.String(length=512), primary_key=True),
        sa.Column("media_type", sa.String(length=32), nullable=False),
        sa.Column("media_path", sa.String(length=1024), nullable=False),
        sa.Column("media_title", sa.String(length=512), nullable=True),
        sa.Column("target_language", sa.String(length=32), nullable=False),
        sa.Column("bazarr_movie_id", sa.Integer(), nullable=True),
        sa.Column("bazarr_episode_id", sa.Integer(), nullable=True),
        sa.Column("bazarr_series_id", sa.Integer(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_automatic_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("automatic_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_outcome", sa.String(length=64), nullable=True),
        sa.Column("last_reason_code", sa.String(length=64), nullable=True),
        sa.Column("currently_wanted", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_table("observed_candidates")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_trigger_type")
        batch.drop_column("trigger_type")
    with op.batch_alter_table("settings") as batch:
        batch.drop_column("openrouter_log_full_exchanges")
        batch.drop_column("maximum_automatic_retries")
        batch.drop_column("automatic_retry_enabled")
        batch.drop_column("bazarr_grace_period_minutes")
        batch.drop_column("automatic_scan_interval_minutes")
        batch.drop_column("automatic_fallback_enabled")

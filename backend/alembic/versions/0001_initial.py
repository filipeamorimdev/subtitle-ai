"""Initial schema."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bazarr_url", sa.String(length=512), nullable=True),
        sa.Column("bazarr_api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("openrouter_api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("openrouter_model", sa.String(length=256), nullable=False),
        sa.Column("target_language_code", sa.String(length=32), nullable=False),
        sa.Column("target_language_name", sa.String(length=128), nullable=False),
        sa.Column("source_languages", sa.JSON(), nullable=False),
        sa.Column("media_roots", sa.JSON(), nullable=False),
        sa.Column("path_mappings", sa.JSON(), nullable=False),
        sa.Column("batch_size", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("candidate_key", sa.String(length=512), nullable=True),
        sa.Column("media_type", sa.String(length=32), nullable=False),
        sa.Column("media_path", sa.String(length=1024), nullable=False),
        sa.Column("media_title", sa.String(length=512), nullable=True),
        sa.Column("bazarr_movie_id", sa.Integer(), nullable=True),
        sa.Column("bazarr_episode_id", sa.Integer(), nullable=True),
        sa.Column("bazarr_series_id", sa.Integer(), nullable=True),
        sa.Column("source_subtitle_path", sa.String(length=1024), nullable=False),
        sa.Column("target_subtitle_path", sa.String(length=1024), nullable=False),
        sa.Column("source_language", sa.String(length=32), nullable=False),
        sa.Column("target_language", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("progress_detail", sa.String(length=256), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("warning", sa.Text(), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("dedupe_key", sa.String(length=128), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_jobs_candidate_key", "jobs", ["candidate_key"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_dedupe_key", "jobs", ["dedupe_key"])
    op.create_table(
        "translation_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("target_language", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("target_subtitle_path", sa.String(length=1024), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_translation_cache_source_hash", "translation_cache", ["source_hash"])


def downgrade() -> None:
    op.drop_table("translation_cache")
    op.drop_table("jobs")
    op.drop_table("settings")

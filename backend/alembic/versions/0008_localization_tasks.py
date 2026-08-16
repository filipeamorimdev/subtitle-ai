"""Add media items, localization tasks, and jobs.task_id."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_localization_tasks"
down_revision = "0007_ai_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("media_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("path", sa.String(length=1024), nullable=True),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("episode", sa.Integer(), nullable=True),
        sa.Column("episode_title", sa.String(length=512), nullable=True),
        sa.Column("bazarr_movie_id", sa.Integer(), nullable=True),
        sa.Column("bazarr_series_id", sa.Integer(), nullable=True),
        sa.Column("bazarr_episode_id", sa.Integer(), nullable=True),
        sa.Column("parent_media_id", sa.Integer(), sa.ForeignKey("media_items.id"), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("provider_id", "external_id", name="uq_media_items_provider_external"),
    )
    op.create_index("ix_media_items_provider_id", "media_items", ["provider_id"])
    op.create_index("ix_media_items_external_id", "media_items", ["external_id"])
    op.create_index("ix_media_items_media_type", "media_items", ["media_type"])
    op.create_index("ix_media_items_bazarr_movie_id", "media_items", ["bazarr_movie_id"])
    op.create_index("ix_media_items_bazarr_series_id", "media_items", ["bazarr_series_id"])
    op.create_index("ix_media_items_bazarr_episode_id", "media_items", ["bazarr_episode_id"])
    op.create_index("ix_media_items_parent_media_id", "media_items", ["parent_media_id"])

    op.create_table(
        "localization_tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("media_item_id", sa.Integer(), sa.ForeignKey("media_items.id"), nullable=False),
        sa.Column("target_language_code", sa.String(length=32), nullable=False),
        sa.Column("target_language_name", sa.String(length=128), nullable=False),
        sa.Column("capability", sa.String(length=32), nullable=False, server_default="subtitles"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="requested"),
        sa.Column("substate", sa.String(length=64), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("priority", sa.String(length=16), nullable=False, server_default="high"),
        sa.Column("requested_by", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_localization_tasks_media_item_id", "localization_tasks", ["media_item_id"])
    op.create_index(
        "ix_localization_tasks_target_language_code",
        "localization_tasks",
        ["target_language_code"],
    )
    op.create_index("ix_localization_tasks_capability", "localization_tasks", ["capability"])
    op.create_index("ix_localization_tasks_status", "localization_tasks", ["status"])
    op.create_index("ix_localization_tasks_origin", "localization_tasks", ["origin"])
    op.create_index("ix_localization_tasks_priority", "localization_tasks", ["priority"])

    # Partial unique index: one active task per media + language + capability.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_localization_tasks_active
        ON localization_tasks (media_item_id, target_language_code, capability)
        WHERE status IN (
            'requested', 'planning', 'waiting_for_source', 'processing', 'verifying'
        )
        """
    )

    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("task_id", sa.Integer(), nullable=True))
    op.create_index("ix_jobs_task_id", "jobs", ["task_id"])
    # SQLite: FK via batch recreate is heavy; app treats task_id as soft link + index.


def downgrade() -> None:
    op.drop_index("ix_jobs_task_id", table_name="jobs")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("task_id")

    op.execute("DROP INDEX IF EXISTS uq_localization_tasks_active")
    op.drop_index("ix_localization_tasks_priority", table_name="localization_tasks")
    op.drop_index("ix_localization_tasks_origin", table_name="localization_tasks")
    op.drop_index("ix_localization_tasks_status", table_name="localization_tasks")
    op.drop_index("ix_localization_tasks_capability", table_name="localization_tasks")
    op.drop_index("ix_localization_tasks_target_language_code", table_name="localization_tasks")
    op.drop_index("ix_localization_tasks_media_item_id", table_name="localization_tasks")
    op.drop_table("localization_tasks")

    op.drop_index("ix_media_items_parent_media_id", table_name="media_items")
    op.drop_index("ix_media_items_bazarr_episode_id", table_name="media_items")
    op.drop_index("ix_media_items_bazarr_series_id", table_name="media_items")
    op.drop_index("ix_media_items_bazarr_movie_id", table_name="media_items")
    op.drop_index("ix_media_items_media_type", table_name="media_items")
    op.drop_index("ix_media_items_external_id", table_name="media_items")
    op.drop_index("ix_media_items_provider_id", table_name="media_items")
    op.drop_table("media_items")

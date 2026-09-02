"""Remove the translation review setting and retire pending review tasks."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_remove_translation_review"
down_revision = "0021_local_whisper_only"
branch_labels = None
depends_on = None


def _columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()}


def upgrade() -> None:
    conn = op.get_bind()
    tables = {
        row[0]
        for row in conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    }
    if "localization_tasks" in tables:
        conn.execute(
            sa.text(
                """
                UPDATE localization_tasks
                SET status = 'failed',
                    substate = NULL,
                    error_code = 'translation_review_removed',
                    error_message = 'Translation review was removed. Retry this task to publish the translation.',
                    completed_at = CURRENT_TIMESTAMP
                WHERE status = 'awaiting_approval'
                """
            )
        )
        conn.execute(sa.text("DROP INDEX IF EXISTS uq_localization_tasks_active"))
        conn.execute(
            sa.text(
                """
                CREATE UNIQUE INDEX uq_localization_tasks_active
                ON localization_tasks (media_item_id, target_language_code, capability)
                WHERE status IN (
                    'requested', 'planning', 'waiting_for_source', 'processing', 'verifying'
                )
                """
            )
        )
    if "settings" in tables and "require_translation_approval" in _columns(conn, "settings"):
        with op.batch_alter_table("settings") as batch:
            batch.drop_column("require_translation_approval")


def downgrade() -> None:
    conn = op.get_bind()
    if "settings" in {
        row[0]
        for row in conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    } and "require_translation_approval" not in _columns(conn, "settings"):
        with op.batch_alter_table("settings") as batch:
            batch.add_column(
                sa.Column(
                    "require_translation_approval",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )

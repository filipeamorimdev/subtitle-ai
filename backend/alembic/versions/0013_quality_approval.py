"""Alembic revision 0013: glossary, locale notes, approval setting."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_quality_approval"
down_revision = "0012_asr_transcribe"
branch_labels = None
depends_on = None


def _tables(conn) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    }


def upgrade() -> None:
    conn = op.get_bind()
    tables = _tables(conn)
    if "glossary_entries" not in tables:
        op.create_table(
            "glossary_entries",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("scope_key", sa.String(length=128), nullable=False),
            sa.Column("target_language", sa.String(length=32), nullable=False),
            sa.Column("source", sa.String(length=512), nullable=False),
            sa.Column("source_normalized", sa.String(length=512), nullable=False),
            sa.Column("target", sa.String(length=512), nullable=False),
            sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint(
                "scope_key",
                "target_language",
                "source_normalized",
                name="uq_glossary_entries_scope_lang_source",
            ),
        )
        op.create_index("ix_glossary_entries_scope_key", "glossary_entries", ["scope_key"])
        op.create_index(
            "ix_glossary_entries_target_language", "glossary_entries", ["target_language"]
        )

    if "locale_prompt_notes" not in tables:
        op.create_table(
            "locale_prompt_notes",
            sa.Column("language_code", sa.String(length=32), primary_key=True),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "settings" in tables:
        settings_cols = {
            row[1] for row in conn.execute(sa.text("PRAGMA table_info(settings)")).fetchall()
        }
        if "require_translation_approval" not in settings_cols:
            with op.batch_alter_table("settings") as batch:
                batch.add_column(
                    sa.Column(
                        "require_translation_approval",
                        sa.Boolean(),
                        nullable=False,
                        server_default=sa.false(),
                    )
                )

    if "localization_tasks" in tables:
        conn.execute(sa.text("DROP INDEX IF EXISTS uq_localization_tasks_active"))
        conn.execute(
            sa.text(
                """
                CREATE UNIQUE INDEX uq_localization_tasks_active
                ON localization_tasks (media_item_id, target_language_code, capability)
                WHERE status IN (
                    'requested', 'planning', 'waiting_for_source', 'processing',
                    'verifying', 'awaiting_approval'
                )
                """
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
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
    settings_cols = {
        row[1] for row in conn.execute(sa.text("PRAGMA table_info(settings)")).fetchall()
    }
    if "require_translation_approval" in settings_cols:
        with op.batch_alter_table("settings") as batch:
            batch.drop_column("require_translation_approval")
    tables = _tables(conn)
    if "locale_prompt_notes" in tables:
        op.drop_table("locale_prompt_notes")
    if "glossary_entries" in tables:
        op.drop_table("glossary_entries")

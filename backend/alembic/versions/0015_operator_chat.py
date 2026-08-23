"""Add operator chat sessions and operator_model_id setting."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_operator_chat"
down_revision = "0014_max_concurrent_dub"
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
    if "settings" in tables:
        settings_cols = {
            row[1] for row in conn.execute(sa.text("PRAGMA table_info(settings)")).fetchall()
        }
        if "operator_model_id" not in settings_cols:
            with op.batch_alter_table("settings") as batch:
                batch.add_column(sa.Column("operator_model_id", sa.String(length=256), nullable=True))

    if "operator_chat_sessions" not in tables:
        op.create_table(
            "operator_chat_sessions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    if "operator_chat_messages" not in tables:
        op.create_table(
            "operator_chat_messages",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("session_id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("content_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["session_id"], ["operator_chat_sessions.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_operator_chat_messages_session_id",
            "operator_chat_messages",
            ["session_id"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    tables = {
        row[0]
        for row in conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    }
    if "operator_chat_messages" in tables:
        op.drop_index("ix_operator_chat_messages_session_id", table_name="operator_chat_messages")
        op.drop_table("operator_chat_messages")
    if "operator_chat_sessions" in tables:
        op.drop_table("operator_chat_sessions")
    settings_cols = {
        row[1] for row in conn.execute(sa.text("PRAGMA table_info(settings)")).fetchall()
    }
    if "operator_model_id" in settings_cols:
        with op.batch_alter_table("settings") as batch:
            batch.drop_column("operator_model_id")

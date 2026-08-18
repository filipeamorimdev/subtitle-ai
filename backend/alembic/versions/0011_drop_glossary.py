"""Drop glossary tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_drop_glossary"
down_revision = "0010_openrouter_temperature"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("glossary_terms")
    op.drop_table("glossary_scopes")


def downgrade() -> None:
    op.create_table(
        "glossary_scopes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=256), nullable=False),
        sa.Column("display_name", sa.String(length=512), nullable=False),
        sa.Column("target_language", sa.String(length=32), nullable=False),
        sa.Column("parent_scope_id", sa.Integer(), sa.ForeignKey("glossary_scopes.id"), nullable=True),
        sa.Column("bazarr_series_id", sa.Integer(), nullable=True),
        sa.Column("bazarr_movie_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_glossary_scopes_kind", "glossary_scopes", ["kind"])
    op.create_index("ix_glossary_scopes_key", "glossary_scopes", ["key"])
    op.create_index("ix_glossary_scopes_target_language", "glossary_scopes", ["target_language"])
    op.create_index("ix_glossary_scopes_parent_scope_id", "glossary_scopes", ["parent_scope_id"])
    op.create_index("ix_glossary_scopes_bazarr_series_id", "glossary_scopes", ["bazarr_series_id"])
    op.create_index("ix_glossary_scopes_bazarr_movie_id", "glossary_scopes", ["bazarr_movie_id"])
    op.create_unique_constraint(
        "uq_glossary_scope_key_lang",
        "glossary_scopes",
        ["key", "target_language"],
    )

    op.create_table(
        "glossary_terms",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "scope_id",
            sa.Integer(),
            sa.ForeignKey("glossary_scopes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=512), nullable=False),
        sa.Column("source_normalized", sa.String(length=512), nullable=False),
        sa.Column("target", sa.String(length=512), nullable=False),
        sa.Column("term_type", sa.String(length=32), nullable=False, server_default="other"),
        sa.Column("policy", sa.String(length=32), nullable=False, server_default="keep"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="suggested"),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_origin", sa.String(length=32), nullable=False, server_default="llm"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_glossary_terms_scope_id", "glossary_terms", ["scope_id"])
    op.create_index("ix_glossary_terms_status", "glossary_terms", ["status"])
    op.create_unique_constraint(
        "uq_glossary_term_scope_source",
        "glossary_terms",
        ["scope_id", "source_normalized"],
    )

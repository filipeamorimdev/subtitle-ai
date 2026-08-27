"""Migrate legacy glossary scopes and terms without losing user data."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_migrate_legacy_glossary"
down_revision = "0015_operator_chat"
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
    if not {"glossary_scopes", "glossary_terms", "glossary_entries"}.issubset(tables):
        return

    # Scope keys were the stable user-facing identity in the former schema.
    # Preserve each legacy term once; the new unique constraint makes this
    # repeat-safe for interrupted or manually repaired databases.
    conn.execute(
        sa.text(
            """
            INSERT OR IGNORE INTO glossary_entries (
                scope_key, target_language, source, source_normalized, target,
                locked, created_at, updated_at
            )
            SELECT
                scopes.key,
                scopes.target_language,
                terms.source,
                terms.source_normalized,
                terms.target,
                terms.locked,
                COALESCE(terms.created_at, CURRENT_TIMESTAMP),
                COALESCE(terms.updated_at, CURRENT_TIMESTAMP)
            FROM glossary_terms AS terms
            JOIN glossary_scopes AS scopes ON scopes.id = terms.scope_id
            WHERE trim(terms.source) <> '' AND trim(terms.target) <> ''
            """
        )
    )
    op.drop_table("glossary_terms")
    op.drop_table("glossary_scopes")


def downgrade() -> None:
    # A downgrade must not recreate empty legacy tables and imply that data was
    # restored.  The new glossary table remains the source of truth.
    return

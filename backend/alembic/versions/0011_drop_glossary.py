"""Retire legacy glossary tables after their data is migrated.

The replacement ``glossary_entries`` table is created in revision 0013, so
this revision must not drop the legacy data.  Revision 0016 copies it after
the replacement table exists and only then removes the old tables.
"""

from __future__ import annotations

revision = "0011_drop_glossary"
down_revision = "0010_openrouter_temperature"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Intentionally empty.  See module docstring.
    return


def downgrade() -> None:
    # The upgrade did not remove any schema, so there is nothing to restore.
    return

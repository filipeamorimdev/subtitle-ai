"""Persist selected dub mix mode and subtitle-speaker voice overrides."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_dub_mix_options"
down_revision = "0016_migrate_legacy_glossary"
branch_labels = None
depends_on = None


def _job_columns() -> set[str]:
    conn = op.get_bind()
    return {row[1] for row in conn.execute(sa.text("PRAGMA table_info(jobs)")).fetchall()}


def upgrade() -> None:
    columns = _job_columns()
    with op.batch_alter_table("jobs") as batch:
        if "dub_mix_mode" not in columns:
            batch.add_column(
                sa.Column(
                    "dub_mix_mode",
                    sa.String(length=32),
                    nullable=False,
                    server_default="background_preserved",
                )
            )
        if "dub_speaker_voices" not in columns:
            batch.add_column(
                sa.Column(
                    "dub_speaker_voices",
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'{}'"),
                )
            )


def downgrade() -> None:
    columns = _job_columns()
    with op.batch_alter_table("jobs") as batch:
        if "dub_speaker_voices" in columns:
            batch.drop_column("dub_speaker_voices")
        if "dub_mix_mode" in columns:
            batch.drop_column("dub_mix_mode")

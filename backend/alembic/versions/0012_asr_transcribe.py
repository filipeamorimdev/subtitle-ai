"""Add ASR / transcribe settings."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_asr_transcribe"
down_revision = "0011_drop_glossary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.add_column(
            sa.Column(
                "max_concurrent_transcribe",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )
        batch.add_column(
            sa.Column(
                "asr_provider",
                sa.String(length=32),
                nullable=False,
                server_default="local_then_openai",
            )
        )
        batch.add_column(
            sa.Column(
                "asr_local_model",
                sa.String(length=32),
                nullable=False,
                server_default="small",
            )
        )
        batch.add_column(sa.Column("openai_api_key_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.drop_column("openai_api_key_encrypted")
        batch.drop_column("asr_local_model")
        batch.drop_column("asr_provider")
        batch.drop_column("max_concurrent_transcribe")

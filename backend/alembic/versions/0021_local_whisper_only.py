"""Remove the retired OpenAI Whisper settings."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0021_local_whisper_only"
down_revision = "0020_series_voice_casts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.drop_column("asr_provider")
        batch.drop_column("openai_api_key_encrypted")


def downgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.add_column(
            sa.Column(
                "asr_provider", sa.String(length=32), nullable=False, server_default="local"
            )
        )
        batch.add_column(sa.Column("openai_api_key_encrypted", sa.Text(), nullable=True))

"""Remove the retired OpenAI Whisper settings."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0021_local_whisper_only"
down_revision = "0020_series_voice_casts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``init_db()`` creates fresh databases from the current ORM model, which
    # no longer includes these columns. Existing databases still need the
    # columns removed, so only run the SQLite batch rebuild when necessary.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("settings")}
    if not {"asr_provider", "openai_api_key_encrypted"}.intersection(columns):
        return

    with op.batch_alter_table("settings") as batch:
        if "asr_provider" in columns:
            batch.drop_column("asr_provider")
        if "openai_api_key_encrypted" in columns:
            batch.drop_column("openai_api_key_encrypted")


def downgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.add_column(
            sa.Column(
                "asr_provider", sa.String(length=32), nullable=False, server_default="local"
            )
        )
        batch.add_column(sa.Column("openai_api_key_encrypted", sa.Text(), nullable=True))

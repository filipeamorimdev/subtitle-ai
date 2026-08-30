"""Add Jellyfin media-catalog settings."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_jellyfin_catalog"
down_revision = "0018_audio_analysis_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("settings")}
    with op.batch_alter_table("settings") as batch:
        if "jellyfin_url" not in columns:
            batch.add_column(sa.Column("jellyfin_url", sa.String(length=512), nullable=True))
        if "jellyfin_api_key_encrypted" not in columns:
            batch.add_column(sa.Column("jellyfin_api_key_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("settings")}
    with op.batch_alter_table("settings") as batch:
        if "jellyfin_api_key_encrypted" in columns:
            batch.drop_column("jellyfin_api_key_encrypted")
        if "jellyfin_url" in columns:
            batch.drop_column("jellyfin_url")

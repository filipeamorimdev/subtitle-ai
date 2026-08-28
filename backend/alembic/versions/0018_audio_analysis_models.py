"""Add a separate audio-analysis model preference pool."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_audio_analysis_models"
down_revision = "0017_dub_mix_options"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("ai_model_preferences")}
    # ``init_db()`` keeps non-Alembic installs current before startup migrations
    # run. In that case the table already has the new constraint and column.
    if "purpose" in columns:
        return
    with op.batch_alter_table("ai_model_preferences", recreate="always") as batch:
        batch.add_column(
            sa.Column(
                "purpose",
                sa.String(length=32),
                nullable=False,
                server_default="translation",
            )
        )
        batch.drop_constraint("uq_ai_model_preferences_provider_model", type_="unique")
        batch.create_unique_constraint(
            "uq_ai_model_preferences_provider_model_purpose",
            ["provider_id", "model_id", "purpose"],
        )
        batch.create_index("ix_ai_model_preferences_purpose", ["purpose"])


def downgrade() -> None:
    # A downgrade cannot safely preserve a model that was selected for both
    # translation and audio analysis, so remove audio-only rows first.
    op.execute("DELETE FROM ai_model_preferences WHERE purpose = 'audio_analysis'")
    with op.batch_alter_table("ai_model_preferences", recreate="always") as batch:
        batch.drop_constraint("uq_ai_model_preferences_provider_model_purpose", type_="unique")
        batch.drop_index("ix_ai_model_preferences_purpose")
        batch.drop_column("purpose")
        batch.create_unique_constraint(
            "uq_ai_model_preferences_provider_model",
            ["provider_id", "model_id"],
        )

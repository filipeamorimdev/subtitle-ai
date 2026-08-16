"""Add provider-aware AI accounts, preferences, catalog, and identity columns."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_ai_providers"
down_revision = "0006_ai_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("base_url", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("provider_id", name="uq_ai_provider_accounts_provider_id"),
    )
    op.create_index("ix_ai_provider_accounts_provider_id", "ai_provider_accounts", ["provider_id"])

    op.create_table(
        "ai_model_preferences",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=256), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "provider_id", "model_id", name="uq_ai_model_preferences_provider_model"
        ),
    )
    op.create_index("ix_ai_model_preferences_provider_id", "ai_model_preferences", ["provider_id"])
    op.create_index("ix_ai_model_preferences_model_id", "ai_model_preferences", ["model_id"])
    op.create_index("ix_ai_model_preferences_tier", "ai_model_preferences", ["tier"])

    op.create_table(
        "ai_model_catalog_cache",
        sa.Column("provider_id", sa.String(length=64), primary_key=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    with op.batch_alter_table("ai_usage_records") as batch:
        batch.add_column(
            sa.Column(
                "provider_id",
                sa.String(length=64),
                nullable=False,
                server_default="openrouter",
            )
        )
        batch.add_column(sa.Column("request_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("attempt_number", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("cost_source", sa.String(length=32), nullable=True))
    op.create_index("ix_ai_usage_records_provider_id", "ai_usage_records", ["provider_id"])
    op.create_index("ix_ai_usage_records_request_id", "ai_usage_records", ["request_id"])
    op.create_index(
        "ix_ai_usage_records_provider_model",
        "ai_usage_records",
        ["provider_id", "model_id"],
    )

    with op.batch_alter_table("ai_routing_events") as batch:
        batch.add_column(sa.Column("provider_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("next_provider_id", sa.String(length=64), nullable=True))
    op.create_index("ix_ai_routing_events_provider_id", "ai_routing_events", ["provider_id"])

    with op.batch_alter_table("jobs") as batch:
        batch.add_column(
            sa.Column(
                "provider_id",
                sa.String(length=64),
                nullable=False,
                server_default="openrouter",
            )
        )
    op.create_index("ix_jobs_provider_id", "jobs", ["provider_id"])

    with op.batch_alter_table("translation_cache") as batch:
        batch.add_column(
            sa.Column(
                "provider_id",
                sa.String(length=64),
                nullable=False,
                server_default="openrouter",
            )
        )
    op.create_index("ix_translation_cache_provider_id", "translation_cache", ["provider_id"])


def downgrade() -> None:
    op.drop_index("ix_translation_cache_provider_id", table_name="translation_cache")
    with op.batch_alter_table("translation_cache") as batch:
        batch.drop_column("provider_id")

    op.drop_index("ix_jobs_provider_id", table_name="jobs")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("provider_id")

    op.drop_index("ix_ai_routing_events_provider_id", table_name="ai_routing_events")
    with op.batch_alter_table("ai_routing_events") as batch:
        batch.drop_column("next_provider_id")
        batch.drop_column("provider_id")

    op.drop_index("ix_ai_usage_records_provider_model", table_name="ai_usage_records")
    op.drop_index("ix_ai_usage_records_request_id", table_name="ai_usage_records")
    op.drop_index("ix_ai_usage_records_provider_id", table_name="ai_usage_records")
    with op.batch_alter_table("ai_usage_records") as batch:
        batch.drop_column("cost_source")
        batch.drop_column("attempt_number")
        batch.drop_column("request_id")
        batch.drop_column("provider_id")

    op.drop_table("ai_model_catalog_cache")
    op.drop_index("ix_ai_model_preferences_tier", table_name="ai_model_preferences")
    op.drop_index("ix_ai_model_preferences_model_id", table_name="ai_model_preferences")
    op.drop_index("ix_ai_model_preferences_provider_id", table_name="ai_model_preferences")
    op.drop_table("ai_model_preferences")
    op.drop_index("ix_ai_provider_accounts_provider_id", table_name="ai_provider_accounts")
    op.drop_table("ai_provider_accounts")

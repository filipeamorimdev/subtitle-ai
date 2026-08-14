"""Add AI model routing, catalog cache, usage, and budget tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_ai_routing"
down_revision = "0005_automatic_fallback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.add_column(
            sa.Column(
                "routing_strategy",
                sa.String(length=32),
                nullable=False,
                server_default="free_first",
            )
        )
        batch.add_column(
            sa.Column(
                "allow_paid_fallback",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "allow_free_fallback",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(
            sa.Column(
                "allow_unknown_pricing",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("maximum_cost_per_job_micro_usd", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "monthly_budget_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("monthly_budget_amount_micro_usd", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "allow_manual_budget_override",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    op.create_table(
        "openrouter_model_preferences",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("model_id", sa.String(length=256), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("model_id", name="uq_openrouter_model_preferences_model_id"),
        sa.UniqueConstraint("tier", "model_id", name="uq_openrouter_model_preferences_tier_model"),
    )
    op.create_index(
        "ix_openrouter_model_preferences_model_id",
        "openrouter_model_preferences",
        ["model_id"],
    )
    op.create_index(
        "ix_openrouter_model_preferences_tier",
        "openrouter_model_preferences",
        ["tier"],
    )

    op.create_table(
        "openrouter_catalog_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "ai_usage_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("operation_type", sa.String(length=64), nullable=False),
        sa.Column("trigger_type", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("model_id", sa.String(length=256), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="success"),
        sa.Column("failure_category", sa.String(length=64), nullable=True),
        sa.Column("outcome", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_micro_usd", sa.Integer(), nullable=True),
        sa.Column("actual_cost_micro_usd", sa.Integer(), nullable=True),
        sa.Column("input_price_micro_usd_per_million", sa.Integer(), nullable=True),
        sa.Column("output_price_micro_usd_per_million", sa.Integer(), nullable=True),
        sa.Column("pricing_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pricing_source", sa.String(length=32), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    for col in (
        "job_id",
        "operation_type",
        "trigger_type",
        "model_id",
        "tier",
        "status",
        "failure_category",
        "outcome",
        "created_at",
    ):
        op.create_index(f"ix_ai_usage_records_{col}", "ai_usage_records", [col])

    op.create_table(
        "ai_budget_reservations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("month_key", sa.String(length=7), nullable=False),
        sa.Column("amount_micro_usd", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ai_budget_reservations_job_id", "ai_budget_reservations", ["job_id"])
    op.create_index("ix_ai_budget_reservations_month_key", "ai_budget_reservations", ["month_key"])

    op.create_table(
        "ai_routing_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("event", sa.String(length=32), nullable=False),
        sa.Column("strategy", sa.String(length=32), nullable=True),
        sa.Column("model_id", sa.String(length=256), nullable=True),
        sa.Column("next_model_id", sa.String(length=256), nullable=True),
        sa.Column("failure_category", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.String(length=512), nullable=True),
    )
    op.create_index("ix_ai_routing_events_job_id", "ai_routing_events", ["job_id"])
    op.create_index("ix_ai_routing_events_created_at", "ai_routing_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("ai_routing_events")
    op.drop_table("ai_budget_reservations")
    op.drop_table("ai_usage_records")
    op.drop_table("openrouter_catalog_cache")
    op.drop_table("openrouter_model_preferences")
    with op.batch_alter_table("settings") as batch:
        for col in (
            "routing_strategy",
            "allow_paid_fallback",
            "allow_free_fallback",
            "allow_unknown_pricing",
            "maximum_cost_per_job_micro_usd",
            "monthly_budget_enabled",
            "monthly_budget_amount_micro_usd",
            "allow_manual_budget_override",
        ):
            batch.drop_column(col)

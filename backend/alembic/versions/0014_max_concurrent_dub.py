"""Add max concurrent dub setting."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_max_concurrent_dub"
down_revision = "0013_quality_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.add_column(
            sa.Column(
                "max_concurrent_dub",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.drop_column("max_concurrent_dub")


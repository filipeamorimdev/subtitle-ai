"""Add extract job support columns."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_extract_jobs"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("job_kind", sa.String(length=32), nullable=False, server_default="translate"))
        batch.add_column(sa.Column("extract_stream_index", sa.Integer(), nullable=True))
        batch.create_index("ix_jobs_job_kind", ["job_kind"])


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_job_kind")
        batch.drop_column("extract_stream_index")
        batch.drop_column("job_kind")

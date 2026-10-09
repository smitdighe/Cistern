"""create query_log and benchmark_results

Revision ID: 4406012a9c43
Revises:
Create Date: 2026-07-20 15:51:31.671662

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "4406012a9c43"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "query_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("generated_sql", sa.Text(), nullable=False),
        sa.Column("final_sql", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("safety_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_query_log_timestamp"), "query_log", ["timestamp"])
    op.create_index(op.f("ix_query_log_success"), "query_log", ["success"])

    op.create_table(
        "benchmark_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("gold_sql", sa.Text(), nullable=False),
        sa.Column("generated_sql", sa.Text(), nullable=False),
        sa.Column("exact_match", sa.Boolean(), nullable=False),
        sa.Column("execution_match", sa.Boolean(), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_benchmark_results_run_id"), "benchmark_results", ["run_id"])
    op.create_index(op.f("ix_benchmark_results_timestamp"), "benchmark_results", ["timestamp"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_benchmark_results_timestamp"), table_name="benchmark_results")
    op.drop_index(op.f("ix_benchmark_results_run_id"), table_name="benchmark_results")
    op.drop_table("benchmark_results")
    op.drop_index(op.f("ix_query_log_success"), table_name="query_log")
    op.drop_index(op.f("ix_query_log_timestamp"), table_name="query_log")
    op.drop_table("query_log")

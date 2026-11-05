"""recurring expenses

Revision ID: 0007
Revises: 0006
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recurring_expenses",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("merchant_pattern", sa.String(128), nullable=True),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(64), server_default="Shared", nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("amount_tolerance", sa.Numeric(4, 2), server_default="0.25", nullable=False),
        sa.Column("cadence", sa.String(16), nullable=False),
        sa.Column("anchor_date", sa.Date(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("origin", sa.String(16), server_default="user", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.add_column(
        "transactions",
        sa.Column("recurring_id", sa.UUID(), sa.ForeignKey("recurring_expenses.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_transactions_recurring_id", "transactions", ["recurring_id"])


def downgrade() -> None:
    op.drop_index("ix_transactions_recurring_id", table_name="transactions")
    op.drop_column("transactions", "recurring_id")
    op.drop_table("recurring_expenses")

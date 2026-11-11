"""income: income sources, transactions.income_source_id

Revision ID: 0018
Revises: 0017
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    op.create_table(
        "income_sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("earner", sa.String(32), nullable=False),
        sa.Column("account_id", sa.String(64), sa.ForeignKey("accounts.id")),
        sa.Column("payer_pattern", sa.String(128), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("amount_tolerance", sa.Numeric(4, 2), server_default="0.25", nullable=False),
        sa.Column("cadence", sa.String(16), nullable=False),
        sa.Column("anchor_date", sa.Date, nullable=False),
        sa.Column("active", sa.Boolean, server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column(
        "transactions",
        sa.Column("income_source_id", UUID(as_uuid=True), sa.ForeignKey("income_sources.id", ondelete="SET NULL")),
    )
    op.create_index("ix_transactions_income_source_id", "transactions", ["income_source_id"])
    op.create_index("idx_transactions_txn_type", "transactions", ["txn_type", "timestamp"])


def downgrade() -> None:
    op.drop_index("idx_transactions_txn_type", "transactions")
    op.drop_index("ix_transactions_income_source_id", "transactions")
    op.drop_column("transactions", "income_source_id")
    op.drop_table("income_sources")

"""month-end balances: balance_snapshots (one per account per day)

Revision ID: 0020
Revises: 0019
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "balance_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("account_id", sa.String(64), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("as_of", sa.Date, nullable=False),
        sa.Column("balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("source", sa.String(16), server_default="manual", nullable=False),
        sa.Column("note", sa.Text),
        sa.Column("created_by", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("account_id", "as_of", name="uq_balance_snapshots_day"),
    )
    op.create_index("ix_balance_snapshots_account_id", "balance_snapshots", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_balance_snapshots_account_id", "balance_snapshots")
    op.drop_table("balance_snapshots")

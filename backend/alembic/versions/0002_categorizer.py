"""categorizer: rule time windows/confidence, categorized_by, merchant index

Revision ID: 0002
Revises: 0001
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("categorized_by", sa.String(16), server_default="none", nullable=False))
    op.create_index("ix_transactions_merchant_normalized", "transactions", ["merchant_normalized"])

    op.alter_column("categorization_rules", "target_purpose", existing_type=sa.String(64), nullable=True)
    op.add_column("categorization_rules", sa.Column("hour_start", sa.Integer(), nullable=True))
    op.add_column("categorization_rules", sa.Column("hour_end", sa.Integer(), nullable=True))
    op.add_column(
        "categorization_rules", sa.Column("weekdays_only", sa.Boolean(), server_default="false", nullable=False)
    )
    op.add_column(
        "categorization_rules",
        sa.Column("confidence", sa.Numeric(3, 2), server_default="1.00", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("categorization_rules", "confidence")
    op.drop_column("categorization_rules", "weekdays_only")
    op.drop_column("categorization_rules", "hour_end")
    op.drop_column("categorization_rules", "hour_start")
    op.execute("UPDATE categorization_rules SET target_purpose = 'Personal' WHERE target_purpose IS NULL")
    op.alter_column("categorization_rules", "target_purpose", existing_type=sa.String(64), nullable=False)

    op.drop_index("ix_transactions_merchant_normalized", table_name="transactions")
    op.drop_column("transactions", "categorized_by")

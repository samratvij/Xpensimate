"""editable categories

Revision ID: 0011
Revises: 0010
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULTS = [
    "Rent + Parking", "Hydro", "Wifi + 5G", "Insurance", "Laundry", "Loan", "Grocery", "Eating Out", "Household",
    "Gas & Parking", "Car", "Entertainment", "Travel", "Gifting", "Health & Fitness", "Personal Care", "Shopping",
    "Subscriptions", "Government & Fees", "Transfer", "Misc", "One-time",
]


def upgrade() -> None:
    table = op.create_table(
        "categories",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.bulk_insert(table, [{"name": n, "sort_order": i} for i, n in enumerate(DEFAULTS)])


def downgrade() -> None:
    op.drop_table("categories")

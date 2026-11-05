"""free-text notes on transactions, split lines and receipt lines

Revision ID: 0013
Revises: 0012
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("transactions", "transaction_items", "receipt_items")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("note", sa.Text(), nullable=True))


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "note")

"""transfers between own accounts: transactions.counterpart_account_id

Revision ID: 0019
Revises: 0018
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("counterpart_account_id", sa.String(64), sa.ForeignKey("accounts.id")))


def downgrade() -> None:
    op.drop_column("transactions", "counterpart_account_id")

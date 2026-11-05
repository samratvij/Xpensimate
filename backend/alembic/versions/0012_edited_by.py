"""who changed a transaction or scanned a receipt (each person has their own API token)

Revision ID: 0012
Revises: 0011
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("edited_by", sa.String(32), nullable=True))
    op.add_column("receipts", sa.Column("created_by", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("receipts", "created_by")
    op.drop_column("transactions", "edited_by")

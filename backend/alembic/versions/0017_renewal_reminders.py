"""remember which renewal of a recurring expense was announced

Revision ID: 0017
Revises: 0016
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("recurring_expenses", sa.Column("reminded_due", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("recurring_expenses", "reminded_due")

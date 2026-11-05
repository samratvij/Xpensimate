"""gmail worker heartbeat: when it last checked the mailbox and its last error

Revision ID: 0014
Revises: 0013
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("gmail_sync_state", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("gmail_sync_state", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("gmail_sync_state", sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for column in ("last_error_at", "last_error", "last_checked_at"):
        op.drop_column("gmail_sync_state", column)

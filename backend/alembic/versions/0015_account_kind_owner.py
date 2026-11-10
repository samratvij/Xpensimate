"""accounts: kind and owner, and several cards on one account

Revision ID: 0015
Revises: 0014

Kind and owner are filled from household.toml when the server starts (app/db/seed.py: sync_accounts); until then
every account is a shared chequing account, which changes nothing that exists today.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("owner", sa.String(32), server_default="Shared", nullable=False))
    op.add_column("accounts", sa.Column("kind", sa.String(16), server_default="chequing", nullable=False))
    op.alter_column("accounts", "card_last4", type_=sa.String(64), existing_type=sa.String(4), existing_nullable=True)


def downgrade() -> None:
    op.execute("UPDATE accounts SET card_last4 = left(card_last4, 4)")
    op.alter_column("accounts", "card_last4", type_=sa.String(4), existing_type=sa.String(64), existing_nullable=True)
    op.drop_column("accounts", "kind")
    op.drop_column("accounts", "owner")

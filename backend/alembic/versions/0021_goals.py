"""savings target and goals: savings_targets (a monthly amount to move into savings and investments) and
savings_goals (an amount by a date, from chosen accounts). Both start empty and are set in the app.

Revision ID: 0021
Revises: 0020
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "savings_targets",
        sa.Column("scope", sa.String(32), primary_key=True),
        sa.Column("monthly_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("alert_day", sa.Integer, server_default="25", nullable=False),
        sa.Column("warning_percent", sa.Numeric(5, 2), server_default="80.00", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "savings_goals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("target_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("target_date", sa.Date, nullable=False),
        sa.Column("account_kinds", JSONB, server_default="[]", nullable=False),
        sa.Column("account_ids", JSONB, server_default="[]", nullable=False),
        sa.Column("active", sa.Boolean, server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("savings_goals")
    op.drop_table("savings_targets")

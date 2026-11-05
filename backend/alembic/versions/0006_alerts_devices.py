"""budget alerts and push devices

Revision ID: 0006
Revises: 0005
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("token", sa.String(512), primary_key=True),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "budget_alerts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("budget_line", sa.String(64), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("monthly_limit", sa.Numeric(10, 2), nullable=False),
        sa.Column("spent", sa.Numeric(10, 2), nullable=False),
        sa.Column("transaction_id", sa.UUID(), sa.ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("month", "budget_line", "level", name="uq_budget_alerts_once"),
    )


def downgrade() -> None:
    op.drop_table("budget_alerts")
    op.drop_table("devices")

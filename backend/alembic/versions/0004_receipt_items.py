"""receipt items and review flag

Revision ID: 0004
Revises: 0003
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("receipts", sa.Column("needs_review", sa.Boolean(), server_default="false", nullable=False))
    op.create_table(
        "receipt_items",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("receipt_id", sa.UUID(), sa.ForeignKey("receipts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("sku_name", sa.String(255), nullable=False),
        sa.Column("raw_text", sa.String(255), nullable=True),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("category_is_manual", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("purpose", sa.String(64), server_default="Personal", nullable=False),
        sa.Column("purpose_is_manual", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("quantity", sa.Numeric(6, 2), server_default="1.0", nullable=False),
    )
    op.create_index("ix_receipt_items_receipt_id", "receipt_items", ["receipt_id"])


def downgrade() -> None:
    op.drop_index("ix_receipt_items_receipt_id", table_name="receipt_items")
    op.drop_table("receipt_items")
    op.drop_column("receipts", "needs_review")

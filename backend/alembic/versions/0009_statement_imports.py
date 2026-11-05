"""statement imports

Revision ID: 0009
Revises: 0008
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "statement_imports",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("bank_format", sa.String(32), nullable=False),
        sa.Column("account_id", sa.String(64), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("status", sa.String(16), server_default="preview", nullable=False),
        sa.Column("rows", postgresql.JSONB(), nullable=False),
        sa.Column("missing", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("processed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.add_column(
        "transactions",
        sa.Column("statement_import_id", sa.UUID(), sa.ForeignKey("statement_imports.id", ondelete="SET NULL"),
                  nullable=True),
    )
    op.create_index("ix_transactions_statement_import_id", "transactions", ["statement_import_id"])


def downgrade() -> None:
    op.drop_index("ix_transactions_statement_import_id", table_name="transactions")
    op.drop_column("transactions", "statement_import_id")
    op.drop_table("statement_imports")

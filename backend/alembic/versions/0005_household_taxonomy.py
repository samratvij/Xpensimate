"""household taxonomy (what + who), rule origin, categorization examples

Revision ID: 0005
Revises: 0004
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CATEGORY_MAP = {
    "Groceries": "Grocery",
    "Dining & Takeout": "Eating Out",
    "Electronics & Tech": "Shopping",
    "Household & Supplies": "Household",
    "Transportation & Fuel": "Gas & Parking",
    "Utilities & Bills": "Misc",
    "General Merchandise": "Shopping",
}
OLD_PURPOSES = ("Personal", "Household/Family", "Work/Reimbursable", "Project", "Vacation/Travel", "Gift/Donation")
TABLES = ("transactions", "transaction_items", "receipt_items")


def upgrade() -> None:
    op.add_column("categorization_rules", sa.Column("origin", sa.String(16), server_default="user", nullable=False))
    # The old generic starter rules use the old taxonomy; seed() re-adds the new set.
    op.execute("DELETE FROM categorization_rules WHERE priority < 10")
    op.execute("DELETE FROM budget_envelopes")

    for old, new in CATEGORY_MAP.items():
        for table in TABLES:
            op.execute(sa.text(f"UPDATE {table} SET category = :new WHERE category = :old").bindparams(old=old, new=new))
        op.execute(
            sa.text("UPDATE categorization_rules SET target_category = :new WHERE target_category = :old")
            .bindparams(old=old, new=new)
        )
    purposes = ", ".join(f"'{p}'" for p in OLD_PURPOSES)
    for table in TABLES:
        op.execute(f"UPDATE {table} SET purpose = 'Shared' WHERE purpose IN ({purposes})")
        op.alter_column(table, "purpose", server_default="Shared")
    op.execute(f"UPDATE categorization_rules SET target_purpose = 'Shared' WHERE target_purpose IN ({purposes})")

    op.create_table(
        "categorization_examples",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("merchant_key", sa.String(255), nullable=False),
        sa.Column("merchant_raw", sa.String(255), nullable=False),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("spent_on", sa.Date(), nullable=True),
        sa.Column("trip", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_index("ix_categorization_examples_merchant_key", "categorization_examples", ["merchant_key"])


def downgrade() -> None:
    op.drop_index("ix_categorization_examples_merchant_key", table_name="categorization_examples")
    op.drop_table("categorization_examples")
    for table in TABLES:
        op.alter_column(table, "purpose", server_default="Personal")
        op.execute(f"UPDATE {table} SET purpose = 'Personal'")
    op.execute("DELETE FROM categorization_rules WHERE origin <> 'user'")
    op.drop_column("categorization_rules", "origin")

"""categories: what a category is for, beyond its name

Revision ID: 0022
Revises: 0021

estimated: an everyday necessity with no bill (groceries, fuel), whose usual monthly amount stands in for what
is still to come this month. subscriptions: new recurring charges here land in Bills with renewal reminders.
tipped: receipts for it are matched to a card charge that may include a tip (restaurants).

Existing installs get these on the starter categories that mean them, by their starter names; renaming a
category later keeps its switches.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STARTER = {"estimated": ("Grocery", "Gas & Parking"), "subscriptions": ("Subscriptions",), "tipped": ("Eating Out",)}


def upgrade() -> None:
    categories = sa.table("categories", sa.column("name"), *(sa.column(c) for c in STARTER))
    for column, names in STARTER.items():
        op.add_column("categories", sa.Column(column, sa.Boolean(), server_default="false", nullable=False))
        op.execute(categories.update().where(categories.c.name.in_(names)).values({column: True}))


def downgrade() -> None:
    for column in STARTER:
        op.drop_column("categories", column)

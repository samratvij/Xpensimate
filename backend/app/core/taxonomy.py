"""Two-tier taxonomy: Category = what the money was spent on, Purpose = who it was for.

Categories are editable and live in the categories table (app/services/categories.py); DefaultCategory is
the starting set. Purposes come from household.toml. The household's budget lines are derived from the
pair (see budget_line): shared spending is budgeted by category, personal and family spending by person.
"""
from typing import Literal, get_args

from app.core.household import PurposeDef, load_household

DefaultCategory = Literal[
    # Fixed
    "Rent + Parking",
    "Hydro",
    "Wifi + 5G",
    "Insurance",
    "Laundry",
    "Loan",
    # Variable
    "Grocery",
    "Eating Out",
    "Household",
    "Gas & Parking",
    "Car",
    "Entertainment",
    "Travel",
    "Gifting",
    "Health & Fitness",
    "Personal Care",
    "Shopping",
    "Subscriptions",
    "Government & Fees",
    "Transfer",
    "Misc",
    "One-time",
]

# The household's purposes and description come from household.toml (app/core/household.py).
_HOUSEHOLD = load_household()
HOUSEHOLD = _HOUSEHOLD.description
PURPOSE_DEFS: tuple[PurposeDef, ...] = _HOUSEHOLD.purposes

DEFAULT_CATEGORIES: tuple[str, ...] = get_args(DefaultCategory)
PURPOSES: tuple[str, ...] = tuple(p.name for p in PURPOSE_DEFS)
Purpose = Literal[PURPOSES]  # type: ignore[valid-type]

UNCATEGORIZED = "Uncategorized"
DEFAULT_PURPOSE = PURPOSES[0]

_BY_NAME = {p.name: p for p in PURPOSE_DEFS}
# Purposes budgeted by person rather than by category.
PERSONAL_PURPOSES: tuple[str, ...] = tuple(p.name for p in PURPOSE_DEFS if p.line)
# Budget line -> icon kind, for the lines that belong to a person or family rather than a category.
PERSON_LINES: dict[str, str] = {
    line: p.icon for p in PURPOSE_DEFS for line in (p.transfer_line, p.line) if line
}
# Names a category can't take, since they already mean something else.
RESERVED_NAMES = {UNCATEGORIZED, *PERSON_LINES}


def budget_lines(categories: list[str]) -> tuple[str, ...]:
    return (*categories, *PERSON_LINES)


def purpose_guide() -> str:
    """The purpose list as the categorizer prompt describes it."""
    return "\n".join(f'- "{p.name}": {p.guide}' for p in PURPOSE_DEFS)


def budget_line_condition(line: str, category_col, purpose_col):
    """SQL condition matching rows (transactions or receipt lines) that fall in a budget line; inverse of budget_line()."""
    from sqlalchemy import and_, not_

    for p in PURPOSE_DEFS:
        if line == p.transfer_line:
            return and_(purpose_col == p.name, category_col == "Transfer")
        if line == p.line:
            if p.transfer_line:
                return and_(purpose_col == p.name, category_col != "Transfer")
            return purpose_col == p.name
    return and_(category_col == line, not_(purpose_col.in_(PERSONAL_PURPOSES)))


def budget_line(category: str, purpose: str) -> str:
    """The budget line a (category, purpose) pair counts against (personal and family spending by person, shared spending by category)."""
    p = _BY_NAME.get(purpose)
    if p is None or p.line is None:
        return category
    if p.transfer_line and category == "Transfer":
        return p.transfer_line
    return p.line

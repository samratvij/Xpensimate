"""Two-tier taxonomy: Category = what the money was spent on, Purpose = who it was for.

Categories are editable and live in the categories table (app/services/categories.py); DefaultCategory is
the starting set. Purposes come from household.toml (app/core/household.py). The household's budget lines are derived from the
pair (see budget_line): shared spending is budgeted by category, personal and family spending by person.
"""
from typing import Literal, get_args

from app.core.household import SHARED, PurposeDef, load_household

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

DEFAULT_CATEGORIES: tuple[str, ...] = get_args(DefaultCategory)
UNCATEGORIZED = "Uncategorized"
# The shared purpose: always first, budgeted by category, and the default for anything not yet sorted.
DEFAULT_PURPOSE = SHARED

# Everything below reads the household as it is now (household.toml can change while the server runs).


def household_description() -> str:
    return load_household().description


def purpose_defs() -> tuple[PurposeDef, ...]:
    return load_household().purposes


def purposes() -> tuple[str, ...]:
    return tuple(p.name for p in purpose_defs())


def personal_purposes() -> tuple[str, ...]:
    """Purposes budgeted by person rather than by category."""
    return tuple(p.name for p in purpose_defs() if p.line)


def person_lines() -> dict[str, str]:
    """Budget line -> icon kind, for the lines that belong to a person or family rather than a category."""
    return {line: p.icon for p in purpose_defs() for line in (p.transfer_line, p.line) if line}


def reserved_names() -> set[str]:
    """Names a category can't take, since they already mean something else."""
    return {UNCATEGORIZED, *person_lines()}


def check_purpose(value: str) -> str:
    if value not in purposes():
        raise ValueError(f"Unknown purpose {value!r}; the household's are {', '.join(purposes())}")
    return value


def budget_lines(categories: list[str]) -> tuple[str, ...]:
    return (*categories, *person_lines())


def purpose_guide() -> str:
    """The purpose list as the categorizer prompt describes it."""
    return "\n".join(f'- "{p.name}": {p.guide}' for p in purpose_defs())


def budget_line_condition(line: str, category_col, purpose_col):
    """SQL condition matching rows (transactions or receipt lines) that fall in a budget line; inverse of budget_line()."""
    from sqlalchemy import and_, not_

    for p in purpose_defs():
        if line == p.transfer_line:
            return and_(purpose_col == p.name, category_col == "Transfer")
        if line == p.line:
            if p.transfer_line:
                return and_(purpose_col == p.name, category_col != "Transfer")
            return purpose_col == p.name
    return and_(category_col == line, not_(purpose_col.in_(personal_purposes())))


def budget_line(category: str, purpose: str) -> str:
    """The budget line a (category, purpose) pair counts against (personal and family spending by person, shared spending by category)."""
    p = next((d for d in purpose_defs() if d.name == purpose), None)
    if p is None or p.line is None:
        return category
    if p.transfer_line and category == "Transfer":
        return p.transfer_line
    return p.line

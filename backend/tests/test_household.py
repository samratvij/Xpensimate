import tomllib
from pathlib import Path

import pytest

from app.core.household import HouseholdConfigError, parse

EXAMPLE = Path(__file__).resolve().parents[1] / "household.example.toml"
SHARED = {"name": "Shared", "guide": "joint spending"}


def household(**overrides):
    return {"description": "two people", "purposes": [SHARED], **overrides}


def test_the_example_household_is_valid():
    example = parse(tomllib.loads(EXAMPLE.read_text()))
    assert example.purposes[0].name == "Shared"
    assert {a.id for a in example.accounts} >= {"tangerine_mastercard", "ws_credit"}


@pytest.mark.parametrize(("data", "message"), [
    (household(purposes=[]), "at least one purpose"),
    (household(purposes=[{**SHARED, "budget_line": "Shared"}]), "shared default"),
    (household(purposes=[SHARED, {"name": "Alex", "guide": "g", "icon": "robot"}]), "icon"),
    (household(purposes=[SHARED, {"name": "A", "guide": "g", "budget_line": "X"},
                         {"name": "B", "guide": "g", "budget_line": "X"}]), "budget lines"),
    (household(purposes=[SHARED, {"name": "A", "guide": "g", "transfer_budget_line": "T"}]), "needs a budget_line"),
    (household(accounts=[{"id": "a", "institution": "Bank", "name": "Card", "card_last4": 1234}]), "four digits"),
    (household(accounts=[{"id": "a", "institution": "Bank"}]), "'name' is required"),
    ({"purposes": [SHARED]}, "'description' is required"),
])
def test_mistakes_are_explained(data, message):
    with pytest.raises(HouseholdConfigError, match=message):
        parse(data)

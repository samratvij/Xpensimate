from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.household import load_household
from app.core.taxonomy import DEFAULT_CATEGORIES
from app.db.base import SessionLocal
from app.db.models import Account, CategorizationRule, CategoryRow


# (pattern, category). Generic Canadian merchants, all Shared; patterns match whole words against the
# normalized merchant. Household-specific rules are learned as you correct categories.
STARTER_RULES: list[tuple[str, str]] = [
    *[(p, "Grocery") for p in (
        "Metro", "Loblaws", "No Frills", "Food Basics", "Sobeys", "FreshCo", "Farm Boy", "Superstore",
        "Fortinos", "Zehrs", "Longos", "T&T", "Costco", "RCSS",
    )],
    *[(p, "Eating Out") for p in (
        "Tim Hortons", "Starbucks", "McDonalds", "Subway", "A&W", "Uber Eats", "UberEats", "DoorDash",
        "Skip The Dishes", "SkipTheDishes", "Pizza Pizza", "Chipotle", "Second Cup",
    )],
    *[(p, "Gas & Parking") for p in (
        "Presto", "Petro-Canada", "Shell", "Esso", "Pioneer", "Ultramar", "Green P",
        "Indigo Park", "PayByPhone",
    )],
    *[(p, "Health & Fitness") for p in ("Shoppers Drug Mart", "Rexall", "GoodLife")],
    *[(p, "Entertainment") for p in ("Cineplex", "LCBO")],
    *[(p, "Subscriptions") for p in ("Netflix", "Spotify", "Disney Plus", "Crave")],
    *[(p, "Wifi + 5G") for p in ("Rogers", "Bell Canada", "Fido", "Koodo", "Virgin Plus", "Freedom Mobile")],
    *[(p, "Hydro") for p in ("Toronto Hydro", "Hydro One", "Hydro-Quebec", "BC Hydro", "Enbridge", "FortisBC")],
    *[(p, "Household") for p in ("Canadian Tire", "Home Depot", "IKEA", "Dollarama", "Rona")],
    *[(p, "Shopping") for p in ("Amazon", "Best Buy", "Winners")],
    *[(p, "Travel") for p in ("Air Canada", "WestJet", "Porter Airlines", "Airbnb", "Expedia", "VIA Rail")],
    *[(p, "Government & Fees") for p in ("DriveTest", "ServiceOntario")],
]


def _starter_rules() -> list[dict]:
    rules = [
        {"merchant_pattern": pattern, "target_category": category, "target_purpose": "Shared",
         "priority": 1, "origin": "starter"}
        for pattern, category in STARTER_RULES
    ]
    # "Uber Eats" must beat any plain "Uber" rule.
    for rule in rules:
        if rule["merchant_pattern"] in ("Uber Eats", "UberEats"):
            rule["priority"] = 3
    return rules


def sync_accounts(db: Session) -> None:
    """household.toml is the source of truth for accounts: new ones are added and changes (a new card's
    digits, a rename) applied. Accounts removed from the file are kept, since charges point at them."""
    accounts = [
        {"id": a.id, "institution": a.institution, "account_name": a.name, "currency": a.currency,
         "card_last4": a.card_last4}
        for a in load_household().accounts
    ]
    if accounts:
        stmt = insert(Account).values(accounts)
        db.execute(stmt.on_conflict_do_update(index_elements=["id"], set_={
            "institution": stmt.excluded.institution, "account_name": stmt.excluded.account_name,
            "currency": stmt.excluded.currency, "card_last4": stmt.excluded.card_last4,
        }))


def seed(db: Session) -> None:
    sync_accounts(db)
    if db.scalar(select(CategoryRow.name).limit(1)) is None:
        db.add_all(CategoryRow(name=n, sort_order=i) for i, n in enumerate(DEFAULT_CATEGORIES))
    # Starter rules go in once; deleting some keeps them deleted (re-added only if all are gone).
    if db.scalar(select(CategorizationRule.id).where(CategorizationRule.origin == "starter").limit(1)) is None:
        db.add_all(CategorizationRule(**rule) for rule in _starter_rules())
    db.commit()


if __name__ == "__main__":
    with SessionLocal() as session:
        seed(session)

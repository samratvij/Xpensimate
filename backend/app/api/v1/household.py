"""The household's setup (people, purposes, accounts): read by the setup wizard and saved to household.toml."""
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.household import (
    KNOWN_ACCOUNTS,
    HouseholdConfigError,
    current_household,
    load_household,
    parse,
    save_household,
    to_dict,
)
from app.db.base import get_db
from app.db.models import (
    BudgetAlert,
    BudgetEnvelope,
    CategorizationExample,
    CategorizationRule,
    ReceiptItem,
    RecurringExpense,
    Transaction,
    TransactionItem,
)
from app.db.seed import sync_accounts
from app.services.categories import category_names

router = APIRouter(prefix="/household", tags=["household"])

# (column, what to call it) for every place a purpose is stored.
PURPOSE_COLUMNS = [
    (Transaction.purpose, "charge"),
    (TransactionItem.purpose, "split or receipt line"),
    (ReceiptItem.purpose, "receipt line"),
    (RecurringExpense.purpose, "bill"),
    (CategorizationRule.target_purpose, "rule"),
    (CategorizationExample.purpose, "past categorization"),
]


class PurposeIn(BaseModel):
    name: str
    # Left empty, the categorizer gets a description that fits the icon.
    guide: str | None = None
    budget_line: str | None = None
    transfer_budget_line: str | None = None
    icon: str = "person"


class AccountIn(BaseModel):
    id: str
    institution: str
    name: str
    currency: str | None = None  # the household's when not given
    card_last4: str | None = Field(None, description="Last four digits of the card, which picks the account for an alert")


class HouseholdIn(BaseModel):
    description: str
    currency: str = "CAD"
    purposes: list[PurposeIn]
    accounts: list[AccountIn] = []


class HouseholdOut(HouseholdIn):
    # True until the household is saved for the first time: the apps show the setup wizard.
    setup_needed: bool
    # Accounts the bank parsers know, for the wizard's bank picker.
    known_accounts: list[AccountIn]
    # Purpose -> how many charges use it (a purpose in use can't be removed).
    purposes_in_use: dict[str, int]


def _usage(db: Session) -> dict[str, int]:
    return dict(db.execute(select(Transaction.purpose, func.count()).group_by(Transaction.purpose)).all())


def _out(db: Session) -> HouseholdOut:
    saved = current_household()
    return HouseholdOut(
        **to_dict(saved or load_household()),
        setup_needed=saved is None,
        known_accounts=[AccountIn(**asdict(a)) for a in KNOWN_ACCOUNTS],
        purposes_in_use=_usage(db),
    )


@router.get("", response_model=HouseholdOut)
def get_household(db: Session = Depends(get_db)) -> HouseholdOut:
    return _out(db)


def _in_use(db: Session, purpose: str) -> list[str]:
    found = []
    for column, label in PURPOSE_COLUMNS:
        n = db.scalar(select(func.count()).where(column == purpose))
        if n:
            found.append(f"{n} {label}{'s' if n != 1 else ''}")
    return found


@router.put("", response_model=HouseholdOut)
def save(body: HouseholdIn, db: Session = Depends(get_db)) -> HouseholdOut:
    """Validate and save the household; budgets follow a person's budget line if it's renamed."""
    try:
        new = parse(body.model_dump())
    except HouseholdConfigError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    old = load_household()

    clashes = {line for p in new.purposes for line in (p.line, p.transfer_line) if line} & set(category_names(db))
    if clashes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"{', '.join(sorted(clashes))} is already a category; pick another budget line name")

    kept = {p.name for p in new.purposes}
    for p in old.purposes:
        if p.name not in kept and (uses := _in_use(db, p.name)):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"{p.name} can't be removed: it's used by {', '.join(uses)}. Change those first.")

    # A person whose budget line was renamed keeps their budget and alert history.
    new_by_name = {p.name: p for p in new.purposes}
    for p in old.purposes:
        q = new_by_name.get(p.name)
        if not q:
            continue
        for before, after in ((p.line, q.line), (p.transfer_line, q.transfer_line)):
            if before and after and before != after and db.get(BudgetEnvelope, after) is None:
                db.execute(update(BudgetEnvelope).where(BudgetEnvelope.category == before).values(category=after))
                db.execute(update(BudgetAlert).where(BudgetAlert.budget_line == before).values(budget_line=after))

    save_household(new)
    sync_accounts(db)
    db.commit()
    return _out(db)

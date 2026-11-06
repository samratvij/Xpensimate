"""The live category list, and renaming or merging a category everywhere it's stored."""
from fastapi import HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.taxonomy import DEFAULT_CATEGORIES, budget_lines, reserved_names
from app.db.models import (
    BudgetAlert,
    BudgetEnvelope,
    CategorizationExample,
    CategorizationRule,
    CategoryRow,
    ReceiptItem,
    RecurringExpense,
    Transaction,
    TransactionItem,
)

# (table, column) pairs that store a category name.
CATEGORY_COLUMNS = [
    (Transaction, Transaction.category),
    (TransactionItem, TransactionItem.category),
    (ReceiptItem, ReceiptItem.category),
    (CategorizationRule, CategorizationRule.target_category),
    (RecurringExpense, RecurringExpense.category),
    (CategorizationExample, CategorizationExample.category),
]


def category_names(db: Session) -> list[str]:
    names = list(db.scalars(select(CategoryRow.name).order_by(CategoryRow.sort_order, CategoryRow.name)))
    return names or list(DEFAULT_CATEGORIES)


def current_budget_lines(db: Session) -> tuple[str, ...]:
    return budget_lines(category_names(db))


def require_category(db: Session, name: str | None) -> None:
    """422 unless `name` is None (not being changed) or an existing category."""
    if name is not None and db.get(CategoryRow, name) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown category {name!r}")


def usage(db: Session) -> dict[str, int]:
    return dict(db.execute(select(Transaction.category, func.count()).group_by(Transaction.category)).all())


def validate_new_name(db: Session, name: str) -> str:
    name = " ".join(name.split())
    if not name or len(name) > 64:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A category name needs 1-64 characters")
    if "/" in name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A category name can't contain '/'")
    if name in reserved_names():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{name!r} is already a budget line for a person")
    if db.get(CategoryRow, name) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"There is already a category called {name!r}")
    return name


def add_category(db: Session, name: str) -> CategoryRow:
    name = validate_new_name(db, name)
    last = db.scalar(select(func.max(CategoryRow.sort_order))) or 0
    row = CategoryRow(name=name, sort_order=last + 1)
    db.add(row)
    db.commit()
    return row


def _move(db: Session, old: str, new: str) -> None:
    for model, column in CATEGORY_COLUMNS:
        db.execute(update(model).where(column == old).values({column.key: new}))


def rename_category(db: Session, old: str, new: str) -> CategoryRow:
    row = db.get(CategoryRow, old)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No category {old!r}")
    new = validate_new_name(db, new)
    _rename_everywhere(db, old, new)
    renamed = CategoryRow(name=new, sort_order=row.sort_order)
    db.delete(row)
    db.add(renamed)
    db.commit()
    return renamed


def merge_category(db: Session, old: str, into: str) -> None:
    """Move everything in `old` to `into`, then remove `old`."""
    if old == into:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick a different category to merge into")
    row = db.get(CategoryRow, old)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No category {old!r}")
    require_category(db, into)
    _move(db, old, into)
    old_budget = db.get(BudgetEnvelope, old)
    if old_budget is not None:
        if db.get(BudgetEnvelope, into) is None:
            db.execute(update(BudgetEnvelope).where(BudgetEnvelope.category == old).values(category=into))
        else:
            db.delete(old_budget)
    # Past alerts keep their old line name (merging could collide with the target's own alerts).
    db.execute(delete(CategoryRow).where(CategoryRow.name == old))
    db.commit()


# Tables whose rows are spending: a category they use can only be merged away, not dropped.
SPENDING_COLUMNS = [
    (Transaction, Transaction.category),
    (TransactionItem, TransactionItem.category),
    (ReceiptItem, ReceiptItem.category),
    (RecurringExpense, RecurringExpense.category),
]


def spending_uses(db: Session, name: str) -> int:
    return sum(db.scalar(select(func.count()).select_from(model).where(column == name)) or 0
               for model, column in SPENDING_COLUMNS)


def set_categories(db: Session, edits: list[tuple[str, str | None]]) -> None:
    """Make the category list exactly `edits`, in order: (name, original) pairs where original is the current
    name being kept or renamed, or None for a new category. Current categories left out are removed, which
    is only allowed while no spending uses them. All or nothing."""
    current = set(category_names(db))
    names = [" ".join(name.split()) for name, _ in edits]
    originals = [original for _, original in edits if original is not None]
    if not names:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Keep at least one category")
    for name in names:
        if not name or len(name) > 64:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A category name needs 1-64 characters")
        if "/" in name:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{name!r}: a category name can't contain '/'")
        if name in reserved_names():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{name!r} is already a budget line for a person")
    if len({n.casefold() for n in names}) != len(names):
        raise HTTPException(status.HTTP_409_CONFLICT, "Each category needs a different name")
    if len(set(originals)) != len(originals) or not set(originals) <= current:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown or repeated original category")
    removed = current - set(originals)
    for name in sorted(removed):
        if uses := spending_uses(db, name):
            raise HTTPException(status.HTTP_409_CONFLICT, f"{name} is used by {uses} charge(s) or bill(s): merge it "
                                                          "into another category instead of removing it")

    for name in removed:
        for model, column in CATEGORY_COLUMNS:
            db.execute(delete(model).where(column == name))
        db.execute(delete(BudgetEnvelope).where(BudgetEnvelope.category == name))
    # Renames go through temporary names, so two categories can swap names.
    renames = [(original, name) for name, (_, original) in zip(names, edits) if original and original != name]
    for i, (original, _) in enumerate(renames):
        _rename_everywhere(db, original, f"~renaming~{i}")
    for i, (_, name) in enumerate(renames):
        _rename_everywhere(db, f"~renaming~{i}", name)
    db.execute(delete(CategoryRow))
    db.flush()
    db.add_all(CategoryRow(name=name, sort_order=i) for i, name in enumerate(names))
    db.commit()


def _rename_everywhere(db: Session, old: str, new: str) -> None:
    # A shared category is its own budget line, so its budget and alert history follow the name.
    _move(db, old, new)
    db.execute(update(BudgetEnvelope).where(BudgetEnvelope.category == old).values(category=new))
    db.execute(update(BudgetAlert).where(BudgetAlert.budget_line == old).values(budget_line=new))

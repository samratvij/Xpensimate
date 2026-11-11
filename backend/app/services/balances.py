"""Balances (stocks): what each account holds, net worth, and how much of a change was contributed vs earned.

A balance snapshot is an account's balance on a day, from a screenshot of the bank app or typed in. An account's
balance for a month is its latest snapshot on or before the month's last day; a snapshot from an earlier month is
carried forward and marked stale. Credit card and loan balances are what is owed (positive) and count against net
worth; everything else counts for it.

For savings and investment accounts, the change between two snapshots splits into what was moved in or out
(transfers between own accounts in that window: contributions) and the rest, which is the market return (for
savings, interest). Market returns are never counted as saving (app/services/savings.py counts only flows).
"""
import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.accounts import INVESTMENT_KINDS, LIABILITY_KINDS, SAVINGS_KINDS
from app.core.config import get_settings
from app.db.models import Account, BalanceSnapshot, Transaction
from app.services.savings import TRANSFER

ZERO = Decimal("0.00")
# How the apps group accounts, in this order.
GROUPS = (
    ("savings", SAVINGS_KINDS),
    ("investments", INVESTMENT_KINDS),
    ("chequing", ("chequing",)),
    ("credit_cards", ("credit_card",)),
    ("loans", ("loan",)),
    ("other", ("asset",)),
)
# Accounts whose change splits into contributions and return.
GROWING_KINDS = (*SAVINGS_KINDS, *INVESTMENT_KINDS)


def group_of(kind: str) -> str:
    return next((name for name, kinds in GROUPS if kind in kinds), "other")


def month_end(month: str) -> date:
    year, mon = map(int, month.split("-"))
    return date(year, mon, calendar.monthrange(year, mon)[1])


def previous_month(month: str) -> str:
    year, mon = map(int, month.split("-"))
    return f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"


def latest_on_or_before(db: Session, day: date) -> dict[str, BalanceSnapshot]:
    """Each account's latest snapshot on or before `day`."""
    latest = (
        select(BalanceSnapshot.account_id, func.max(BalanceSnapshot.as_of).label("as_of"))
        .where(BalanceSnapshot.as_of <= day)
        .group_by(BalanceSnapshot.account_id)
        .subquery()
    )
    query = select(BalanceSnapshot).join(
        latest, (BalanceSnapshot.account_id == latest.c.account_id) & (BalanceSnapshot.as_of == latest.c.as_of)
    )
    return {s.account_id: s for s in db.scalars(query)}


def net_moved_in(db: Session, account_id: str, after: date, through: date) -> Decimal:
    """Money moved into an account from the household's other accounts, less money moved out, for local days
    after `after` up to and including `through`."""
    tz = ZoneInfo(get_settings().timezone)
    start = datetime.combine(after + timedelta(days=1), time.min, tzinfo=tz)
    end = datetime.combine(through + timedelta(days=1), time.min, tzinfo=tz)
    in_window = (Transaction.txn_type == TRANSFER, Transaction.timestamp >= start, Transaction.timestamp < end)
    moved_in = db.scalar(select(func.sum(Transaction.amount)).where(
        *in_window, Transaction.counterpart_account_id == account_id)) or ZERO
    moved_out = db.scalar(select(func.sum(Transaction.amount)).where(
        *in_window, Transaction.account_id == account_id)) or ZERO
    return moved_in - moved_out


def signed(kind: str, balance: Decimal) -> Decimal:
    """What a balance adds to net worth: owed amounts count against it."""
    return -balance if kind in LIABILITY_KINDS else balance


@dataclass
class AccountBalance:
    account: Account
    balance: Decimal | None
    as_of: date | None
    # The snapshot is from an earlier month, carried forward.
    stale: bool
    source: str | None
    previous_balance: Decimal | None = None
    previous_as_of: date | None = None
    change: Decimal | None = None
    contributions: Decimal | None = None
    market_return: Decimal | None = None


@dataclass
class MonthBalances:
    month: str
    as_of: date
    assets: Decimal
    liabilities: Decimal
    net_worth: Decimal
    previous_net_worth: Decimal | None
    groups: dict[str, Decimal]
    accounts: list[AccountBalance] = field(default_factory=list)
    # Accounts with no balance at all yet (not counted).
    missing: int = 0
    # Savings and investments: what was contributed vs earned over the month, where both ends are known.
    contributions: Decimal = ZERO
    market_return: Decimal = ZERO


def month_balances(db: Session, month: str) -> MonthBalances:
    end = month_end(month)
    start = date(end.year, end.month, 1)
    prev_end = month_end(previous_month(month))
    current, previous = latest_on_or_before(db, end), latest_on_or_before(db, prev_end)
    accounts = list(db.scalars(select(Account).order_by(Account.kind, Account.account_name)))

    rows, groups = [], {name: ZERO for name, _ in GROUPS}
    assets = liabilities = contributions = market_return = ZERO
    prev_total, have_prev, missing = ZERO, False, 0
    for account in accounts:
        snap, before = current.get(account.id), previous.get(account.id)
        if before is not None:
            prev_total += signed(account.kind, before.balance)
            have_prev = True
        if snap is None:
            missing += 1
            rows.append(AccountBalance(account, None, None, False, None))
            continue
        row = AccountBalance(account, snap.balance, snap.as_of, snap.as_of < start and snap.source != "static",
                             snap.source)
        groups[group_of(account.kind)] += snap.balance
        if account.kind in LIABILITY_KINDS:
            liabilities += snap.balance
        else:
            assets += snap.balance
        if before is not None:
            row.previous_balance, row.previous_as_of = before.balance, before.as_of
            row.change = snap.balance - before.balance
            if account.kind in GROWING_KINDS and snap.as_of > before.as_of:
                row.contributions = net_moved_in(db, account.id, before.as_of, snap.as_of)
                row.market_return = row.change - row.contributions
                contributions += row.contributions
                market_return += row.market_return
        rows.append(row)
    return MonthBalances(
        month=month, as_of=end, assets=assets, liabilities=liabilities, net_worth=assets - liabilities,
        previous_net_worth=prev_total if have_prev else None, groups=groups, accounts=rows, missing=missing,
        contributions=contributions, market_return=market_return,
    )


def save_snapshot(db: Session, account_id: str, as_of: date, balance: Decimal, source: str = "manual",
                  note: str | None = None, user: str | None = None) -> BalanceSnapshot:
    """Record (or replace) an account's balance on a day."""
    snap = db.scalar(select(BalanceSnapshot).where(BalanceSnapshot.account_id == account_id,
                                                   BalanceSnapshot.as_of == as_of))
    if snap is None:
        snap = BalanceSnapshot(account_id=account_id, as_of=as_of)
        db.add(snap)
    snap.balance, snap.source, snap.created_by = balance, source, user
    if note is not None:
        snap.note = note or None
    return snap

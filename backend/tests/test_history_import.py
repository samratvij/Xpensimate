import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.models import CategorizationExample, CategorizationRule, Transaction
from app.services.history_import import date_formats, parse_amount, read_table

TODAY = date.today()
OLD = TODAY - timedelta(days=120)
NEW = TODAY - timedelta(days=2)


def us(d: date) -> str:
    return d.strftime("%m/%d/%Y")


# A household budgeting spreadsheet (fictional), spending as positive dollars.
SHEET = f"""Date,Description,Amount,Category,Who,Notes
{us(OLD)},Loblaws,"$104.23",Groceries,Both,
{us(OLD + timedelta(days=7))},Loblaws,$88.10,Groceries,Both,
{us(OLD + timedelta(days=14))},Loblaws,$91.45,Groceries,Both,
{us(OLD + timedelta(days=3))},Barber & Co,$35.00,Haircut,Alex,Fade
{us(OLD + timedelta(days=5))},Uniqlo,(19.99),Clothing,Sam,Returned the socks
{us(OLD + timedelta(days=9))},Mystery Shop,$12.00,Weird Stuff,Nobody,
not a date,Typo Row,$5.00,Groceries,Both,
{us(NEW)},Pizza Pizza,$23.50,Dining,Both,Friday pizza
"""

MAPPING = {
    "date": "Date", "merchant": "Description", "amount": "Amount", "date_format": "%m/%d/%Y",
    "category": "Category", "who": "Who", "note": "Notes", "spending_is": "positive",
    "categories": {"Groceries": "Grocery", "Haircut": "Personal Care", "Clothing": "Shopping", "Dining": "Eating Out"},
    "who_values": {"Both": "Shared", "Alex": "Alex", "Sam": "Sam"},
}


def upload(client, path, content=SHEET, mapping=None):
    files = {"file": ("history.csv", content.encode(), "text/csv")}
    data = {"mapping": json.dumps(mapping)} if mapping is not None else None
    return client.post(f"/api/v1/history/{path}", files=files, data=data)


def live_charge(db, on: date):
    db.add(Transaction(source="test", source_ref=f"live:{on}", amount=Decimal("1.00"), merchant_raw="Anything",
                       timestamp=datetime.combine(on, datetime.min.time(), timezone.utc) + timedelta(hours=15)))
    db.commit()


@pytest.mark.parametrize(("text", "value"), [
    ("$1,234.56", "1234.56"), ("(19.99)", "-19.99"), ("-5", "-5"), ("12,34", "12.34"), ("1.234,50", "1234.50"),
    ("CA$ 7.00", "7.00"), ("", None), ("n/a", None),
])
def test_amounts_in_many_spellings(text, value):
    assert parse_amount(text) == (Decimal(value) if value else None)


def test_date_formats_flag_ambiguity():
    assert date_formats(["2026-03-04", "2026-12-31"]) == ["%Y-%m-%d"]
    assert set(date_formats(["03/04/2026", "05/06/2026"])) >= {"%m/%d/%Y", "%d/%m/%Y"}
    assert date_formats(["03/04/2026", "13/06/2026"]) == ["%d/%m/%Y"]


def test_semicolons_and_blank_lines_are_fine():
    table = read_table("Date;Payee;Amount\n\n2026-01-02;Cafe;4,50\n".encode())
    assert (table.headers, table.rows) == (["Date", "Payee", "Amount"], [{"Date": "2026-01-02", "Payee": "Cafe", "Amount": "4,50"}])


def test_preview_guesses_the_mapping(client):
    got = upload(client, "preview").json()
    assert got["row_count"] == 8
    assert {k: got["suggested"][k] for k in ("date", "merchant", "amount", "category", "who", "note")} == {
        "date": "Date", "merchant": "Description", "amount": "Amount", "category": "Category", "who": "Who",
        "note": "Notes"}
    assert (got["suggested"]["date_format"], got["suggested"]["spending_is"]) == ("%m/%d/%Y", "positive")
    assert got["category_guesses"]["Groceries"] == "Grocery" and got["category_guesses"]["Dining"] == "Eating Out"
    assert got["who_guesses"]["Both"] == "Shared" and got["who_guesses"]["Alex"] == "Alex"
    assert "Weird Stuff" not in got["category_guesses"]
    assert set(got["values"]["Category"]) >= {"Groceries", "Haircut"}


def test_import_teaches_and_adds_only_older_spending(client, db):
    live_charge(db, TODAY - timedelta(days=30))
    got = upload(client, "import", mapping=MAPPING).json()
    assert got["skipped"] == {"unreadable date": 1}
    assert (got["rows"], got["added_as_spending"]) == (7, 6)  # the pizza is after the first live charge
    # Loblaws was always groceries for both: a rule. A one-off isn't.
    rules = {r.merchant_pattern: r for r in db.scalars(select(CategorizationRule).where(CategorizationRule.origin == "history"))}
    assert (rules["Loblaws"].target_category, rules["Loblaws"].target_purpose) == ("Grocery", "Shared")
    assert "Barber & Co" not in rules
    added = {t.merchant_raw: t for t in db.scalars(select(Transaction).where(Transaction.source == "history"))}
    assert (added["Barber & Co"].category, added["Barber & Co"].purpose, added["Barber & Co"].note) == (
        "Personal Care", "Alex", "Fade")
    assert added["Uniqlo"].amount == Decimal("-19.99")  # a refund
    # Unknown category and "who" values: no category, shared.
    assert (added["Mystery Shop"].category, added["Mystery Shop"].purpose) == ("Uncategorized", "Shared")
    assert "Pizza Pizza" not in added
    assert db.scalar(select(func.count()).select_from(CategorizationExample)) == 7


def test_an_incoming_charge_that_is_an_imported_row_takes_its_category(client, db):
    live_charge(db, TODAY - timedelta(days=30))
    upload(client, "import", mapping=MAPPING)
    pizza = client.post("/api/v1/transactions/push", json={
        "timestamp": datetime.combine(NEW, datetime.min.time(), timezone.utc).isoformat().replace("00:00:00", "17:00:00"),
        "amount": "23.50", "merchant": "PIZZA PIZZA", "source_ref": "p1",
    }).json()
    assert (pizza["category"], pizza["purpose"]) == ("Eating Out", "Shared")


def test_spending_as_negative_numbers(client, db):
    sheet = "Date,Payee,Amount\n2026-01-05,Cafe,-4.50\n2026-01-06,Refund Co,10.00\n2026-01-07,Bakery,-6.25\n"
    mapping = {"date": "Date", "merchant": "Payee", "amount": "Amount", "date_format": "%Y-%m-%d", "spending_is": "negative"}
    assert upload(client, "preview", sheet).json()["suggested"]["spending_is"] == "negative"
    upload(client, "import", sheet, mapping)
    amounts = dict(db.execute(select(Transaction.merchant_raw, Transaction.amount)).all())
    assert amounts == {"Cafe": Decimal("4.50"), "Refund Co": Decimal("-10.00"), "Bakery": Decimal("6.25")}


def test_importing_again_replaces_and_delete_undoes(client, db):
    upload(client, "import", mapping=MAPPING)
    upload(client, "import", mapping=MAPPING)
    assert client.get("/api/v1/history").json()["added_as_spending"] == 7
    assert client.delete("/api/v1/history").status_code == 204
    assert client.get("/api/v1/history").json() == {
        "examples": 0, "rules": 0, "added_as_spending": 0, "first_live_charge": None}


def test_bad_files_and_mappings_are_explained(client):
    assert "header row" in upload(client, "preview", "Just one line").json()["detail"]
    wrong = {**MAPPING, "date_format": "%Y-%m-%d"}
    assert "date format" in upload(client, "import", mapping=wrong).json()["detail"]
    missing = {**MAPPING, "amount": "Price"}
    assert "amount column" in upload(client, "import", mapping=missing).json()["detail"]

from datetime import datetime, timezone
from decimal import Decimal

from app.db.models import Receipt, StatementImport, Transaction

NOW = datetime.now(timezone.utc)


def shift(month, by):
    y, m = map(int, month.split("-"))
    i = y * 12 + m - 1 + by
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


THIS = datetime.now().strftime("%Y-%m")


def add(db, amount, category="Grocery", purpose="Shared", month=THIS, day=10, account="pc_mastercard", **kw):
    ts = datetime.fromisoformat(f"{month}-{day:02d}T17:00:00+00:00")
    kw.setdefault("categorized_by", "rule")
    txn = Transaction(source="test", timestamp=ts, amount=Decimal(amount), merchant_raw=kw.pop("merchant", "LOBLAWS #1"),
                      category=category, purpose=purpose, account_id=account, **kw)
    db.add(txn)
    db.commit()
    return txn


def test_trends_cover_each_month_up_to_now(client, db):
    add(db, "100.00", month=shift(THIS, -2))
    add(db, "40.00", month=THIS)
    add(db, "25.00", category="Health & Fitness", purpose="Alex", month=THIS)
    body = client.get("/api/v1/trends", params={"months": 3}).json()
    assert [m["month"] for m in body] == [shift(THIS, -2), shift(THIS, -1), THIS]
    assert [m["spent"] for m in body] == [100.0, 0.0, 65.0]
    assert body[-1]["by_line"] == {"Grocery": 40.0, "Alex Personal": 25.0}
    alex = client.get("/api/v1/trends", params={"months": 1, "purpose": "Alex"}).json()
    assert alex[0]["spent"] == 25.0


def test_review_compares_months_and_lists_what_needs_a_look(client, db):
    client.put("/api/v1/budgets/Grocery", json={"monthly_limit": "500"})
    for k, amount in ((1, "300.00"), (2, "200.00"), (3, "100.00")):
        add(db, amount, month=shift(THIS, -k))
    add(db, "250.00")
    guess = add(db, "60.00", category="Shopping", merchant="ZXQ HOLDINGS", categorized_by="llm",
                inference_confidence=Decimal("0.40"))
    add(db, "70.00", category="Shopping", merchant="SURE THING", categorized_by="llm", inference_confidence=Decimal("0.90"))
    add(db, "999.00", excluded=True, categorized_by="none")
    db.add(StatementImport(filename="s.csv", bank_format="pcf", account_id="pc_mastercard", status="done", rows=[],
                           period_start=datetime.fromisoformat(f"{THIS}-01").date(),
                           period_end=datetime.fromisoformat(f"{THIS}-20").date()))
    db.add(Receipt(merchant="Loblaws", timestamp=NOW, total=Decimal("5.00")))
    db.commit()

    body = client.get("/api/v1/review", params={"month": THIS}).json()
    assert (body["total_spent"], body["previous_total"], body["average_3m"]) == (380.0, 300.0, 200.0)
    grocery = next(line for line in body["lines"] if line["budget_line"] == "Grocery")
    assert (grocery["spent"], grocery["monthly_limit"], grocery["previous"], grocery["average_3m"]) == (250.0, 500.0, 300.0, 200.0)
    assert [t["id"] for t in body["needs_check"]] == [str(guess.id)]
    pc = next(a for a in body["accounts"] if a["account_id"] == "pc_mastercard")
    assert (pc["transactions"], pc["statement_imported"]) == (3, True)
    assert body["top_merchants"][0] == {"merchant": "Loblaws", "spent": 250.0, "count": 1}
    assert body["unmatched_receipts"] == 1

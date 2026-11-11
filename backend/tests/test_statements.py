from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.db.models import CategorizationExample, Transaction
from app.statements.parsers import INTEREST, PAYMENT, PURCHASE, REFUND, parse_statement, source_refs
from tests.fixtures import statements as csvs

TZ = ZoneInfo("America/Toronto")


def preview(client, text, name="statement.csv", **form):
    resp = client.post("/api/v1/statements/preview", files={"file": (name, text.encode(), "text/csv")}, data=form)
    assert resp.status_code == 201, resp.text
    return resp.json()


def by_merchant(body):
    return {r["merchant"]: r for r in body["rows"]}


def add_alert(db, merchant, amount, day, account, source="gmail_pubsub", hour=15):
    txn = Transaction(source=source, source_ref=f"{merchant}-{day}", account_id=account,
                      timestamp=datetime.fromisoformat(f"{day}T{hour:02d}:00:00").replace(tzinfo=TZ),
                      amount=Decimal(amount), merchant_raw=merchant, category="Misc", purpose="Shared")
    db.add(txn)
    db.commit()
    return txn


@pytest.mark.parametrize(
    ("text", "fmt", "account", "kinds"),
    [
        (csvs.CTM, "ctm", "ctfs_triangle", [PURCHASE, PURCHASE]),
        (csvs.PCF, "pcf", "pc_mastercard", [PURCHASE, PURCHASE, PAYMENT, REFUND, INTEREST, PURCHASE]),
        (csvs.TANGERINE, "tangerine", "tangerine_mastercard", [PURCHASE, PURCHASE, PAYMENT]),
        (csvs.WEALTHSIMPLE, "wealthsimple", "ws_credit", [PURCHASE, PURCHASE, PAYMENT, REFUND]),
    ],
)
def test_formats_are_detected(text, fmt, account, kinds):
    parsed = parse_statement(text.encode())
    assert (parsed.bank_format, parsed.account_id, [r.kind for r in parsed.rows]) == (fmt, account, kinds)


def test_signs_are_normalized_to_spending_positive():
    pcf = {r.merchant: r for r in parse_statement(csvs.PCF.encode()).rows}
    assert pcf["CINEPLEX CINECLUB"].amount == Decimal("12.42")
    assert pcf["SHOPPERS DRUG MART #06"].amount == Decimal("-13.87")
    assert pcf["Pymt Money Account 9012"].amount == Decimal("-420.00")
    assert pcf["PURCHASE INTEREST CHARGE"].amount == Decimal("12.60")
    assert pcf["MAPLE DENTAL CENTRE"].at.strftime("%H:%M") == "15:43"
    ctm = parse_statement(csvs.CTM.encode()).rows
    assert (ctm[0].amount, ctm[0].ref, ctm[0].bank_category) == (Decimal("65.88"), "10000000000000000000001", "Entertainment")
    tangerine = parse_statement(csvs.TANGERINE.encode()).rows
    assert tangerine[0].bank_category == "Restaurant"
    wealthsimple = parse_statement(csvs.WEALTHSIMPLE.encode()).rows
    assert [r.merchant for r in wealthsimple] == ["Corner Creamery Toronto", "Rogers ******0000", "Payment", "Sports Outlet Midtown"]


def test_identical_rows_get_distinct_stable_refs():
    rows = parse_statement((csvs.TANGERINE + "10/23/2026,DEBIT,TIM HORTONS #1000 TORONTO ON,,-11.82\n").encode()).rows
    refs = source_refs("tangerine_mastercard", rows)
    assert len(set(refs)) == len(refs)
    assert refs == source_refs("tangerine_mastercard", rows)


def test_unknown_format_is_rejected(client):
    resp = client.post("/api/v1/statements/preview", files={"file": ("x.csv", b"a,b,c\n1,2,3\n", "text/csv")})
    assert resp.status_code == 422 and "Unrecognized statement format" in resp.json()["detail"]


def test_preview_matches_adds_pairs_payments_and_lists_missing(client, db):
    seen = add_alert(db, "CINEPLEX CINECLUB", "12.42", "2026-10-11", "pc_mastercard")  # alert a day later
    paid_by = add_alert(db, "Unknown merchant", "420.00", "2026-10-01", "pc_money")
    stray = add_alert(db, "DOORDASH", "25.00", "2026-10-12", "pc_mastercard")

    body = preview(client, csvs.PCF, month="2026-10")
    rows = by_merchant(body)
    assert body["counts"] == {"match": 1, "add": 3, "payment": 1, "outside": 1}
    assert rows["CINEPLEX CINECLUB"]["transaction"]["id"] == str(seen.id)
    assert rows["Pymt Money Account 9012"]["pays"]["id"] == str(paid_by.id)
    assert rows["LOBLAWS 1000"]["action"] == "outside"
    assert [m["id"] for m in body["missing"]] == [str(stray.id)]
    assert (body["period_start"], body["period_end"]) == ("2026-10-01", "2026-10-20")
    # Nothing changes until commit.
    assert db.scalar(select(Transaction).where(Transaction.source == "statement")) is None


def test_commit_adds_categorizes_and_excludes_payments(client, db):
    seen = add_alert(db, "CINEPLEX CINECLUB", "12.42", "2026-10-11", "pc_mastercard")
    paid_by = add_alert(db, "Unknown merchant", "420.00", "2026-10-01", "pc_money")
    # The household's imported history has this exact dental visit.
    db.add(CategorizationExample(merchant_key="Maple Dental Centre", merchant_raw="MAPLE DENTAL CENTRE",
                                 category="Health & Fitness", purpose="Alex", amount=Decimal("-32.70"),
                                 spent_on=datetime(2026, 10, 2).date()))
    db.commit()

    body = preview(client, csvs.PCF, month="2026-10")
    done = client.post(f"/api/v1/statements/{body['id']}/commit", json={})
    assert done.status_code == 202
    status = client.get(f"/api/v1/statements/{body['id']}").json()
    assert status["status"] == "done"
    assert status["result"] == {"matched": 1, "added": 3, "payment": 1, "skipped": 1}

    db.expire_all()
    added = {t.merchant_raw: t for t in db.scalars(select(Transaction).where(Transaction.source == "statement"))}
    dental = added["MAPLE DENTAL CENTRE"]
    assert (dental.category, dental.purpose, dental.categorized_by) == ("Health & Fitness", "Alex", "sheet")
    assert dental.timestamp.astimezone(TZ).strftime("%Y-%m-%d %H:%M") == "2026-10-02 15:43"
    assert added["SHOPPERS DRUG MART #06"].amount == Decimal("-13.87")
    assert added["Pymt Money Account 9012"].excluded is True
    assert db.get(Transaction, paid_by.id).excluded is True
    assert db.get(Transaction, seen.id).statement_import_id is not None
    assert [i["status"] for i in client.get("/api/v1/statements").json()] == ["done"]


def test_reimporting_the_same_statement_adds_nothing(client):
    first = preview(client, csvs.TANGERINE)
    client.post(f"/api/v1/statements/{first['id']}/commit", json={})
    again = preview(client, csvs.TANGERINE)
    assert again["counts"] == {"imported": 3}
    assert client.post(f"/api/v1/statements/{first['id']}/commit", json={}).status_code == 409


def test_identical_charges_are_unclear_until_decided(client, db):
    a = add_alert(db, "TIM HORTONS", "11.82", "2026-10-23", "tangerine_mastercard", hour=9)
    b = add_alert(db, "TIM HORTONS", "11.82", "2026-10-23", "tangerine_mastercard", source="android_push", hour=10)
    body = preview(client, csvs.TANGERINE)
    tim = by_merchant(body)["TIM HORTONS #1000 TORONTO ON"]
    assert tim["action"] == "unclear"
    assert {c["id"] for c in tim["candidates"]} == {str(a.id), str(b.id)}

    url = f"/api/v1/statements/{body['id']}/commit"
    assert client.post(url, json={}).status_code == 422
    assert client.post(url, json={"decisions": {str(tim["index"]): "match:not-a-candidate"}}).status_code == 422
    assert client.post(url, json={"decisions": {str(tim["index"]): f"match:{b.id}"}}).status_code == 202
    db.expire_all()
    assert db.get(Transaction, b.id).statement_import_id is not None
    assert db.get(Transaction, a.id).statement_import_id is None


def test_account_can_be_overridden(client):
    body = preview(client, csvs.TANGERINE, account_id="tangerine_cheq")
    assert body["account_id"] == "tangerine_cheq"
    resp = client.post("/api/v1/statements/preview", files={"file": ("s.csv", csvs.TANGERINE.encode(), "text/csv")},
                       data={"account_id": "nope"})
    assert resp.status_code == 422


def test_import_page_is_served_without_a_token():
    from fastapi.testclient import TestClient

    from main import app
    resp = TestClient(app).get("/import")
    assert resp.status_code == 200 and "Import a bank statement" in resp.text


# --- RBC PDF -----------------------------------------------------------------------------------------

from app.statements.pdf import parse_rbc_text  # noqa: E402
from tests.pdf_maker import make_pdf  # noqa: E402

RBC_LEFT = [
    "Royal Bank RBC ION Visa",
    "STATEMENT FROM DEC 16, 2025 TO JAN 15, 2026",
    "PREVIOUS ACCOUNT BALANCE -$5.39",
    "TRANSACTION POSTING ACTIVITY DESCRIPTION AMOUNT ($)",
    "DEC 20 DEC 22 AMZN MKTP CA*5L8KA77S0 866-216-1072 ON $40.67",
    "74537886258401717306805",
    "JAN 03 JAN 04 PAYMENT - THANK YOU / PAIEMENT - MERCI -$35.28",
    "74537886258401717306806",
    "JAN 05 JAN 06 BEST BUY #123 TORONTO ON",
    "74537886258401717306807",
    "-$12.00",
    "TOTAL ACCOUNT BALANCE $35.28",
]
RBC_RIGHT = ["CONTACT US", "Customer Service 1-800-769-2512", "Minimum payment $10.00", "NEW BALANCE $35.28"]


def test_rbc_text_rows_years_and_kinds():
    rows = parse_rbc_text("\n".join(RBC_LEFT)).rows
    assert [(r.spent_on.isoformat(), r.kind, str(r.amount)) for r in rows] == [
        ("2025-12-20", "purchase", "40.67"), ("2026-01-03", "payment", "-35.28"), ("2026-01-05", "refund", "-12.00"),
    ]
    assert rows[0].merchant == "AMZN MKTP CA*5L8KA77S0 866-216-1072 ON"
    assert rows[0].ref == "74537886258401717306805"
    assert rows[2].merchant == "BEST BUY #123 TORONTO ON"


def test_rbc_pdf_preview_ignores_the_side_panel(client):
    pdf = make_pdf(RBC_LEFT, RBC_RIGHT)
    resp = client.post("/api/v1/statements/preview", files={"file": ("visa.pdf", pdf, "application/pdf")})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["account_id"], body["counts"]) == ("rbc_visa", {"add": 2, "payment": 1})
    assert [r["merchant"] for r in body["rows"]][0] == "AMZN MKTP CA*5L8KA77S0 866-216-1072 ON"


def test_other_pdfs_are_rejected(client):
    pdf = make_pdf(["Some other bank statement", "JAN 03 JAN 04 THING $1.00"])
    resp = client.post("/api/v1/statements/preview", files={"file": ("x.pdf", pdf, "application/pdf")})
    assert resp.status_code == 422 and "Only RBC" in resp.json()["detail"]


def test_payment_reversal_is_not_spending(client, db):
    text = csvs.PCF + '"Pre-Auth Payment Reversal","PURCHASE","ALEX","10/21/2026","12:00 AM","-1042.74"\n'
    reversal = parse_statement(text.encode()).rows[-1]
    assert (reversal.kind, reversal.amount) == (PAYMENT, Decimal("1042.74"))
    body = preview(client, text, month="2026-10")
    client.post(f"/api/v1/statements/{body['id']}/commit", json={})
    db.expire_all()
    stored = db.scalar(select(Transaction).where(Transaction.merchant_raw == "Pre-Auth Payment Reversal"))
    assert (stored.excluded, stored.categorized_by, stored.category) == (True, "statement", "Uncategorized")


# Screenshots of the bank app

def _png() -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (40, 80), "white").save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def screenshot(monkeypatch):
    """Upload a screenshot whose reading is `read` (account_id and rows as the model would return them)."""
    from app.core.config import get_settings
    from app.statements import screenshot as module

    def upload(client, guess, rows, is_list=True, **form):
        monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
        seen = {}

        def fake(jpeg, accounts, today):
            seen["accounts"] = [a.id for a in accounts]
            return module.ScreenshotRead(is_transaction_list=is_list, account_id=guess,
                                         rows=[module.ScreenshotRow(**r) for r in rows])

        monkeypatch.setattr(module, "read_screenshot", fake)
        resp = client.post("/api/v1/statements/preview", files={"file": ("shot.png", _png(), "image/png")}, data=form)
        # Categorizing what gets added stays offline.
        monkeypatch.setattr(get_settings(), "anthropic_api_key", "")
        return resp

    return upload


def _row(date, merchant, amount, kind="purchase", pending=False):
    return {"date": date, "merchant": merchant, "amount": amount, "kind": kind, "pending": pending}


def test_screenshot_is_read_and_added_under_its_own_source(client, db, screenshot):
    alert = add_alert(db, "NORTHSIDE BOXING", "75.40", "2026-11-04", "ws_credit")
    resp = screenshot(client, "ws_credit", [
        _row("2026-11-04", "Northside Boxing", 75.40),
        _row("2026-11-03", "#32-153 Nepean Library", 2.78),
        _row("2026-11-05", "Shell Ep On C41503", 40.00, pending=True),
        _row("2026-10-30", "Payment", 500, kind="payment"),
    ], month="2026-09")  # the month is ignored for screenshots
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["bank_format"], body["account_id"], body["missing"]) == ("screenshot", "ws_credit", [])
    rows = by_merchant(body)
    assert rows["Northside Boxing"]["action"] == "match" and rows["Northside Boxing"]["transaction"]["id"] == str(alert.id)
    assert (rows["#32-153 Nepean Library"]["action"], rows["#32-153 Nepean Library"]["amount"]) == ("add", "2.78")
    assert (rows["Payment"]["action"], rows["Payment"]["amount"]) == ("payment", "-500.00")
    assert (rows["Shell Ep On C41503"]["action"], rows["Shell Ep On C41503"]["pending"]) == ("add", True)

    client.post(f"/api/v1/statements/{body['id']}/commit", json={})
    db.expire_all()
    added = db.scalar(select(Transaction).where(Transaction.merchant_raw == "#32-153 Nepean Library"))
    assert (added.source, added.account_id) == ("screenshot", "ws_credit")
    # A screenshot doesn't mark the month as covered by a statement.
    assert client.get("/api/v1/statements").json()[0]["bank_format"] == "screenshot"


def test_overlapping_screenshot_and_later_statement_add_nothing_twice(client, db, screenshot):
    first = screenshot(client, "ws_credit", [_row("2026-10-27", "Corner Creamery", 9.29)]).json()
    client.post(f"/api/v1/statements/{first['id']}/commit", json={})
    # Read again, transcribed a little differently: still the same charge.
    again = screenshot(client, "ws_credit", [_row("2026-10-27", "Corner Creamery Toronto", 9.29)]).json()
    assert by_merchant(again)["Corner Creamery Toronto"]["action"] == "match"
    # The full statement later confirms it instead of adding it again.
    statement = by_merchant(preview(client, csvs.WEALTHSIMPLE))
    assert statement["Corner Creamery Toronto"]["action"] == "match"
    assert statement["Corner Creamery Toronto"]["transaction"]["source"] == "screenshot"


def test_screenshot_account_must_be_known(client, screenshot):
    rows = [_row("2026-11-04", "WALMART.CA", 37.78)]
    resp = screenshot(client, "unknown", rows)
    assert resp.status_code == 422 and "Pick the account" in resp.json()["detail"]
    assert screenshot(client, "unknown", rows, account_id="rbc_visa").json()["account_id"] == "rbc_visa"


def test_screenshot_that_isnt_a_transaction_list_is_rejected(client, screenshot):
    resp = screenshot(client, "unknown", [], is_list=False)
    assert resp.status_code == 422 and "doesn't look like" in resp.json()["detail"]


def test_screenshot_needs_the_api_key(client):
    resp = client.post("/api/v1/statements/preview", files={"file": ("shot.png", _png(), "image/png")})
    assert resp.status_code == 503


# --- holds the bank replaced (an order that changed) ------------------------------------------------

def test_a_hold_replaced_by_the_final_charge_is_excluded_on_import(client, db):
    # DoorDash fixed a botched order: alerts for $25 and then $10.42; the statement has only the $10.42.
    hold = add_alert(db, "DoorDash", "25.00", "2026-10-23", "tangerine_mastercard", hour=18)
    final = add_alert(db, "DoorDash", "10.42", "2026-10-23", "tangerine_mastercard", source="android_push", hour=20)
    text = csvs.TANGERINE + "10/23/2026,DEBIT,DOORDASH*MCDONALDS TORONTO ON,,-10.42\n"

    body = preview(client, text)
    missing = {m["id"]: m for m in body["missing"]}
    assert missing[str(hold.id)]["replaced_by"]["amount"] == "10.42"
    assert by_merchant(body)["DOORDASH*MCDONALDS TORONTO ON"]["transaction"]["id"] == str(final.id)

    client.post(f"/api/v1/statements/{body['id']}/commit", json={})
    assert client.get(f"/api/v1/statements/{body['id']}").json()["result"]["replaced"] == 1
    db.expire_all()
    assert db.get(Transaction, hold.id).excluded is True
    assert db.get(Transaction, final.id).excluded is False


def test_a_replaced_hold_can_be_kept_and_needs_a_later_charge(client, db):
    # Walmart: order changed after it was placed; the final charge came two days later with no alert.
    hold = add_alert(db, "WALMART.CA", "100.00", "2026-10-20", "tangerine_mastercard")
    earlier = add_alert(db, "WALMART.CA", "40.00", "2026-10-26", "tangerine_mastercard")  # after the statement's charge
    other = add_alert(db, "SPORT CHEK", "60.00", "2026-10-21", "tangerine_mastercard")
    text = csvs.TANGERINE + "10/22/2026,DEBIT,WAL-MART #1061 OTTAWA ON,,-75.12\n"

    body = preview(client, text)
    missing = {m["id"]: m for m in body["missing"]}
    assert missing[str(hold.id)]["replaced_by"]["merchant"] == "WAL-MART #1061 OTTAWA ON"
    assert "replaced_by" not in missing[str(other.id)]
    assert str(earlier.id) not in missing  # outside the statement's period

    assert client.post(f"/api/v1/statements/{body['id']}/commit", json={"keep": [str(other.id)]}).status_code == 422
    client.post(f"/api/v1/statements/{body['id']}/commit", json={"keep": [str(hold.id)]})
    db.expire_all()
    assert db.get(Transaction, hold.id).excluded is False
    assert "replaced" not in client.get(f"/api/v1/statements/{body['id']}").json()["result"]


def test_skipping_the_final_charge_keeps_the_hold(client, db):
    hold = add_alert(db, "DoorDash", "25.00", "2026-10-23", "tangerine_mastercard")
    body = preview(client, csvs.TANGERINE + "10/23/2026,DEBIT,DOORDASH TORONTO ON,,-10.42\n")
    final = next(r for r in body["rows"] if r["merchant"].startswith("DOORDASH"))
    assert final["action"] == "add"
    client.post(f"/api/v1/statements/{body['id']}/commit", json={"decisions": {str(final["index"]): "skip"}})
    db.expire_all()
    assert db.get(Transaction, hold.id).excluded is False

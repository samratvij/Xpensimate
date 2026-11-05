import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from PIL import Image
from sqlalchemy import select

from app.api.v1 import receipts as receipts_api
from app.core.config import get_settings
from app.db.models import Receipt, ReceiptItem, Transaction
from app.services.ingest import NewTransaction, ingest_transaction
from app.services.ocr_service import OcrLine, OcrReceipt
from app.services.reconciler import allocate, try_match_receipt

T0 = datetime(2026, 11, 2, 23, 45, tzinfo=timezone.utc)  # 18:45 Toronto


def add_receipt(db, *, merchant="Loblaws", total="14.14", ts=T0, lines=None):
    lines = lines or [("Milk", "Grocery", "Shared", "6.49"), ("Bananas", "Grocery", "Shared", "2.00"),
                      ("Batteries", "Household", "Shared", "6.00"), ("Price match", "Grocery", "Shared", "-1.00")]
    receipt = Receipt(merchant=merchant, timestamp=ts, total=Decimal(total), tax=Decimal("0.65"), items=[
        ReceiptItem(position=i, sku_name=name, category=cat, purpose=pur, price=Decimal(price))
        for i, (name, cat, pur, price) in enumerate(lines)
    ])
    db.add(receipt)
    db.commit()
    return receipt


def add_txn(db, *, amount="14.14", merchant="LOBLAWS #1234", ts=T0 + timedelta(hours=1), **extra):
    txn, _ = ingest_transaction(db, NewTransaction(
        source=extra.pop("source", "test"), source_ref=extra.pop("source_ref", None), account_id=None,
        timestamp=ts, amount=Decimal(amount), merchant=merchant, reconciliation_status="PENDING_MATCH",
    ), raw_payload={})
    for key, value in extra.items():
        setattr(txn, key, value)
    db.commit()
    return txn


def items_total(txn):
    return sum((i.price for i in txn.items), Decimal("0"))


def test_allocate_spreads_tax_and_keeps_exact_total():
    lines = allocate([Decimal("6.49"), Decimal("2.00"), Decimal("6.00"), Decimal("-1.00")], Decimal("14.14"))
    assert sum(lines) == Decimal("14.14")
    assert lines[3] < 0  # discounts stay discounts
    assert allocate([Decimal("10.00")], Decimal("11.30")) == [Decimal("11.30")]


def test_transaction_arriving_after_receipt_is_matched(db):
    receipt = add_receipt(db)
    txn = add_txn(db, merchant="LOBLAWS #1234 TORONTO ON")
    db.refresh(receipt)
    assert receipt.matched_transaction_id == txn.id
    assert (txn.is_itemized, txn.reconciliation_status) == (True, "RECONCILED")
    assert [i.sku_name for i in txn.items] == ["Milk", "Bananas", "Batteries", "Price match"]
    assert items_total(txn) == txn.amount
    assert (txn.category, txn.purpose) == ("Grocery", "Shared")


def test_receipt_arriving_after_transaction_is_matched(db):
    txn = add_txn(db)
    receipt = add_receipt(db)
    assert try_match_receipt(db, receipt).id == txn.id


def test_amount_within_five_cents_matches(db):
    add_receipt(db)
    assert add_txn(db, amount="14.18").is_itemized


@pytest.mark.parametrize(("amount", "ts"), [("14.25", T0), ("14.14", T0 + timedelta(hours=73))])
def test_no_match_outside_tolerance_or_window(db, amount, ts):
    add_receipt(db)
    assert not add_txn(db, amount=amount, ts=ts).is_itemized


def test_restaurant_tip_matches_with_similar_merchant(db):
    add_receipt(db, merchant="Shawarma House", total="40.00", lines=[("Platter", "Eating Out", "Shared", "35.40")])
    txn = add_txn(db, amount="46.00", merchant="SHAWARMA HOUSE TORONTO ON")
    assert txn.is_itemized
    assert items_total(txn) == Decimal("46.00")


def test_tip_needs_a_restaurant_receipt(db):
    add_receipt(db, total="40.00")
    assert not add_txn(db, amount="46.00", merchant="LOBLAWS").is_itemized


def test_tip_needs_merchant_agreement(db):
    add_receipt(db, merchant="Shawarma House", total="40.00", lines=[("Platter", "Eating Out", "Shared", "35.40")])
    assert not add_txn(db, amount="46.00", merchant="PETRO-CANADA").is_itemized


def test_two_identical_charges_are_left_for_a_human(db):
    first = add_txn(db, merchant="DOLLARAMA")
    second = add_txn(db, merchant="DOLLARAMA", ts=T0 + timedelta(hours=2))
    receipt = add_receipt(db, merchant="Dollarama")
    assert try_match_receipt(db, receipt) is None
    assert {first.is_itemized, second.is_itemized} == {False}


def test_merchant_name_breaks_a_tie(db):
    add_txn(db, merchant="PETRO-CANADA")
    loblaws = add_txn(db, merchant="LOBLAWS #12 TORONTO ON", ts=T0 + timedelta(hours=2))
    assert try_match_receipt(db, add_receipt(db)).id == loblaws.id


def test_manual_transaction_purpose_flows_to_lines(db):
    txn = add_txn(db, purpose="Parents", purpose_is_manual=True)
    assert try_match_receipt(db, add_receipt(db)).id == txn.id
    assert {i.purpose for i in txn.items} == {"Parents"}
    assert txn.purpose == "Parents"


def test_refunds_are_not_matched(db):
    add_receipt(db)
    assert not add_txn(db, amount="-14.14").is_itemized


# --- API -------------------------------------------------------------------------------------------

def jpeg():
    out = io.BytesIO()
    Image.new("RGB", (400, 600), "white").save(out, format="JPEG")
    return out.getvalue()


@pytest.fixture
def ocr(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr(get_settings(), "receipts_dir", str(tmp_path))
    result = OcrReceipt(is_receipt=True, merchant="Loblaws", purchase_date="2026-11-02", purchase_time="18:45",
                        subtotal=13.49, tax=0.65, total=14.14, lines=[
                            OcrLine(raw_text="MILK", name="Milk", quantity=1, line_total=6.49, category="Grocery",
                                    purpose="Shared"),
                            OcrLine(raw_text="LIPSTICK", name="Lipstick", quantity=1, line_total=7.00,
                                    category="Personal Care", purpose="Sam"),
                        ])
    monkeypatch.setattr(receipts_api, "extract_receipt", lambda *_: result)


def upload(client):
    resp = client.post("/api/v1/receipts/ocr", files={"file": ("r.jpg", jpeg(), "image/jpeg")})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_upload_matches_pending_bank_alert_and_splits_budget_lines(client, db, ocr):
    txn = add_txn(db, merchant="LOBLAWS #1234 TORONTO ON")
    receipt = upload(client)
    assert receipt["matched_transaction_id"] == str(txn.id)

    detail = client.get(f"/api/v1/transactions/{txn.id}").json()
    assert [(i["sku_name"], i["price"], i["budget_line"]) for i in detail["items"]] == [
        ("Milk", 6.80, "Grocery"), ("Lipstick", 7.34, "Sam Personal"),
    ]
    lines = {r["category"]: r["spent"] for r in client.get("/api/v1/summary", params={"month": "2026-11"}).json()["categories"]}
    assert lines == {"Grocery": 6.80, "Sam Personal": 7.34}


def test_candidates_manual_match_and_unmatch(client, db, ocr):
    first = add_txn(db, merchant="LOBLAWS")
    second = add_txn(db, merchant="LOBLAWS", ts=T0 + timedelta(hours=3))
    receipt = upload(client)
    assert receipt["matched_transaction_id"] is None

    candidates = client.get(f"/api/v1/receipts/{receipt['id']}/candidates").json()
    assert [c["transaction"]["id"] for c in candidates] == [str(first.id), str(second.id)]

    matched = client.post(f"/api/v1/receipts/{receipt['id']}/match", json={"transaction_id": str(second.id)}).json()
    assert matched["matched_transaction_id"] == str(second.id)
    again = client.post(f"/api/v1/receipts/{receipt['id']}/match", json={"transaction_id": str(first.id)})
    assert again.status_code == 409

    client.post(f"/api/v1/receipts/{receipt['id']}/unmatch")
    restored = client.get(f"/api/v1/transactions/{second.id}").json()
    assert (restored["is_itemized"], restored["reconciliation_status"], restored["items"]) == (
        False, "PENDING_MATCH", [],
    )


def test_deleting_matched_receipt_restores_transaction(client, db, ocr):
    txn = add_txn(db, merchant="LOBLAWS")
    receipt = upload(client)
    assert client.delete(f"/api/v1/receipts/{receipt['id']}").status_code == 204
    detail = client.get(f"/api/v1/transactions/{txn.id}").json()
    assert (detail["is_itemized"], detail["items"]) == (False, [])


def test_excluded_transactions_are_not_matched(db):
    txn = add_txn(db, excluded=True)
    assert try_match_receipt(db, add_receipt(db)) is None
    assert not txn.is_itemized


def test_excluding_a_matched_transaction_frees_its_receipt(client, db, ocr):
    txn = add_txn(db, merchant="LOBLAWS")
    receipt = upload(client)
    assert receipt["matched_transaction_id"] == str(txn.id)
    client.patch(f"/api/v1/transactions/{txn.id}", json={"excluded": True})
    assert client.get(f"/api/v1/receipts/{receipt['id']}").json()["matched_transaction_id"] is None

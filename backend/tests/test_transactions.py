import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.models import RawIngestLog, Receipt, Transaction

PUSH = {
    "account_id": "ws_credit",
    "timestamp": "2026-11-02T14:32:00Z",
    "amount": "15.40",
    "merchant": "SUBWAY #1234",
    "source_ref": "notif-abc",
}


def test_push_creates_transaction(client, db):
    resp = client.post("/api/v1/transactions/push", json=PUSH)
    assert resp.status_code == 201
    body = resp.json()
    assert body["amount"] == 15.40
    assert body["txn_type"] == "PURCHASE"
    assert body["source"] == "android_push"
    assert body["merchant_normalized"] == "Subway"
    assert body["category"] == "Eating Out"
    assert body["categorized_by"] == "rule"
    assert body["purpose"] == "Shared"
    assert body["purpose_is_manual"] is False
    assert db.scalar(select(func.count()).select_from(RawIngestLog)) == 1


def test_push_negative_amount_is_refund(client):
    resp = client.post("/api/v1/transactions/push", json={**PUSH, "amount": "-5.00", "source_ref": "r1"})
    assert resp.json()["txn_type"] == "REFUND"


def test_push_is_idempotent_on_source_ref(client, db):
    first = client.post("/api/v1/transactions/push", json=PUSH)
    second = client.post("/api/v1/transactions/push", json=PUSH)
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert db.scalar(select(func.count()).select_from(Transaction)) == 1


def test_push_without_source_ref_is_not_deduplicated(client, db):
    payload = {k: v for k, v in PUSH.items() if k != "source_ref"}
    client.post("/api/v1/transactions/push", json=payload)
    client.post("/api/v1/transactions/push", json=payload)
    assert db.scalar(select(func.count()).select_from(Transaction)) == 2


def test_push_rejects_unknown_account(client):
    assert client.post("/api/v1/transactions/push", json={**PUSH, "account_id": "nope"}).status_code == 422


def test_push_requires_timezone(client):
    assert client.post("/api/v1/transactions/push", json={**PUSH, "timestamp": "2026-11-02T14:32:00"}).status_code == 422


def test_patch_purpose_marks_manual(client):
    txn_id = client.post("/api/v1/transactions/push", json=PUSH).json()["id"]
    body = client.patch(f"/api/v1/transactions/{txn_id}", json={"purpose": "Alex"}).json()
    assert body["purpose"] == "Alex"
    assert body["purpose_is_manual"] is True
    assert body["category_is_manual"] is False
    assert body["items"] == []


def test_patch_category_marks_manual(client):
    txn_id = client.post("/api/v1/transactions/push", json=PUSH).json()["id"]
    body = client.patch(f"/api/v1/transactions/{txn_id}", json={"category": "Eating Out"}).json()
    assert body["category"] == "Eating Out"
    assert body["category_is_manual"] is True
    assert body["purpose_is_manual"] is False


def test_patch_rejects_unknown_purpose(client):
    txn_id = client.post("/api/v1/transactions/push", json=PUSH).json()["id"]
    assert client.patch(f"/api/v1/transactions/{txn_id}", json={"purpose": "Yolo"}).status_code == 422


def test_patch_missing_transaction(client):
    resp = client.patch("/api/v1/transactions/00000000-0000-0000-0000-000000000000", json={"purpose": "Shared"})
    assert resp.status_code == 404


def test_list_filters_and_orders(client):
    for i, ts in enumerate(["2026-11-01T10:00:00Z", "2026-11-03T10:00:00Z", "2026-10-15T10:00:00Z"]):
        client.post("/api/v1/transactions/push", json={**PUSH, "timestamp": ts, "source_ref": f"n{i}"})
    txn_id = client.get("/api/v1/transactions").json()[0]["id"]
    client.patch(f"/api/v1/transactions/{txn_id}", json={"purpose": "Parents"})

    all_rows = client.get("/api/v1/transactions").json()
    assert [r["timestamp"][:10] for r in all_rows] == ["2026-11-03", "2026-11-01", "2026-10-15"]

    november = client.get("/api/v1/transactions", params={"start": "2026-11-01T00:00:00Z"}).json()
    assert len(november) == 2

    project = client.get("/api/v1/transactions", params={"purpose": "Parents"}).json()
    assert [r["id"] for r in project] == [txn_id]


def test_excluding_a_transaction_removes_it_from_totals_but_keeps_it(client, db):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    month = datetime.now().strftime("%Y-%m")
    payment = client.post("/api/v1/transactions/push", json={**PUSH, "timestamp": now, "amount": "420.00",
                                                             "merchant": "Unknown merchant", "source_ref": "pay"}).json()
    client.post("/api/v1/transactions/push", json={**PUSH, "timestamp": now, "source_ref": "real"})
    assert client.get("/api/v1/summary", params={"month": month}).json()["total_spent"] == 435.40

    body = client.patch(f"/api/v1/transactions/{payment['id']}", json={"excluded": True}).json()
    assert body["excluded"] is True
    assert client.get("/api/v1/summary", params={"month": month}).json()["total_spent"] == 15.40
    # Still listed (so the app can show and undo it), and a re-import is still deduplicated.
    assert payment["id"] in [t["id"] for t in client.get("/api/v1/transactions").json()]
    again = client.post("/api/v1/transactions/push", json={**PUSH, "timestamp": now, "amount": "420.00",
                                                           "merchant": "Unknown merchant", "source_ref": "pay"})
    assert again.status_code == 200 and again.json()["excluded"] is True

    client.patch(f"/api/v1/transactions/{payment['id']}", json={"excluded": False})
    assert client.get("/api/v1/summary", params={"month": month}).json()["total_spent"] == 435.40


AMAZON = {**PUSH, "amount": "120.00", "merchant": "AMZN Mktp CA", "source_ref": "amzn-1",
          "timestamp": "2026-11-02T14:32:00Z"}
LINES = [
    {"sku_name": "Phone case", "price": "30.00", "category": "Shopping", "purpose": "Alex"},
    {"sku_name": "Shampoo", "price": "90.00", "category": "Household", "purpose": "Parents"},
]


def split(client, txn_id, items):
    return client.put(f"/api/v1/transactions/{txn_id}/items", json={"items": items})


def test_split_counts_each_line_on_its_own(client):
    txn_id = client.post("/api/v1/transactions/push", json=AMAZON).json()["id"]
    resp = split(client, txn_id, LINES)
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_itemized"] is True and body["receipt_id"] is None
    assert {(i["sku_name"], i["budget_line"], i["price"]) for i in body["items"]} == {
        ("Phone case", "Alex Personal", 30.0), ("Shampoo", "Parents Shopping", 90.0)}
    # The charge itself is labelled by its biggest line.
    assert (body["category"], body["purpose"]) == ("Household", "Parents")

    cats = {r["category"]: r["spent"] for r in client.get("/api/v1/summary", params={"month": "2026-11", "group_by": "category"}).json()["categories"]}
    assert cats["Shopping"] == 30.0 and cats["Household"] == 90.0
    listed = client.get("/api/v1/transactions", params={"budget_line": "Alex Personal"}).json()
    assert [t["id"] for t in listed] == [txn_id]


def test_split_must_add_up_to_the_charge(client):
    txn_id = client.post("/api/v1/transactions/push", json=AMAZON).json()["id"]
    resp = split(client, txn_id, [LINES[0], {**LINES[1], "price": "80.00"}])
    assert resp.status_code == 422
    assert "110.00" in resp.json()["detail"] and "120.00" in resp.json()["detail"]
    assert client.get(f"/api/v1/transactions/{txn_id}").json()["is_itemized"] is False


@pytest.mark.parametrize("items", [
    [{**LINES[0], "price": "120.00"}],  # one line is not a split
    [LINES[0], {**LINES[1], "category": "Nope"}],
    [LINES[0], {**LINES[1], "purpose": "Nobody"}],
    [{**LINES[0], "sku_name": "   "}, LINES[1]],
    [{**LINES[0], "price": "0"}, {**LINES[1], "price": "120.00"}],
])
def test_split_rejects_bad_lines(client, items):
    txn_id = client.post("/api/v1/transactions/push", json=AMAZON).json()["id"]
    assert split(client, txn_id, items).status_code == 422


def test_split_can_be_replaced_and_removed(client):
    txn_id = client.post("/api/v1/transactions/push", json=AMAZON).json()["id"]
    split(client, txn_id, LINES)
    three = [{**LINES[0], "price": "20.00"}, {**LINES[0], "sku_name": "Cable", "price": "10.00"}, LINES[1]]
    assert len(split(client, txn_id, three).json()["items"]) == 3

    body = split(client, txn_id, []).json()
    assert body["is_itemized"] is False and body["items"] == []
    cats = {r["category"]: r["spent"] for r in client.get("/api/v1/summary", params={"month": "2026-11", "group_by": "category"}).json()["categories"]}
    assert cats.get("Shopping", 0) == 0 and cats["Household"] == 120.0  # whole charge under its own label again


def test_split_keeps_discount_lines(client):
    txn_id = client.post("/api/v1/transactions/push", json=AMAZON).json()["id"]
    items = [{**LINES[0], "price": "40.00"}, LINES[1], {**LINES[1], "sku_name": "Promo", "price": "-10.00"}]
    assert split(client, txn_id, items).status_code == 200


def test_receipt_lines_cannot_be_overwritten_by_a_split(client, db):
    txn_id = client.post("/api/v1/transactions/push", json=AMAZON).json()["id"]
    receipt = Receipt(merchant="Amazon", timestamp=datetime(2026, 11, 2, 14, 32, tzinfo=timezone.utc),
                      total=Decimal("120.00"), matched_transaction_id=uuid.UUID(txn_id))
    db.add(receipt)
    db.commit()
    assert client.get(f"/api/v1/transactions/{txn_id}").json()["receipt_id"] == str(receipt.id)
    assert split(client, txn_id, LINES).status_code == 409


def test_split_unknown_transaction_is_404(client):
    assert split(client, uuid.uuid4(), LINES).status_code == 404


def test_split_transactions_are_not_matched_to_receipts(client, db):
    from app.services.reconciler import try_match_receipt
    txn_id = client.post("/api/v1/transactions/push", json=AMAZON).json()["id"]
    split(client, txn_id, LINES)
    receipt = Receipt(merchant="Amazon", timestamp=datetime(2026, 11, 2, 14, 32, tzinfo=timezone.utc),
                      total=Decimal("120.00"))
    db.add(receipt)
    db.commit()
    assert try_match_receipt(db, receipt) is None

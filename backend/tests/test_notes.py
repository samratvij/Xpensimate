from datetime import datetime, timezone
from decimal import Decimal

from app.db.models import Receipt, ReceiptItem
from app.services import categorizer


def push(client, merchant="FARM BOY #12", amount="40.00", ts="2026-11-02T14:00:00Z"):
    return client.post("/api/v1/transactions/push", json={"timestamp": ts, "amount": amount, "merchant": merchant}).json()


def test_note_on_a_charge(client):
    txn = push(client)
    assert txn["note"] is None
    saved = client.patch(f"/api/v1/transactions/{txn['id']}", json={"note": "  Birthday cake for Riya  "}).json()
    assert saved["note"] == "Birthday cake for Riya"
    assert saved["edited_by"] == "Alex"
    # Other edits leave it alone; the list shows it too.
    client.patch(f"/api/v1/transactions/{txn['id']}", json={"category": "Gifting"})
    listed = client.get("/api/v1/transactions").json()
    assert [t["note"] for t in listed] == ["Birthday cake for Riya"]
    # "" clears it.
    assert client.patch(f"/api/v1/transactions/{txn['id']}", json={"note": " "}).json()["note"] is None


def test_note_is_limited(client):
    txn = push(client)
    assert client.patch(f"/api/v1/transactions/{txn['id']}", json={"note": "x" * 1001}).status_code == 422


def test_notes_on_split_lines(client):
    txn = push(client, amount="30.00")
    detail = client.put(f"/api/v1/transactions/{txn['id']}/items", json={"items": [
        {"sku_name": "Veg", "price": "20.00", "category": "Grocery", "purpose": "Shared", "note": "for the week"},
        {"sku_name": "Chips", "price": "10.00", "category": "Grocery", "purpose": "Alex", "note": ""},
    ]}).json()
    assert [(i["sku_name"], i["note"]) for i in detail["items"]] == [("Veg", "for the week"), ("Chips", None)]


def test_receipt_line_note_reaches_the_matched_charge(client, db):
    txn = push(client, merchant="LOBLAWS 1000", amount="12.00", ts="2026-11-02T14:00:00Z")
    receipt = Receipt(merchant="Loblaws", timestamp=datetime(2026, 11, 2, 14, 5, tzinfo=timezone.utc),
                      total=Decimal("12.00"), items=[
                          ReceiptItem(position=0, sku_name="Milk", category="Grocery", price=Decimal("5.00")),
                          ReceiptItem(position=1, sku_name="Card", category="Gifting", price=Decimal("7.00")),
                      ])
    db.add(receipt)
    db.commit()
    rid, card = str(receipt.id), str(receipt.items[1].id)
    assert client.post(f"/api/v1/receipts/{rid}/match", json={"transaction_id": txn["id"]}).status_code == 200
    edited = client.patch(f"/api/v1/receipts/{rid}/items/{card}", json={"note": "Anniversary card for parents"}).json()
    assert [i["note"] for i in edited["items"]] == [None, "Anniversary card for parents"]
    detail = client.get(f"/api/v1/transactions/{txn['id']}").json()
    assert [(i["sku_name"], i["note"]) for i in detail["items"]] == [("Milk", None), ("Card", "Anniversary card for parents")]
    # Changing the line's category rebuilds the charge's lines; the note stays.
    client.patch(f"/api/v1/receipts/{rid}/items/{card}", json={"category": "Household"})
    detail = client.get(f"/api/v1/transactions/{txn['id']}").json()
    assert detail["items"][1]["note"] == "Anniversary card for parents"


def test_notes_give_claude_context_for_the_merchant(client, db):
    first = push(client, merchant="AMAZON.CA*AB12C", amount="25.00")
    client.patch(f"/api/v1/transactions/{first['id']}", json={"note": "Phone case for Sam"})
    second = push(client, merchant="AMAZON.CA*ZZ99X", amount="30.00", ts="2026-11-03T14:00:00Z")
    client.put(f"/api/v1/transactions/{second['id']}/items", json={"items": [
        {"sku_name": "Diapers", "price": "20.00", "category": "Household", "note": "for the baby shower"},
        {"sku_name": "Book", "price": "10.00", "category": "Shopping"},
    ]})
    hint = categorizer.history_hint(db, "AMAZON.CA*NEW123")
    assert "Diapers: for the baby shower" in hint and "Phone case for Sam" in hint
    # Newest first.
    assert hint.index("baby shower") < hint.index("Phone case")

import io
from datetime import datetime, timezone

import pytest
from PIL import Image

from app.api.v1 import receipts as receipts_api
from app.core.config import get_settings
from app.services.ocr_service import OcrError, OcrLine, OcrReceipt

URL = "/api/v1/receipts/ocr"


def image_bytes(size=(600, 900), fmt="JPEG", mode="RGB"):
    out = io.BytesIO()
    Image.new(mode, size, "white").save(out, format=fmt)
    return out.getvalue()


def line(name, total, category="Grocery", purpose="Shared", qty=1.0):
    return OcrLine(raw_text=name.upper(), name=name, quantity=qty, line_total=total, category=category,
                   purpose=purpose)


GROCERY = OcrReceipt(
    is_receipt=True,
    merchant="Loblaws",
    purchase_date="2026-11-02",
    purchase_time="18:45",
    subtotal=13.49,
    tax=0.65,
    total=14.14,
    lines=[
        line("Great Value 2% Milk 4L", 6.49),
        line("Bananas", 2.00, qty=1.25),
        line("AA Batteries 8pk", 6.00, category="Household"),
        line("Price match", -1.00),
    ],
)


@pytest.fixture
def ocr(monkeypatch, tmp_path):
    """Enable OCR with a stubbed Claude call; returns a setter for the stub's result."""
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr(get_settings(), "receipts_dir", str(tmp_path))
    state = {"result": GROCERY, "images": []}

    def fake(jpeg, categories=None):
        state["images"].append(jpeg)
        if isinstance(state["result"], Exception):
            raise state["result"]
        return state["result"]

    monkeypatch.setattr(receipts_api, "extract_receipt", fake)
    return state


def upload(client, data, name="r.jpg", ctype="image/jpeg"):
    return client.post(URL, files={"file": (name, data, ctype)})


def test_requires_api_key(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "")
    assert upload(client, image_bytes()).status_code == 503


def test_receipt_is_extracted_and_stored(client, ocr, tmp_path):
    resp = upload(client, image_bytes())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["merchant"], body["total"], body["tax"]) == ("Loblaws", 14.14, 0.65)
    assert body["timestamp"] == "2026-11-02T23:45:00Z"  # 18:45 Toronto
    assert body["needs_review"] is False
    assert body["matched_transaction_id"] is None
    assert body["created_by"] == "Alex"
    assert [(i["sku_name"], i["price"], i["category"]) for i in body["items"]] == [
        ("Great Value 2% Milk 4L", 6.49, "Grocery"),
        ("Bananas", 2.0, "Grocery"),
        ("AA Batteries 8pk", 6.0, "Household"),
        ("Price match", -1.0, "Grocery"),
    ]
    assert body["items"][1]["quantity"] == 1.25
    assert body["items"][0]["raw_text"] == "GREAT VALUE 2% MILK 4L"

    image = client.get(f"/api/v1/receipts/{body['id']}/image")
    assert image.headers["content-type"] == "image/jpeg"
    assert image.content == (tmp_path / f"{body['id']}.jpg").read_bytes()


def test_upload_is_normalized_to_bounded_jpeg(client, ocr):
    upload(client, image_bytes(size=(4000, 3000), fmt="PNG", mode="RGBA"), name="r.png", ctype="image/png")
    sent = Image.open(io.BytesIO(ocr["images"][0]))
    assert sent.format == "JPEG"
    assert max(sent.size) == 2000


def test_rejects_non_images(client, ocr):
    assert upload(client, b"%PDF-1.4 not an image", name="r.pdf", ctype="application/pdf").status_code == 415
    assert ocr["images"] == []


def test_rejects_oversized_upload(client, ocr, monkeypatch):
    monkeypatch.setattr(receipts_api, "MAX_UPLOAD_BYTES", 100)
    assert upload(client, image_bytes()).status_code == 413


def test_unbalanced_receipt_needs_review(client, ocr):
    ocr["result"] = GROCERY.model_copy(update={"total": 20.00})
    assert upload(client, image_bytes()).json()["needs_review"] is True


def test_missing_date_uses_upload_time_and_needs_review(client, ocr):
    ocr["result"] = GROCERY.model_copy(update={"purchase_date": None})
    before = datetime.now(timezone.utc)
    body = upload(client, image_bytes()).json()
    assert datetime.fromisoformat(body["timestamp"]) >= before.replace(microsecond=0)
    assert body["needs_review"] is True


def test_missing_time_defaults_to_noon(client, ocr):
    ocr["result"] = GROCERY.model_copy(update={"purchase_time": None})
    assert upload(client, image_bytes()).json()["timestamp"] == "2026-11-02T17:00:00Z"


def test_not_a_receipt_is_rejected_without_storing(client, ocr, tmp_path):
    ocr["result"] = OcrReceipt(is_receipt=False, merchant="", purchase_date=None, purchase_time=None,
                               subtotal=None, tax=0, total=0, lines=[])
    assert upload(client, image_bytes()).status_code == 422
    assert client.get("/api/v1/receipts").json() == []
    assert list(tmp_path.iterdir()) == []


def test_ocr_failure_is_502(client, ocr, tmp_path):
    ocr["result"] = OcrError("Claude API error 529")
    assert upload(client, image_bytes()).status_code == 502
    assert list(tmp_path.iterdir()) == []


def test_edit_item_marks_manual(client, ocr):
    receipt = upload(client, image_bytes()).json()
    item = receipt["items"][2]
    body = client.patch(f"/api/v1/receipts/{receipt['id']}/items/{item['id']}",
                        json={"purpose": "Alex"}).json()
    edited = next(i for i in body["items"] if i["id"] == item["id"])
    assert (edited["purpose"], edited["purpose_is_manual"], edited["category_is_manual"]) == (
        "Alex", True, False,
    )
    other = upload(client, image_bytes()).json()
    resp = client.patch(f"/api/v1/receipts/{other['id']}/items/{item['id']}", json={"purpose": "Parents"})
    assert resp.status_code == 404


def test_delete_removes_image(client, ocr, tmp_path):
    receipt = upload(client, image_bytes()).json()
    assert client.delete(f"/api/v1/receipts/{receipt['id']}").status_code == 204
    assert list(tmp_path.iterdir()) == []
    assert client.get(f"/api/v1/receipts/{receipt['id']}").status_code == 404


def test_list_newest_first(client, ocr):
    upload(client, image_bytes())
    ocr["result"] = GROCERY.model_copy(update={"purchase_date": "2026-11-03"})
    upload(client, image_bytes())
    assert [r["timestamp"][:10] for r in client.get("/api/v1/receipts").json()] == ["2026-11-03", "2026-11-02"]

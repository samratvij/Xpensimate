"""The OpenAI-compatible provider (Ollama, llama.cpp, vLLM, LM Studio) behind categorizing and receipts."""
import json
from datetime import datetime
from decimal import Decimal

import httpx
import pytest

from app.api.v1 import receipts as receipts_api
from app.core.config import get_settings
from app.services import llm
from app.services.llm import LlmError, ask, classify_with_llm, json_part, output_model
from app.services.ocr_service import OcrReceipt, extract_receipt
from tests.test_receipts import image_bytes

CATEGORIES = ["Grocery", "Gas & Parking", "Eating Out"]


@pytest.fixture
def local(monkeypatch):
    """Switch to an OpenAI-compatible server and record what is sent to it; set .reply to answer."""
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_base_url", "http://models.test:11434/v1/")
    monkeypatch.setattr(settings, "anthropic_api_key", "")

    class Server:
        sent: list[dict] = []
        reply: str | Exception = ""
        status = 200

    def post(url, json, headers, timeout):
        Server.sent.append({"url": url, "body": json, "headers": headers, "timeout": timeout})
        if isinstance(Server.reply, Exception):
            raise Server.reply
        return httpx.Response(Server.status, json={"choices": [{"message": {"content": Server.reply}}]})

    monkeypatch.setattr(llm.httpx, "post", post)
    Server.sent = []
    return Server


def classify():
    return classify_with_llm(merchant_raw="SHELL C123", merchant="Shell", amount=Decimal("58.20"), currency="CAD",
                             account=None, local_time=datetime(2026, 11, 3, 18, 5), categories=CATEGORIES)


def test_categorizes_with_a_local_model(local):
    local.reply = json.dumps({"category": "Gas & Parking", "purpose": "Shared", "confidence": 0.9})
    got = classify()
    assert (got.category, got.purpose, got.confidence) == ("Gas & Parking", "Shared", 0.9)
    sent = local.sent[0]
    assert sent["url"] == "http://models.test:11434/v1/chat/completions"
    assert sent["body"]["model"] == "qwen2.5vl:7b" and sent["headers"] == {}
    schema = sent["body"]["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["category"]["enum"] == CATEGORIES  # the model can only pick a real category
    assert "Shell" in sent["body"]["messages"][1]["content"]


def test_a_reasoning_models_thinking_and_fences_are_ignored(local):
    local.reply = '<think>Shell sells fuel.</think>\n```json\n{"category": "Gas & Parking", "purpose": "Shared", "confidence": 0.8}\n```'
    assert classify().category == "Gas & Parking"
    assert json_part("no json here") == "no json here"


@pytest.mark.parametrize("reply", [
    json.dumps({"category": "Fuel", "purpose": "Shared", "confidence": 0.9}),  # not one of the categories
    "I think it's gas.",
    httpx.ConnectError("refused"),
])
def test_a_bad_answer_or_no_server_means_no_guess(local, reply):
    local.reply = reply
    assert classify() is None


def test_server_errors_and_api_keys(local, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_api_key", "sk-local")
    monkeypatch.setattr(get_settings(), "categorizer_model", "llama3.1:8b")
    local.status, local.reply = 404, "model not found"
    with pytest.raises(LlmError, match="404"):
        ask(model=get_settings().categorizer(), system="s", text="t", schema=output_model(CATEGORIES),
            max_tokens=10, effort="low")
    assert local.sent[0]["headers"] == {"Authorization": "Bearer sk-local"}
    assert local.sent[0]["body"]["model"] == "llama3.1:8b"


def test_receipt_photo_goes_as_an_image(local):
    local.reply = json.dumps({"is_receipt": True, "merchant": "FreshCo", "purchase_date": "2026-11-02",
                              "purchase_time": "14:31", "subtotal": 1.48, "tax": 0, "total": 1.48,
                              "lines": [{"raw_text": "BANANAS", "name": "Bananas", "quantity": 1, "line_total": 1.48,
                                         "category": "Grocery", "purpose": "Shared"}]})
    got = extract_receipt(b"\xff\xd8jpeg", CATEGORIES)
    assert isinstance(got, OcrReceipt) and got.lines[0].name == "Bananas"
    image, text = local.sent[0]["body"]["messages"][1]["content"]
    assert image["image_url"]["url"].startswith("data:image/jpeg;base64,") and text["type"] == "text"


def test_enabled_without_a_claude_key(local, client, monkeypatch):
    assert get_settings().ai_enabled()
    health = {c["name"]: c for c in client.get("/api/v1/health").json()["checks"]}
    assert health["AI model"]["status"] == "ok" and "models.test" in health["AI model"]["detail"]
    # Receipt upload no longer needs ANTHROPIC_API_KEY.
    monkeypatch.setattr(receipts_api, "extract_receipt", lambda *_: (_ for _ in ()).throw(receipts_api.OcrError("down")))
    got = client.post("/api/v1/receipts/ocr", files={"file": ("r.jpg", image_bytes(), "image/jpeg")})
    assert got.status_code == 502


def test_claude_stays_the_default(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    assert settings.llm_provider == "anthropic" and not settings.ai_enabled()
    assert (settings.categorizer(), settings.receipt_reader()) == ("claude-haiku-5-5", "claude-sonnet-5-5")

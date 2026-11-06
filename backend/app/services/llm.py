import base64
import logging
import re
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from typing import Literal

import anthropic
import httpx
from pydantic import BaseModel, ValidationError, create_model

from app.core.config import get_settings
from app.core.taxonomy import DEFAULT_PURPOSE, household_description, purpose_guide, purposes

log = logging.getLogger(__name__)

# Notes for categories whose name alone is ambiguous (skipped if the category was renamed or merged away).
CATEGORY_NOTES = {
    "Gas & Parking": "fuel, parking, transit fares and tolls",
    "Subscriptions": "software, apps, cloud storage and streaming",
    "Government & Fees": "licences, road tests, permits, visas and bank fees",
    "Transfer": "money sent to a person",
    "One-time": "a large, unusual purchase",
    "Shopping": "clothes, electronics and other goods that aren't household supplies",
}


def household_guide(categories: list[str]) -> str:
    notes = "\n".join(f'- "{c}": {n}.' for c, n in CATEGORY_NOTES.items() if c in categories)
    return f"""The household is {household_description()}.

Category is what the money was spent on: {", ".join(categories)}.
{notes}

Purpose is who it was for:
{purpose_guide()}

Purpose often can't be told from the merchant alone: then use "{DEFAULT_PURPOSE}" with lower confidence.
When the household's own past categorizations of a merchant are given, follow them: pick the most common
choice unless the amount or notes clearly point elsewhere, and lower your confidence when they disagree."""


def system_prompt(categories: list[str]) -> str:
    return f"""You categorize card transactions for one household.

{household_guide(categories)}

Set confidence between 0 and 1: how likely both category and purpose match what the household would choose.
Merchant strings come from bank alerts and may be truncated or contain store codes."""


class LlmClassification(BaseModel):
    category: str
    purpose: str
    confidence: float


def output_model(categories: list[str]) -> type[BaseModel]:
    """The answer schema, built per request so the model can only pick a category and purpose that exist right now."""
    return create_model(
        "LlmClassification",
        category=(Literal[tuple(categories)], ...),
        purpose=(Literal[purposes()], ...),
        confidence=(float, ...),
    )


def refusal_fallback(model: str) -> dict:
    """Server-side refusal fallback (a declined request is re-run on Anthropic's recommended model).

    Not offered for Haiku, where a refusal simply returns no classification.
    """
    if model.startswith("claude-haiku"):
        return {}
    return {"extra_headers": {"anthropic-beta": "server-side-fallback-2026-07-01"}, "extra_body": {"fallbacks": "default"}}


class LlmError(Exception):
    """The model couldn't be reached, or gave no usable answer."""


@lru_cache
def claude_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key, timeout=30.0, max_retries=2)


def ask(*, model: str, system: str, text: str, schema: type[BaseModel], image_jpeg: bytes | None = None,
        max_tokens: int, effort: str) -> BaseModel:
    """One answer shaped like `schema`, from Claude or from an OpenAI-compatible server (LLM_PROVIDER)."""
    if get_settings().llm_provider == "openai":
        return _ask_openai_compatible(model=model, system=system, text=text, schema=schema, image_jpeg=image_jpeg,
                                      max_tokens=max_tokens)
    return _ask_claude(model=model, system=system, text=text, schema=schema, image_jpeg=image_jpeg,
                       max_tokens=max_tokens, effort=effort)


def _ask_claude(*, model, system, text, schema, image_jpeg, max_tokens, effort) -> BaseModel:
    content = [{"type": "text", "text": text}]
    if image_jpeg is not None:
        content.insert(0, {"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(image_jpeg).decode("ascii")}})
    try:
        response = claude_client().messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=schema,
            output_config={"effort": effort},
            **refusal_fallback(model),
        )
    except anthropic.APIStatusError as e:
        raise LlmError(f"Claude API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LlmError("Could not reach the Claude API") from e
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise LlmError(f"No answer from Claude (stop_reason={response.stop_reason})")
    return response.parsed_output


def _ask_openai_compatible(*, model, system, text, schema, image_jpeg, max_tokens) -> BaseModel:
    """POST /chat/completions with a JSON schema, which Ollama, llama.cpp, vLLM and LM Studio all accept."""
    settings = get_settings()
    content: str | list = text
    if image_jpeg is not None:
        data = base64.standard_b64encode(image_jpeg).decode("ascii")
        content = [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}},
                   {"type": "text", "text": text}]
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "response_format": {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()}},
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    try:
        response = httpx.post(url, json=body, headers=headers, timeout=settings.llm_timeout)
    except httpx.HTTPError as e:
        raise LlmError(f"Could not reach the model server at {settings.llm_base_url} ({type(e).__name__})") from e
    if response.status_code >= 400:
        raise LlmError(f"Model server error {response.status_code}: {response.text[:300]}")
    try:
        reply = response.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise LlmError("The model server's answer wasn't a chat completion") from e
    try:
        return schema.model_validate_json(json_part(reply))
    except ValidationError as e:
        raise LlmError(f"The model's answer didn't fit the expected shape: {e.errors()[0]['msg']}") from e


def json_part(reply: str) -> str:
    """The JSON object in a reply, without a reasoning model's <think> block or a ``` fence around it."""
    reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.DOTALL)
    start, end = reply.find("{"), reply.rfind("}")
    return reply[start:end + 1] if 0 <= start < end else reply


def classify_with_llm(
    *,
    merchant_raw: str,
    merchant: str,
    amount: Decimal,
    currency: str,
    account: str | None,
    local_time: datetime,
    categories: list[str],
    history: str | None = None,
) -> LlmClassification | None:
    """Ask the model for category + purpose. Returns None on any failure so ingestion never blocks on it."""
    details = "\n".join(
        [
            f"Merchant (raw): {merchant_raw}",
            f"Merchant (normalized): {merchant}",
            f"Amount: {amount} {currency}",
            f"Account: {account or 'unknown'}",
            f"Local time: {local_time:%A %Y-%m-%d %H:%M}",
        ]
    )
    if history:
        details += f"\n\n{history}"
    try:
        answer = ask(model=get_settings().categorizer(), system=system_prompt(categories), text=details,
                     schema=output_model(categories), max_tokens=2048, effort="low")
    except LlmError as e:
        log.warning("Categorizing with the model failed: %s", e)
        return None
    return LlmClassification.model_validate(answer.model_dump())

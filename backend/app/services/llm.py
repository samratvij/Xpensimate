import logging
from datetime import datetime
from decimal import Decimal
from functools import lru_cache

import anthropic
from typing import Literal

from pydantic import BaseModel, create_model

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
    """The answer schema, built per request so Claude can only pick a category and purpose that exist right now."""
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


@lru_cache
def claude_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key, timeout=30.0, max_retries=2)


def classify_with_claude(
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
    """Ask Claude for category + purpose. Returns None on any failure so ingestion never blocks on it."""
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
    model = get_settings().categorizer_model
    try:
        response = claude_client().messages.parse(
            model=model,
            max_tokens=2048,
            system=system_prompt(categories),
            messages=[{"role": "user", "content": details}],
            output_format=output_model(categories),
            output_config={"effort": "low"},
            **refusal_fallback(model),
        )
    except anthropic.APIStatusError as e:
        log.warning("Claude categorization failed (%s): %s", e.status_code, e.message)
        return None
    except anthropic.APIConnectionError as e:
        log.warning("Claude categorization unreachable: %s", e)
        return None

    if response.stop_reason == "refusal" or response.parsed_output is None:
        log.warning("Claude returned no classification (stop_reason=%s)", response.stop_reason)
        return None
    return LlmClassification.model_validate(response.parsed_output.model_dump())

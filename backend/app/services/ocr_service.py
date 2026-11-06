import io
import logging
from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from PIL import Image, ImageOps, UnidentifiedImageError
from typing import Literal

from pydantic import BaseModel, create_model

from app.core.config import get_settings
from app.core.taxonomy import purposes
from app.services.llm import LlmError, ask, household_guide

log = logging.getLogger(__name__)

MAX_EDGE_PX = 2000
JPEG_QUALITY = 85
# Line items + tax may differ from the printed total by rounding; beyond this the receipt needs review.
BALANCE_TOLERANCE = Decimal("0.05")
# A receipt without a printed time is assumed to be from midday, keeping it inside the match window.
DEFAULT_TIME = time(12, 0)

def system_prompt(categories: list[str]) -> str:
    return f"""You read photos of shopping receipts, mostly from Canada (HST/GST/PST/QST, bottle deposits,
environmental fees, PC Optimum and other loyalty lines).

Extract every purchased line:
- Merge multi-line items into one line, e.g. a weight line "1.234 kg @ $2.99/kg" belongs to the item above it.
- line_total is the amount charged for that line after quantity. Discounts, coupons and price matches are
  their own lines with a negative line_total. Deposits and eco fees are their own lines with the category of
  the item they belong to.
- Skip lines that are not charges: points, "you saved", subtotal, payment method, change, card numbers.
- raw_text is the line as printed; name is a readable product name, expanding abbreviations only when you
  are confident (e.g. "GV 2% MLK 4L" -> "Great Value 2% Milk 4L").
- category and purpose follow the household's taxonomy below. Most receipt lines are "Shared"; mark a line
  for one person (e.g. a cosmetic or a hobby item) only when it is clearly theirs.

tax is the sum of all tax lines; total is the final amount charged. purchase_date (YYYY-MM-DD) and
purchase_time (HH:MM, 24h) are as printed, or null if not readable. Amounts are plain numbers.
If the image is not a receipt or is unreadable, set is_receipt to false and return no lines.

{household_guide(categories)}"""


class OcrLine(BaseModel):
    raw_text: str
    name: str
    quantity: float
    line_total: float
    category: str
    purpose: str


class OcrReceipt(BaseModel):
    is_receipt: bool
    merchant: str
    purchase_date: str | None
    purchase_time: str | None
    subtotal: float | None
    tax: float
    total: float
    lines: list[OcrLine]


def output_model(categories: list[str]) -> type[BaseModel]:
    """The answer schema with category and purpose limited to the ones that exist right now."""
    line = create_model("OcrLine", __base__=OcrLine, category=(Literal[tuple(categories)], ...),
                        purpose=(Literal[purposes()], ...))
    return create_model("OcrReceipt", __base__=OcrReceipt, lines=(list[line], ...))


class OcrError(Exception):
    pass


def normalize_image(data: bytes) -> bytes:
    """Validate an upload as an image; apply EXIF rotation, cap the size and re-encode as JPEG."""
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise ValueError("Not a readable image") from e
    image.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()


def extract_receipt(jpeg: bytes, categories: list[str]) -> OcrReceipt:
    try:
        answer = ask(
            model=get_settings().receipt_reader(), system=system_prompt(categories), text="Extract this receipt.",
            image_jpeg=jpeg, schema=output_model(categories), max_tokens=16000, effort="medium",
        )
    except LlmError as e:
        log.warning("Receipt OCR failed: %s", e)
        raise OcrError(str(e)) from e
    return OcrReceipt.model_validate(answer.model_dump())


def money(value: float) -> Decimal:
    return Decimal(str(round(value, 2)))


def receipt_timestamp(ocr: OcrReceipt, tz: ZoneInfo) -> datetime | None:
    """Printed date/time in local time, or None if the date isn't readable."""
    try:
        day = date.fromisoformat(ocr.purchase_date) if ocr.purchase_date else None
    except ValueError:
        day = None
    if day is None:
        return None
    try:
        clock = time.fromisoformat(ocr.purchase_time) if ocr.purchase_time else DEFAULT_TIME
    except ValueError:
        clock = DEFAULT_TIME
    return datetime.combine(day, clock, tzinfo=tz)


def is_balanced(ocr: OcrReceipt) -> bool:
    lines = sum((money(line.line_total) for line in ocr.lines), Decimal("0"))
    return abs(lines + money(ocr.tax) - money(ocr.total)) <= BALANCE_TOLERANCE


def now_utc() -> datetime:
    return datetime.now(timezone.utc)

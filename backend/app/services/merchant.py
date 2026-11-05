import re

# Payment-processor prefixes: "SQ *BLUE BOTTLE", "TST* KINKA", "PAYPAL *STEAM".
_PROCESSOR_PREFIX = re.compile(r"^(SQ|TST|SP|PAYPAL|PP|IC|DD)\s*\*\s*")
_REFERENCE_SUFFIX = re.compile(r"\*\S*")
_STORE_NUMBER = re.compile(r"#\s*\d+")
_LONG_NUMBER = re.compile(r"\b\d{3,}\b")
_PROVINCE_SUFFIX = re.compile(r"\s+(ON|QC|BC|AB|MB|SK|NS|NB|NL|PE|YT|NT|NU)$")
_AMAZON = re.compile(r"^(AMZN(\s+MKTP)?(\s+CA)?|AMAZON\.CA)\b")


def normalize_merchant(raw: str) -> str:
    """Strip processor prefixes, store numbers and reference codes; return a display-cased name."""
    name = raw.upper().replace("’", "'").replace("'", "")
    name = _PROCESSOR_PREFIX.sub("", name.strip())
    name = _AMAZON.sub("AMAZON", name)
    name = _REFERENCE_SUFFIX.sub(" ", name)
    name = _STORE_NUMBER.sub(" ", name)
    name = _LONG_NUMBER.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = _PROVINCE_SUFFIX.sub("", name)
    return name.title() if name else raw.strip()


def matches_pattern(pattern: str, *names: str) -> bool:
    """Whole-word, case-insensitive match, so "METRO" hits "Metro 123" but not "Metrolinx"."""
    regex = re.compile(rf"(?<![A-Z0-9]){re.escape(pattern.upper())}(?![A-Z0-9])")
    return any(regex.search(name.upper().replace("'", "")) for name in names)


_HAS_DIGIT = re.compile(r"\S*\d\S*")
# Trailing location words in card descriptors ("TIM HORTONS #1000 TORONTO ON" -> "Tim Hortons").
_CITY_SUFFIX = re.compile(
    r"\s+(Downtown|Toronto|Toro|Ottawa|Gatineau|Montreal|Mississauga|Brampton|Hamilton|Vancouver|Calgary|Edmonton|Winnipeg|Halifax)"
    r"(\s.*)?$",
    re.IGNORECASE,
)


def merchant_key(raw: str) -> str:
    """Coarser than normalize_merchant: also drops store codes and city, so all locations of a chain share a key."""
    name = _HAS_DIGIT.sub(" ", normalize_merchant(raw))
    name = re.sub(r"\s+", " ", name).strip()
    name = _CITY_SUFFIX.sub("", name).strip(" .-/&")
    return name or normalize_merchant(raw)

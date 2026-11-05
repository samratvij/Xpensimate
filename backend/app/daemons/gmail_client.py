import base64
import html
import os
import re
from pathlib import Path
from typing import Any

import httplib2
from google.auth.transport.requests import Request
from google_auth_httplib2 import AuthorizedHttp
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.core.config import get_settings

# One OAuth grant covers reading mail and pulling the Pub/Sub subscription.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/pubsub",
]


def secrets_path(name: str) -> Path:
    return Path(get_settings().gmail_secrets_dir) / name


def save_credentials(creds: Credentials) -> None:
    path = secrets_path("token.json")
    path.write_text(creds.to_json())
    os.chmod(path, 0o600)


def load_credentials() -> Credentials:
    path = secrets_path("token.json")
    if not path.exists():
        raise SystemExit(f"{path} not found: run `python -m app.daemons.gmail_auth` first (docs/gmail-setup.md)")
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        save_credentials(creds)
    return creds


# httplib2 waits forever by default; one hung request would hold the worker's lock and stall all syncing.
GMAIL_TIMEOUT_SECONDS = 60


def build_gmail(creds: Credentials):
    http = AuthorizedHttp(creds, http=httplib2.Http(timeout=GMAIL_TIMEOUT_SECONDS))
    return build("gmail", "v1", http=http, cache_discovery=False)


def header(message: dict[str, Any], name: str) -> str:
    for h in message.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style).*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    return "\n".join(re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.splitlines() if line.strip())


# Zero-width / combining padding that marketing templates use to pad preview text.
_INVISIBLE = re.compile(r"[\u034f\u200b\u200c\u200d\u2060\ufeff\xa0]")


def _tidy(text: str) -> str:
    text = _INVISIBLE.sub(" ", text)
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines() if line.strip())


def message_text(payload: dict[str, Any]) -> str:
    """Plain-text body of a Gmail message payload, falling back to de-tagged HTML."""
    plain: list[str] = []
    markup: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            markup.append(_decode(data))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    if plain:
        return _tidy("\n".join(plain))
    return _tidy("\n".join(_html_to_text(m) for m in markup))

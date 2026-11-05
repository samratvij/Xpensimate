"""Firebase Cloud Messaging (HTTP v1) sender. Outbound HTTPS only."""
import logging
from functools import lru_cache
from pathlib import Path

import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Device

log = logging.getLogger(__name__)

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
ANDROID_CHANNEL = "budget_alerts"


@lru_cache
def fcm_session() -> tuple[AuthorizedSession, str] | None:
    path = Path(get_settings().fcm_credentials)
    if not path.exists():
        log.info("Push notifications are off: %s not found", path)
        return None
    creds = service_account.Credentials.from_service_account_file(str(path), scopes=[FCM_SCOPE])
    return AuthorizedSession(creds), creds.project_id


def _is_dead_token(resp: requests.Response) -> bool:
    """FCM's answer for a token that was uninstalled or rotated: drop it."""
    if resp.status_code == 404:
        return True
    try:
        details = resp.json()["error"].get("details", [])
    except (ValueError, KeyError):
        return False
    return any(d.get("errorCode") == "UNREGISTERED" for d in details)


def send_to_devices(db: Session, title: str, body: str, data: dict[str, str]) -> int:
    """Notify every registered device. Returns how many accepted it; never raises for delivery problems."""
    fcm = fcm_session()
    if fcm is None:
        return 0
    session, project = fcm
    delivered = 0
    for device in list(db.scalars(select(Device))):
        message = {
            "message": {
                "token": device.token,
                "notification": {"title": title, "body": body},
                "data": data,
                "android": {"priority": "high", "notification": {"channel_id": ANDROID_CHANNEL}},
            }
        }
        try:
            resp = session.post(FCM_URL.format(project=project), json=message, timeout=10)
        except requests.RequestException as e:
            log.warning("FCM unreachable: %s", e)
            continue
        if resp.ok:
            delivered += 1
        elif _is_dead_token(resp):
            log.info("Removing unregistered device %s", device.name or device.token[:12])
            db.delete(device)
        else:
            log.warning("FCM rejected message (%s): %s", resp.status_code, resp.text[:300])
    db.commit()
    return delivered

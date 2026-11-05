from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import Device
from app.schemas import DeviceIn, DeviceOut, PushTestOut
from app.services.push import send_to_devices

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
def register_device(body: DeviceIn, response: Response, db: Session = Depends(get_db)) -> Device:
    """Called by the app on start and whenever Firebase rotates its token."""
    device = db.get(Device, body.token)
    if device is None:
        device = Device(token=body.token)
        db.add(device)
    else:
        response.status_code = status.HTTP_200_OK
        device.last_seen_at = datetime.now(timezone.utc)
    device.name = body.name or device.name
    db.commit()
    db.refresh(device)
    return device


@router.delete("/{token}", status_code=status.HTTP_204_NO_CONTENT)
def unregister_device(token: str, db: Session = Depends(get_db)) -> None:
    device = db.get(Device, token)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not registered")
    db.delete(device)
    db.commit()


@router.post("/test", response_model=PushTestOut)
def send_test_notification(db: Session = Depends(get_db)) -> PushTestOut:
    devices = db.scalar(select(func.count()).select_from(Device))
    delivered = send_to_devices(
        db, "Xpensimate test", "Budget alerts will show up like this.", {"event": "TEST"}
    )
    return PushTestOut(devices=devices, delivered=delivered)

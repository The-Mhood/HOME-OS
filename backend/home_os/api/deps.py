import hmac
from datetime import datetime, timezone
from typing import Generator

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from home_os.db.models import Device, DeviceCredential, User, UserSession
from home_os.runtime import HomeOsRuntime
from home_os.security.audit import append_audit_log
from home_os.security.auth import resolve_session_user
from home_os.security.crypto import hash_token
from home_os.security.replay import ReplayAttackError


def get_runtime(request: Request) -> HomeOsRuntime:
    return request.app.state.runtime


def get_db(runtime: HomeOsRuntime = Depends(get_runtime)) -> Generator[Session, None, None]:
    db = runtime.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def get_current_user_and_session(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> tuple[User, UserSession]:
    raw_token = _extract_bearer_token(authorization)
    resolved = resolve_session_user(db, raw_token or "")
    if resolved is None:
        append_audit_log(
            db,
            severity="warning",
            category="auth",
            event_type="api.unauthorized_access",
            actor_type="anonymous",
            ip_address=request.client.host if request.client else None,
            outcome="denied",
            details={"path": request.url.path},
        )
        db.commit()
        raise HTTPException(status_code=401, detail="Valid authenticated session required")
    return resolved


def get_current_user(
    pair: tuple[User, UserSession] = Depends(get_current_user_and_session),
) -> User:
    return pair[0]


async def verify_authenticated_device(
    request: Request,
    x_device_id: str = Header(...),
    x_device_token: str = Header(...),
    x_timestamp: int = Header(...),
    x_nonce: str = Header(...),
    x_signature: str = Header(...),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
) -> Device:
    device = db.get(Device, x_device_id)
    if device is None:
        raise HTTPException(status_code=401, detail="Unknown device")
    if device.lifecycle_state == "isolated" or device.connection_status == "isolated":
        raise HTTPException(status_code=403, detail="Device is isolated/quarantined")
    if device.lifecycle_state != "authenticated":
        raise HTTPException(status_code=403, detail="Device is not authenticated")

    cred = db.execute(select(DeviceCredential).where(DeviceCredential.device_id == x_device_id)).scalar_one_or_none()
    if cred is None or cred.revoked_at is not None:
        raise HTTPException(status_code=401, detail="Device credentials revoked or missing")

    presented_hash = hash_token(x_device_token)
    if not hmac.compare_digest(presented_hash, cred.token_hash):
        append_audit_log(
            db,
            severity="warning",
            category="security",
            event_type="device.invalid_token",
            actor_type="device",
            actor_id=x_device_id,
            device_id=x_device_id,
            ip_address=request.client.host if request.client else None,
            outcome="rejected",
        )
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid device token")

    hmac_secret = runtime.vault.decrypt(cred.encrypted_hmac_secret, aad=f"device:{x_device_id}")
    raw_body = await request.body()

    try:
        runtime.replay_guard.verify_signed_request(
            device_id=x_device_id,
            secret=hmac_secret,
            method=request.method,
            path=request.url.path,
            timestamp=x_timestamp,
            nonce=x_nonce,
            body=raw_body,
            signature=x_signature,
        )
    except ReplayAttackError as exc:
        append_audit_log(
            db,
            severity="critical",
            category="security",
            event_type="security.replay_or_signature_blocked",
            actor_type="device",
            actor_id=x_device_id,
            device_id=x_device_id,
            ip_address=request.client.host if request.client else None,
            outcome="blocked",
            details={"reason": str(exc), "path": request.url.path},
        )
        db.commit()
        raise HTTPException(status_code=401, detail=str(exc))

    device.last_seen_at = datetime.now(timezone.utc)
    if device.connection_status == "offline":
        device.connection_status = "online"
    db.flush()
    return device

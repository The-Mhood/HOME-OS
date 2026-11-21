from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from home_os.db.models import User, UserSession
from home_os.security.crypto import generate_secure_token, hash_token


def _ensure_utc(dt_val: datetime) -> datetime:
    if dt_val.tzinfo is None:
        return dt_val.replace(tzinfo=timezone.utc)
    return dt_val.astimezone(timezone.utc)


def create_user_session(
    db: Session,
    *,
    user: User,
    ttl_minutes: int = 720,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, UserSession]:
    raw_token = generate_secure_token(32)
    now = datetime.now(timezone.utc)
    session_obj = UserSession(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        ip_address=ip_address,
        user_agent=user_agent,
        created_at=now,
        expires_at=now + timedelta(minutes=ttl_minutes),
    )
    user.last_login_at = now
    db.add(session_obj)
    db.flush()
    return raw_token, session_obj


def resolve_session_user(db: Session, raw_token: str) -> tuple[User, UserSession] | None:
    if not raw_token:
        return None
    digest = hash_token(raw_token)
    session_obj = db.execute(
        select(UserSession).where(UserSession.token_hash == digest)
    ).scalar_one_or_none()
    if session_obj is None:
        return None
    if session_obj.revoked_at is not None:
        return None
    now = datetime.now(timezone.utc)
    if _ensure_utc(session_obj.expires_at) <= now:
        return None
    user = db.get(User, session_obj.user_id)
    if user is None or not user.is_active:
        return None
    return user, session_obj

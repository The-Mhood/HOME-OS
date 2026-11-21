import dataclasses
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from home_os.db.models import AuditLog

GENESIS_HASH = "0" * 64


@dataclasses.dataclass(frozen=True)
class AuditChainVerificationReport:
    valid: bool
    checked_entries: int
    head_hash: str
    broken_at_id: int | None = None
    reason: str | None = None


def _canonical_audit_digest(
    *,
    prev_hash: str,
    severity: str,
    category: str,
    event_type: str,
    actor_type: str,
    actor_id: str | None,
    device_id: str | None,
    ip_address: str | None,
    outcome: str,
    details: dict[str, Any],
    created_iso: str,
) -> str:
    payload = {
        "prev_hash": prev_hash,
        "severity": severity,
        "category": category,
        "event_type": event_type,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "device_id": device_id,
        "ip_address": ip_address,
        "outcome": outcome,
        "details": details,
        "created_at": created_iso,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_ts(dt_val: datetime) -> str:
    if dt_val.tzinfo is None:
        dt_val = dt_val.replace(tzinfo=timezone.utc)
    else:
        dt_val = dt_val.astimezone(timezone.utc)
    return dt_val.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def append_audit_log(
    db: Session,
    *,
    severity: str,
    category: str,
    event_type: str,
    actor_type: str,
    outcome: str,
    actor_id: str | None = None,
    device_id: str | None = None,
    ip_address: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    last_entry = db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1)).scalar_one_or_none()
    prev_hash = last_entry.entry_hash if last_entry is not None else GENESIS_HASH
    now = datetime.now(timezone.utc)
    clean_details = details or {}
    created_iso = _normalize_ts(now)

    entry_hash = _canonical_audit_digest(
        prev_hash=prev_hash,
        severity=severity,
        category=category,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        device_id=device_id,
        ip_address=ip_address,
        outcome=outcome,
        details=clean_details,
        created_iso=created_iso,
    )

    entry = AuditLog(
        severity=severity,
        category=category,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        device_id=device_id,
        ip_address=ip_address,
        outcome=outcome,
        details_json=clean_details,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
        created_at=now,
    )
    db.add(entry)
    db.flush()
    return entry


def verify_audit_chain(db: Session) -> AuditChainVerificationReport:
    entries = db.execute(select(AuditLog).order_by(AuditLog.id.asc())).scalars().all()
    expected_prev = GENESIS_HASH
    for entry in entries:
        if entry.prev_hash != expected_prev:
            return AuditChainVerificationReport(
                valid=False,
                checked_entries=len(entries),
                head_hash=entries[-1].entry_hash if entries else GENESIS_HASH,
                broken_at_id=entry.id,
                reason="prev_hash linkage mismatch",
            )
        recomputed = _canonical_audit_digest(
            prev_hash=entry.prev_hash,
            severity=entry.severity,
            category=entry.category,
            event_type=entry.event_type,
            actor_type=entry.actor_type,
            actor_id=entry.actor_id,
            device_id=entry.device_id,
            ip_address=entry.ip_address,
            outcome=entry.outcome,
            details=entry.details_json,
            created_iso=_normalize_ts(entry.created_at),
        )
        if recomputed != entry.entry_hash:
            return AuditChainVerificationReport(
                valid=False,
                checked_entries=len(entries),
                head_hash=entries[-1].entry_hash if entries else GENESIS_HASH,
                broken_at_id=entry.id,
                reason="entry_hash digest mismatch (tampering detected)",
            )
        expected_prev = entry.entry_hash

    return AuditChainVerificationReport(
        valid=True,
        checked_entries=len(entries),
        head_hash=expected_prev,
    )

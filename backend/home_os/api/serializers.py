from typing import Any
from home_os.db.models import (
    ActiveMediaSession,
    AuditLog,
    AutomationRule,
    AutomationRun,
    CommandExecution,
    CommunicationItem,
    Device,
    DeviceCapability,
    DevicePermission,
    FileTransfer,
    IntegrationConnection,
    MediaItem,
    Room,
    User,
    UserSession,
)


def serialize_user(u: User) -> dict[str, Any]:
    return {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "role": u.role,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
    }


def serialize_session(s: UserSession, current_id: str | None = None) -> dict[str, Any]:
    return {
        "id": s.id,
        "ip_address": s.ip_address,
        "user_agent": s.user_agent,
        "created_at": s.created_at.isoformat(),
        "expires_at": s.expires_at.isoformat(),
        "revoked_at": s.revoked_at.isoformat() if s.revoked_at else None,
        "is_current": s.id == current_id,
    }


def serialize_room(r: Room) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "floor": r.floor,
        "icon": r.icon,
        "created_at": r.created_at.isoformat(),
    }


def serialize_capability(c: DeviceCapability) -> dict[str, Any]:
    return {
        "id": c.id,
        "capability_key": c.capability_key,
        "category": c.category,
        "display_name": c.display_name,
        "description": c.description,
        "supported": c.supported,
        "unavailable_reason": c.unavailable_reason,
        "requires_permission": c.requires_permission,
        "requires_confirmation": c.requires_confirmation,
        "parameters_schema": c.parameters_schema_json or {},
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def serialize_permission(p: DevicePermission) -> dict[str, Any]:
    return {
        "id": p.id,
        "device_id": p.device_id,
        "permission_scope": p.permission_scope,
        "status": p.status,
        "granted_at": p.granted_at.isoformat() if p.granted_at else None,
        "expires_at": p.expires_at.isoformat() if p.expires_at else None,
        "revoked_at": p.revoked_at.isoformat() if p.revoked_at else None,
        "reason": p.reason,
    }


def serialize_device(d: Device, include_details: bool = False) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": d.id,
        "hardware_fingerprint": d.hardware_fingerprint,
        "name": d.name,
        "category": d.category,
        "manufacturer": d.manufacturer,
        "model": d.model,
        "os_name": d.os_name,
        "os_version": d.os_version,
        "ip_address": d.ip_address,
        "mac_address": d.mac_address,
        "port": d.port,
        "hostname": d.hostname,
        "room_id": d.room_id,
        "room_name": d.room.name if d.room else None,
        "group_tags": d.group_tags or [],
        "lifecycle_state": d.lifecycle_state,
        "is_registered": d.lifecycle_state in ("enrolled", "authenticated", "isolated"),
        "discovery_source": d.discovery_source,
        "adapter_type": d.adapter_type,
        "connection_status": d.connection_status,
        "battery_level": d.battery_level,
        "is_charging": d.is_charging,
        "health_score": d.health_score,
        "integration_health": d.integration_health,
        "health_details": d.health_details_json or {},
        "metadata": d.metadata_json or {},
        "first_seen_at": d.first_seen_at.isoformat() if d.first_seen_at else None,
        "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
        "enrolled_at": d.enrolled_at.isoformat() if d.enrolled_at else None,
        "key_version": d.credential.key_version if d.credential else None,
    }
    if include_details:
        base["capabilities"] = [serialize_capability(c) for c in d.capabilities]
        base["permissions"] = [serialize_permission(p) for p in d.permissions]
    return base


def serialize_command(c: CommandExecution, device_name: str | None = None) -> dict[str, Any]:
    return {
        "id": c.id,
        "device_id": c.device_id,
        "device_name": device_name,
        "automation_run_id": c.automation_run_id,
        "capability_key": c.capability_key,
        "parameters": c.parameters_json or {},
        "status": c.status,
        "verification_method": c.verification_method,
        "result": c.result_json or {},
        "error_code": c.error_code,
        "error_message": c.error_message,
        "retry_count": c.retry_count,
        "max_retries": c.max_retries,
        "timeout_ms": c.timeout_ms,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "dispatched_at": c.dispatched_at.isoformat() if c.dispatched_at else None,
        "completed_at": c.completed_at.isoformat() if c.completed_at else None,
    }


def serialize_comm_item(item: CommunicationItem, device_name: str | None = None) -> dict[str, Any]:
    return {
        "id": item.id,
        "device_id": item.device_id,
        "device_name": device_name,
        "source_type": item.source_type,
        "access_level": item.access_level,
        "app_package": item.app_package,
        "app_name": item.app_name,
        "sender": item.sender,
        "title": item.title,
        "body_preview": item.body_preview,
        "can_reply": item.can_reply,
        "is_read": item.is_read,
        "dismissed": item.dismissed,
        "received_at": item.received_at.isoformat(),
    }


def serialize_media_session(ms: ActiveMediaSession, device_name: str | None = None) -> dict[str, Any]:
    return {
        "id": ms.id,
        "device_id": ms.device_id,
        "device_name": device_name,
        "app_name": ms.app_name,
        "title": ms.title,
        "artist": ms.artist,
        "album": ms.album,
        "playback_state": ms.playback_state,
        "position_ms": ms.position_ms,
        "duration_ms": ms.duration_ms,
        "volume_percent": ms.volume_percent,
        "is_muted": ms.is_muted,
        "can_seek": ms.can_seek,
        "can_skip": ms.can_skip,
        "updated_at": ms.updated_at.isoformat(),
    }


def serialize_media_item(m: MediaItem) -> dict[str, Any]:
    return {
        "id": m.id,
        "device_id": m.device_id,
        "media_type": m.media_type,
        "title": m.title,
        "remote_path": m.remote_path,
        "mime_type": m.mime_type,
        "size_bytes": m.size_bytes,
        "duration_seconds": m.duration_seconds,
        "indexed_at": m.indexed_at.isoformat(),
    }


def serialize_transfer(t: FileTransfer) -> dict[str, Any]:
    return {
        "id": t.id,
        "direction": t.direction,
        "source_device_id": t.source_device_id,
        "target_device_id": t.target_device_id,
        "source_path": t.source_path,
        "target_path": t.target_path,
        "file_name": t.file_name,
        "mime_type": t.mime_type,
        "size_bytes": t.size_bytes,
        "transferred_bytes": t.transferred_bytes,
        "sha256_checksum": t.sha256_checksum,
        "status": t.status,
        "error_message": t.error_message,
        "created_at": t.created_at.isoformat(),
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
    }


def serialize_rule(r: AutomationRule) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "description": r.description,
        "enabled": r.enabled,
        "trigger_type": r.trigger_type,
        "trigger_config": r.trigger_config_json,
        "conditions": r.conditions_json,
        "actions": r.actions_json,
        "retry_policy": r.retry_policy_json,
        "requires_confirmation": r.requires_confirmation,
        "last_triggered_at": r.last_triggered_at.isoformat() if r.last_triggered_at else None,
        "created_at": r.created_at.isoformat(),
    }


def serialize_run(r: AutomationRun, rule_name: str | None = None) -> dict[str, Any]:
    return {
        "id": r.id,
        "rule_id": r.rule_id,
        "rule_name": rule_name,
        "trigger_reason": r.trigger_reason,
        "status": r.status,
        "step_results": r.step_results_json,
        "error_message": r.error_message,
        "started_at": r.started_at.isoformat(),
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
    }


def serialize_integration(ic: IntegrationConnection) -> dict[str, Any]:
    return {
        "id": ic.id,
        "integration_type": ic.integration_type,
        "name": ic.name,
        "endpoint_url": ic.endpoint_url,
        "status": ic.status,
        "health_message": ic.health_message,
        "config": ic.config_json or {},
        "last_sync_at": ic.last_sync_at.isoformat() if ic.last_sync_at else None,
        "created_at": ic.created_at.isoformat(),
    }


def serialize_audit(a: AuditLog) -> dict[str, Any]:
    return {
        "id": a.id,
        "severity": a.severity,
        "category": a.category,
        "event_type": a.event_type,
        "actor_type": a.actor_type,
        "actor_id": a.actor_id,
        "device_id": a.device_id,
        "ip_address": a.ip_address,
        "outcome": a.outcome,
        "details": a.details_json,
        "prev_hash": a.prev_hash,
        "entry_hash": a.entry_hash,
        "created_at": a.created_at.isoformat(),
    }

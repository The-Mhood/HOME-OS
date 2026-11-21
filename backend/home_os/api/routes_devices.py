import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from home_os.api.deps import get_current_user, get_db, get_runtime
from home_os.api.serializers import (
    serialize_command,
    serialize_device,
    serialize_media_session,
    serialize_permission,
    serialize_room,
)
from home_os.db.models import (
    ActiveMediaSession,
    CommandExecution,
    Device,
    DeviceCapability,
    DevicePermission,
    Room,
    User,
)
from home_os.runtime import HomeOsRuntime
from home_os.schemas.api import (
    DeviceUpdateRequest,
    IsolateDeviceRequest,
    ManualDeviceRequest,
    PairingCompleteRequest,
    PairingInitiateRequest,
    PermissionUpdateRequest,
    RoomCreateRequest,
    SubmitCommandRequest,
)
from home_os.security.audit import append_audit_log
from home_os.services.command_orchestrator import CommandPipelineError
from home_os.services.pairing import PairingError
from home_os.adapters.base import CapabilityDescriptor

router = APIRouter()


@router.get("/rooms")
def list_rooms(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rooms = db.execute(select(Room).order_by(Room.name.asc())).scalars().all()
    return {"rooms": [serialize_room(r) for r in rooms]}


@router.post("/rooms")
def create_room(
    payload: RoomCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.execute(select(Room).where(Room.name == payload.name.strip())).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Room with that name already exists")
    room = Room(name=payload.name.strip(), floor=payload.floor, icon=payload.icon)
    db.add(room)
    append_audit_log(
        db,
        severity="info",
        category="inventory",
        event_type="room.created",
        actor_type="user",
        actor_id=user.id,
        outcome="success",
        details={"name": room.name},
    )
    db.commit()
    db.refresh(room)
    return {"room": serialize_room(room)}


@router.delete("/rooms/{room_id}")
def delete_room(
    room_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    room = db.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")
    db.delete(room)
    append_audit_log(
        db,
        severity="info",
        category="inventory",
        event_type="room.deleted",
        actor_type="user",
        actor_id=user.id,
        outcome="success",
        details={"room_id": room_id},
    )
    db.commit()
    return {"status": "deleted"}


@router.post("/discovery/scan")
async def run_network_discovery(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    devices = await runtime.discovery.scan_all(db=db, actor_user_id=user.id)
    all_devices = db.execute(select(Device).order_by(Device.last_seen_at.desc())).scalars().all()
    return {
        "discovered_in_scan": len(devices),
        "devices": [serialize_device(d, include_details=True) for d in all_devices],
    }


@router.post("/devices/manual")
def register_manual_discovered_device(
    payload: ManualDeviceRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    seed = f"manual:{payload.ip_address or ''}:{payload.mac_address or ''}:{payload.name}"
    fp = "manual-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    existing = db.execute(select(Device).where(Device.hardware_fingerprint == fp)).scalar_one_or_none()
    if existing is not None:
        return {"device": serialize_device(existing, include_details=True)}

    now = datetime.now(timezone.utc)
    device = Device(
        hardware_fingerprint=fp,
        name=payload.name.strip(),
        category=payload.category,
        manufacturer=payload.manufacturer,
        model=payload.model,
        ip_address=payload.ip_address,
        port=payload.port,
        mac_address=payload.mac_address,
        lifecycle_state="identified",
        discovery_source="manual",
        adapter_type=payload.adapter_type,
        connection_status="unpaired",
        integration_health="unverified_until_paired",
        first_seen_at=now,
        last_seen_at=now,
    )
    db.add(device)
    db.flush()
    db.add(
        DeviceCapability(
            device_id=device.id,
            capability_key="control.unpaired",
            category="system",
            display_name="Remote Control Unavailable Until Paired",
            supported=False,
            unavailable_reason="Manually added device must complete authenticated pairing before control.",
            requires_permission="telemetry:read",
        )
    )
    append_audit_log(
        db,
        severity="info",
        category="inventory",
        event_type="device.manual_added",
        actor_type="user",
        actor_id=user.id,
        device_id=device.id,
        outcome="success",
        details={"name": device.name, "adapter_type": device.adapter_type},
    )
    db.commit()
    db.refresh(device)
    return {"device": serialize_device(device, include_details=True)}


@router.post("/pairing/initiate")
def initiate_pairing(
    payload: PairingInitiateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    from home_os.services.discovery import _is_safe_lan_ip, get_primary_lan_ipv4

    req_host = request.url.hostname or ""
    if (
        payload.hub_host
        and payload.hub_host not in ("127.0.0.1", "localhost", "0.0.0.0")
        and _is_safe_lan_ip(payload.hub_host)
    ):
        host = payload.hub_host
    elif req_host and req_host not in ("127.0.0.1", "localhost", "0.0.0.0") and _is_safe_lan_ip(req_host):
        host = req_host
    else:
        host = get_primary_lan_ipv4()

    port = payload.hub_port or runtime.settings.bind_port
    res = runtime.pairing.initiate_pairing(
        db=db,
        user_id=user.id,
        adapter_type=payload.adapter_type,
        hub_host=host,
        hub_port=port,
        device_id=payload.device_id,
        requested_permissions=payload.requested_permissions,
    )
    return {
        "session_id": res.session_id,
        "pin": res.pin,
        "qr_uri": res.qr_uri,
        "deep_link_uri": res.deep_link_uri,
        "lan_host": host,
        "lan_port": port,
        "qr_svg": res.qr_svg,
        "challenge_nonce": res.challenge_nonce,
        "expires_at": res.expires_at.isoformat(),
    }


@router.post("/pairing/complete")
async def complete_pairing(
    payload: PairingCompleteRequest,
    request: Request,
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    caps = (
        [CapabilityDescriptor(**c) for c in payload.capabilities]
        if payload.capabilities is not None
        else None
    )
    try:
        res = await runtime.pairing.complete_pairing(
            db=db,
            pairing_session_id=payload.pairing_session_id,
            pin_or_token=payload.pin_or_token,
            hardware_fingerprint=payload.hardware_fingerprint,
            name=payload.name,
            category=payload.category,
            manufacturer=payload.manufacturer,
            model=payload.model,
            os_name=payload.os_name,
            os_version=payload.os_version,
            ip_address=payload.ip_address or (request.client.host if request.client else None),
            port=payload.port,
            capabilities=caps,
            metadata=payload.metadata,
        )
    except PairingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    if res.device.adapter_type == "google_tv" and res.device.ip_address:
        runtime.google_tv_adapter.register_endpoint(
            device_id=res.device.id,
            host=res.device.ip_address,
            operational_port=res.device.port or 6466,
            mac_address=res.device.mac_address,
        )

    return {
        "device": serialize_device(res.device, include_details=True),
        "credentials": {
            "device_id": res.device.id,
            "device_token": res.device_token,
            "hmac_secret": res.hmac_secret,
            "key_version": res.key_version,
            "tls_fingerprint": runtime.tls_info.sha256_fingerprint,
        },
    }


@router.get("/devices")
def list_devices(
    search: str | None = Query(default=None),
    category: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    connection_status: str | None = Query(default=None),
    lifecycle_state: str | None = Query(default=None),
    sort_by: str = Query(default="last_seen"),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Device)
    if category:
        stmt = stmt.where(Device.category == category)
    if room_id:
        stmt = stmt.where(Device.room_id == room_id)
    if connection_status:
        stmt = stmt.where(Device.connection_status == connection_status)
    if lifecycle_state:
        stmt = stmt.where(Device.lifecycle_state == lifecycle_state)

    devices = db.execute(stmt).scalars().all()
    if search:
        q = search.strip().lower()
        devices = [
            d
            for d in devices
            if q in d.name.lower()
            or q in (d.manufacturer or "").lower()
            or q in (d.model or "").lower()
            or q in (d.ip_address or "").lower()
            or q in d.category.lower()
        ]

    if sort_by == "name":
        devices.sort(key=lambda x: x.name.lower())
    elif sort_by == "status":
        devices.sort(key=lambda x: x.connection_status)
    else:
        devices.sort(key=lambda x: x.last_seen_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    return {"devices": [serialize_device(d, include_details=True) for d in devices]}


@router.get("/devices/{device_id}")
def get_device_detail(
    device_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    commands = db.execute(
        select(CommandExecution)
        .where(CommandExecution.device_id == device_id)
        .order_by(CommandExecution.created_at.desc())
        .limit(25)
    ).scalars().all()
    media_session = db.execute(
        select(ActiveMediaSession).where(ActiveMediaSession.device_id == device_id)
    ).scalar_one_or_none()

    return {
        "device": serialize_device(device, include_details=True),
        "recent_commands": [serialize_command(c, device.name) for c in commands],
        "media_session": serialize_media_session(media_session, device.name) if media_session else None,
    }


@router.patch("/devices/{device_id}")
def update_device(
    device_id: str,
    payload: DeviceUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    if payload.name is not None:
        device.name = payload.name.strip()
    if payload.clear_room:
        device.room_id = None
    elif payload.room_id is not None:
        device.room_id = payload.room_id
    if payload.group_tags is not None:
        device.group_tags = payload.group_tags

    append_audit_log(
        db,
        severity="info",
        category="inventory",
        event_type="device.updated",
        actor_type="user",
        actor_id=user.id,
        device_id=device.id,
        outcome="success",
        details={"name": device.name, "room_id": device.room_id},
    )
    db.commit()
    db.refresh(device)
    return {"device": serialize_device(device, include_details=True)}


@router.delete("/devices/{device_id}")
async def remove_device(
    device_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    adapter = runtime.adapter_registry.get(device.adapter_type)
    if adapter is not None:
        await adapter.revoke_access(device.id, reason="device_removed")

    dev_name = device.name
    db.delete(device)
    append_audit_log(
        db,
        severity="warning",
        category="inventory",
        event_type="device.removed",
        actor_type="user",
        actor_id=user.id,
        device_id=device_id,
        outcome="removed",
        details={"name": dev_name},
    )
    db.commit()
    return {"status": "removed"}


@router.post("/devices/{device_id}/reconnect")
async def reconnect_device(
    device_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if device.lifecycle_state == "isolated":
        raise HTTPException(status_code=403, detail="Restore isolated device before reconnecting")

    adapter = runtime.adapter_registry.get(device.adapter_type)
    if adapter is not None and device.lifecycle_state == "authenticated":
        snapshot = await adapter.get_status(device.id)
        device.connection_status = "online" if snapshot.online else "offline"
        device.integration_health = snapshot.integration_health
        if snapshot.battery_level is not None:
            device.battery_level = snapshot.battery_level
        if snapshot.online:
            device.last_seen_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(device)

    return {"device": serialize_device(device, include_details=True)}


@router.post("/devices/{device_id}/permissions")
async def update_device_permission(
    device_id: str,
    payload: PermissionUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    perm = db.execute(
        select(DevicePermission).where(
            DevicePermission.device_id == device_id,
            DevicePermission.permission_scope == payload.permission_scope,
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if perm is None:
        perm = DevicePermission(
            device_id=device_id,
            permission_scope=payload.permission_scope,
            status=payload.status,
            granted_by_user_id=user.id,
            granted_at=now,
            revoked_at=now if payload.status == "revoked" else None,
            reason=payload.reason,
        )
        db.add(perm)
    else:
        perm.status = payload.status
        perm.reason = payload.reason
        if payload.status == "revoked":
            perm.revoked_at = now
        else:
            perm.revoked_at = None
            perm.granted_at = now
            perm.granted_by_user_id = user.id

    append_audit_log(
        db,
        severity="warning" if payload.status == "revoked" else "info",
        category="security",
        event_type=f"permission.{payload.status}",
        actor_type="user",
        actor_id=user.id,
        device_id=device_id,
        outcome=payload.status,
        details={"scope": payload.permission_scope, "reason": payload.reason},
    )
    db.commit()
    db.refresh(perm)

    await runtime.event_bus.publish(
        f"permission.{payload.status}",
        {"device_id": device_id, "permission_scope": payload.permission_scope},
    )
    return {"permission": serialize_permission(perm)}


@router.post("/devices/{device_id}/rotate-credentials")
def rotate_device_credentials(
    device_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    try:
        rotated = runtime.pairing.rotate_credentials(db=db, device_id=device_id, actor_user_id=user.id)
    except PairingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    return {
        "device_id": device_id,
        "key_version": rotated.key_version,
        "device_token": rotated.device_token,
        "hmac_secret": rotated.hmac_secret,
    }


@router.post("/devices/{device_id}/isolate")
async def isolate_device(
    device_id: str,
    payload: IsolateDeviceRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    try:
        dev = await runtime.pairing.isolate_device(
            db=db,
            device_id=device_id,
            actor_user_id=user.id,
            reason=payload.reason,
            adapter_registry=runtime.adapter_registry,
        )
    except PairingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return {"device": serialize_device(dev, include_details=True)}


@router.post("/devices/{device_id}/restore")
def restore_isolated_device(
    device_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    dev = db.get(Device, device_id)
    if dev is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if dev.credential is None:
        dev.lifecycle_state = "discovered"
        dev.connection_status = "unpaired"
    else:
        dev.lifecycle_state = "authenticated"
        dev.connection_status = "online" if runtime.agent_hub.is_connected(dev.id) else "offline"
        dev.integration_health = "healthy"

    append_audit_log(
        db,
        severity="info",
        category="security",
        event_type="device.restored_from_isolation",
        actor_type="user",
        actor_id=user.id,
        device_id=device_id,
        outcome="restored",
    )
    db.commit()
    db.refresh(dev)
    return {"device": serialize_device(dev, include_details=True)}


@router.post("/devices/{device_id}/commands")
async def execute_device_command(
    device_id: str,
    payload: SubmitCommandRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    try:
        cmd_exec = await runtime.orchestrator.submit_command(
            db=db,
            user_id=user.id,
            device_id=device_id,
            capability_key=payload.capability_key,
            parameters=payload.parameters,
            confirmed=payload.confirmed,
            timeout_ms=payload.timeout_ms,
            max_retries=payload.max_retries,
        )
    except CommandPipelineError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        )
    dev = db.get(Device, device_id)
    return {"command": serialize_command(cmd_exec, dev.name if dev else None)}


@router.get("/commands")
def list_commands(
    device_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(CommandExecution).order_by(CommandExecution.created_at.desc()).limit(limit)
    if device_id:
        stmt = stmt.where(CommandExecution.device_id == device_id)
    if status:
        stmt = stmt.where(CommandExecution.status == status)
    items = db.execute(stmt).scalars().all()
    dev_map = {d.id: d.name for d in db.execute(select(Device)).scalars().all()}
    return {"commands": [serialize_command(c, dev_map.get(c.device_id)) for c in items]}


@router.post("/commands/{command_id}/approve")
async def approve_command(
    command_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    try:
        cmd = await runtime.orchestrator.approve_pending_command(
            db=db,
            command_id=command_id,
            approver_user_id=user.id,
        )
    except CommandPipelineError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})
    dev = db.get(Device, cmd.device_id)
    return {"command": serialize_command(cmd, dev.name if dev else None)}


@router.post("/commands/{command_id}/reject")
async def reject_command(
    command_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    try:
        cmd = await runtime.orchestrator.reject_pending_command(
            db=db,
            command_id=command_id,
            approver_user_id=user.id,
        )
    except CommandPipelineError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})
    dev = db.get(Device, cmd.device_id)
    return {"command": serialize_command(cmd, dev.name if dev else None)}

import base64
import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from home_os.api.deps import get_current_user, get_db, get_runtime
from home_os.api.serializers import (
    serialize_audit,
    serialize_comm_item,
    serialize_command,
    serialize_device,
    serialize_integration,
    serialize_media_item,
    serialize_media_session,
    serialize_permission,
    serialize_rule,
    serialize_run,
    serialize_transfer,
)
from home_os.db.models import (
    ActiveMediaSession,
    AuditLog,
    AutomationRule,
    AutomationRun,
    CommunicationItem,
    Device,
    DeviceCapability,
    DeviceCredential,
    DevicePermission,
    FileTransfer,
    IntegrationConnection,
    MediaItem,
    User,
)
from home_os.runtime import HomeOsRuntime
from home_os.schemas.api import (
    AutomationRuleCreateRequest,
    AutomationRulePatchRequest,
    DeviceToDeviceTransferRequest,
    HomeAssistantConnectRequest,
    MatterConnectRequest,
    NotificationReplyRequest,
)
from home_os.security.audit import append_audit_log, verify_audit_chain
from home_os.security.crypto import generate_secure_token, hash_token
from home_os.services.command_orchestrator import CommandPipelineError

router = APIRouter()


# --- COMMUNICATIONS CENTRE ---


@router.get("/communications")
def list_communications(
    source_type: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
    unread_only: bool = Query(default=False),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(CommunicationItem).where(CommunicationItem.dismissed.is_(False)).order_by(CommunicationItem.received_at.desc())
    if source_type:
        stmt = stmt.where(CommunicationItem.source_type == source_type)
    if device_id:
        stmt = stmt.where(CommunicationItem.device_id == device_id)
    if unread_only:
        stmt = stmt.where(CommunicationItem.is_read.is_(False))
    items = db.execute(stmt.limit(100)).scalars().all()
    dev_map = {d.id: d.name for d in db.execute(select(Device)).scalars().all()}
    return {"items": [serialize_comm_item(i, dev_map.get(i.device_id)) for i in items]}


@router.post("/communications/{comm_id}/read")
def mark_communication_read(
    comm_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = db.get(CommunicationItem, comm_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Communication item not found")
    item.is_read = True
    db.commit()
    return {"item": serialize_comm_item(item)}


@router.post("/communications/{comm_id}/dismiss")
def dismiss_communication(
    comm_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = db.get(CommunicationItem, comm_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Communication item not found")
    item.dismissed = True
    db.commit()
    return {"status": "dismissed"}


@router.post("/communications/{comm_id}/reply")
async def reply_to_communication(
    comm_id: str,
    payload: NotificationReplyRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    try:
        cmd = await runtime.communications.reply_to_notification(
            db=db,
            user_id=user.id,
            communication_id=comm_id,
            reply_text=payload.message,
        )
    except CommandPipelineError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})
    return {"command": serialize_command(cmd)}


@router.post("/communications/sync")
async def sync_communications_from_devices(
    payload: dict | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    device_id = (payload or {}).get("device_id")
    res = await runtime.communications.sync_device_messages(db=db, user_id=user.id, device_id=device_id)
    return res


@router.post("/communications/send-sms")
async def send_direct_sms_from_device(
    payload: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    device_id = str(payload.get("device_id") or "").strip()
    to_addr = str(payload.get("to") or "").strip()
    message = str(payload.get("message") or "").strip()
    if not device_id or not to_addr or not message:
        raise HTTPException(status_code=400, detail="device_id, to, and message are required")
    try:
        cmd = await runtime.orchestrator.submit_command(
            db=db,
            user_id=user.id,
            device_id=device_id,
            capability_key="sms.send",
            parameters={"to": to_addr, "message": message},
        )
    except CommandPipelineError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})
    return {"command": serialize_command(cmd)}


# --- MEDIA & FILE MANAGEMENT ---


@router.get("/media/sessions")
def list_media_sessions(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sessions = db.execute(select(ActiveMediaSession).order_by(ActiveMediaSession.updated_at.desc())).scalars().all()
    dev_map = {d.id: d.name for d in db.execute(select(Device)).scalars().all()}
    return {"sessions": [serialize_media_session(s, dev_map.get(s.device_id)) for s in sessions]}


@router.get("/media/items")
def list_media_items(
    device_id: str | None = Query(default=None),
    media_type: str | None = Query(default=None),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(MediaItem).order_by(MediaItem.indexed_at.desc())
    if device_id:
        stmt = stmt.where(MediaItem.device_id == device_id)
    if media_type:
        stmt = stmt.where(MediaItem.media_type == media_type)
    items = db.execute(stmt.limit(200)).scalars().all()
    return {"items": [serialize_media_item(m) for m in items]}


@router.post("/media/index/{device_id}")
async def index_device_media(
    device_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    try:
        items = await runtime.media_files.index_device_media(db=db, user_id=user.id, device_id=device_id)
    except CommandPipelineError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})
    return {"indexed_count": len(items), "items": [serialize_media_item(m) for m in items]}


@router.get("/media/stream/{media_id}")
async def stream_authorized_media(
    media_id: str,
    range_header: str | None = Header(default=None, alias="Range"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Media item not found")

    try:
        cmd = await runtime.orchestrator.submit_command(
            db=db,
            user_id=user.id,
            device_id=item.device_id,
            capability_key="files.browse",
            parameters={"action": "read_file", "path": item.remote_path},
        )
    except CommandPipelineError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})

    if cmd.status not in ("verified", "acknowledged") or "content_b64" not in cmd.result_json:
        raise HTTPException(status_code=502, detail="Unable to stream media from target device")

    raw = base64.b64decode(cmd.result_json["content_b64"])
    total = len(raw)

    if range_header and range_header.startswith("bytes="):
        spec = range_header.removeprefix("bytes=").split("-", 1)
        start = int(spec[0]) if spec[0] else 0
        end = int(spec[1]) if len(spec) > 1 and spec[1] else total - 1
        end = min(end, total - 1)
        chunk = raw[start : end + 1]
        return Response(
            content=chunk,
            status_code=206,
            media_type=item.mime_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{total}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(len(chunk)),
            },
        )

    return Response(content=raw, media_type=item.mime_type, headers={"Accept-Ranges": "bytes"})


@router.get("/files/browse/{device_id}")
async def browse_device_files(
    device_id: str,
    path: str = Query(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    try:
        listing = await runtime.media_files.browse_device_directory(
            db=db,
            user_id=user.id,
            device_id=device_id,
            path=path,
        )
    except CommandPipelineError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})
    return listing


@router.get("/files/download/{device_id}")
async def download_device_file(
    device_id: str,
    path: str = Query(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    try:
        cmd = await runtime.orchestrator.submit_command(
            db=db,
            user_id=user.id,
            device_id=device_id,
            capability_key="files.browse",
            parameters={"action": "read_file", "path": path},
        )
    except CommandPipelineError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})

    if cmd.status not in ("verified", "acknowledged") or "content_b64" not in cmd.result_json:
        raise HTTPException(status_code=502, detail=cmd.error_message or "File download failed")

    raw = base64.b64decode(cmd.result_json["content_b64"])
    sha = hashlib.sha256(raw).hexdigest()
    filename = path.rstrip("/").split("/")[-1] or "download.bin"

    transfer = FileTransfer(
        direction="download",
        source_device_id=device_id,
        source_path=path,
        target_path="desktop_download",
        file_name=filename,
        size_bytes=len(raw),
        transferred_bytes=len(raw),
        sha256_checksum=sha,
        status="completed",
        authorized_by_user_id=user.id,
        completed_at=datetime.now(timezone.utc),
    )
    db.add(transfer)
    db.commit()

    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-SHA256-Checksum": sha,
        },
    )


@router.post("/files/upload/{device_id}")
async def upload_file_to_device(
    device_id: str,
    target_dir: str = Form(default=""),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    raw = await file.read()
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Single upload exceeds 25 MB safety limit")
    sha = hashlib.sha256(raw).hexdigest()
    b64 = base64.b64encode(raw).decode("ascii")
    fname = file.filename or "upload.bin"

    try:
        cmd = await runtime.orchestrator.submit_command(
            db=db,
            user_id=user.id,
            device_id=device_id,
            capability_key="files.transfer",
            parameters={
                "action": "write_file",
                "target_dir": target_dir,
                "file_name": fname,
                "content_b64": b64,
                "expected_sha256": sha,
            },
        )
    except CommandPipelineError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})

    ok = cmd.status in ("verified", "acknowledged")
    transfer = FileTransfer(
        direction="upload",
        target_device_id=device_id,
        source_path=fname,
        target_path=f"{target_dir.rstrip('/')}/{fname}" if target_dir else fname,
        file_name=fname,
        mime_type=file.content_type or "application/octet-stream",
        size_bytes=len(raw),
        transferred_bytes=len(raw) if ok else 0,
        sha256_checksum=sha,
        status="completed" if ok else "failed",
        error_message=cmd.error_message,
        authorized_by_user_id=user.id,
        completed_at=datetime.now(timezone.utc) if ok else None,
    )
    db.add(transfer)
    db.commit()
    db.refresh(transfer)
    return {"transfer": serialize_transfer(transfer)}


@router.post("/files/transfer")
async def transfer_device_to_device(
    payload: DeviceToDeviceTransferRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    try:
        transfer = await runtime.media_files.transfer_file_between_devices(
            db=db,
            user_id=user.id,
            source_device_id=payload.source_device_id,
            source_path=payload.source_path,
            target_device_id=payload.target_device_id,
            target_dir=payload.target_dir,
        )
    except CommandPipelineError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})
    return {"transfer": serialize_transfer(transfer)}


@router.get("/files/transfers")
def list_file_transfers(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items = db.execute(select(FileTransfer).order_by(FileTransfer.created_at.desc()).limit(100)).scalars().all()
    return {"transfers": [serialize_transfer(t) for t in items]}


# --- AUTOMATION ENGINE ---


@router.get("/automations/rules")
def list_automation_rules(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rules = db.execute(select(AutomationRule).order_by(AutomationRule.created_at.desc())).scalars().all()
    return {"rules": [serialize_rule(r) for r in rules]}


@router.post("/automations/rules")
def create_automation_rule(
    payload: AutomationRuleCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    rule = runtime.automations.create_rule(
        db=db,
        user_id=user.id,
        name=payload.name.strip(),
        description=payload.description,
        trigger_type=payload.trigger_type,
        trigger_config=payload.trigger_config,
        conditions=payload.conditions,
        actions=payload.actions,
        retry_policy=payload.retry_policy,
        requires_confirmation=payload.requires_confirmation,
        enabled=payload.enabled,
    )
    return {"rule": serialize_rule(rule)}


@router.patch("/automations/rules/{rule_id}")
def update_automation_rule(
    rule_id: str,
    payload: AutomationRulePatchRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rule = db.get(AutomationRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Automation rule not found")
    if payload.name is not None:
        rule.name = payload.name.strip()
    if payload.description is not None:
        rule.description = payload.description
    if payload.enabled is not None:
        rule.enabled = payload.enabled
    if payload.requires_confirmation is not None:
        rule.requires_confirmation = payload.requires_confirmation

    append_audit_log(
        db,
        severity="info",
        category="automation",
        event_type="automation.rule_updated",
        actor_type="user",
        actor_id=user.id,
        outcome="success",
        details={"rule_id": rule_id, "enabled": rule.enabled},
    )
    db.commit()
    db.refresh(rule)
    return {"rule": serialize_rule(rule)}


@router.delete("/automations/rules/{rule_id}")
def delete_automation_rule(
    rule_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rule = db.get(AutomationRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Automation rule not found")
    db.delete(rule)
    append_audit_log(
        db,
        severity="info",
        category="automation",
        event_type="automation.rule_deleted",
        actor_type="user",
        actor_id=user.id,
        outcome="deleted",
        details={"rule_id": rule_id},
    )
    db.commit()
    return {"status": "deleted"}


@router.post("/automations/rules/{rule_id}/trigger")
async def manual_trigger_rule(
    rule_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    rule = db.get(AutomationRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Automation rule not found")
    run = await runtime.automations.execute_rule(
        db=db,
        rule=rule,
        trigger_reason=f"manual_operator_trigger:{user.username}",
        confirmed=False,
    )
    return {"run": serialize_run(run, rule.name)}


@router.get("/automations/runs")
def list_automation_runs(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runs = db.execute(select(AutomationRun).order_by(AutomationRun.started_at.desc()).limit(80)).scalars().all()
    rule_map = {r.id: r.name for r in db.execute(select(AutomationRule)).scalars().all()}
    return {"runs": [serialize_run(r, rule_map.get(r.rule_id)) for r in runs]}


@router.post("/automations/runs/{run_id}/approve")
async def approve_automation_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    try:
        run = await runtime.automations.approve_run(db=db, run_id=run_id, approver_user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    rule = db.get(AutomationRule, run.rule_id)
    return {"run": serialize_run(run, rule.name if rule else None)}


# --- SMART HOME INTEGRATIONS (HOME ASSISTANT & MATTER) ---


@router.get("/integrations")
def list_integrations(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conns = db.execute(select(IntegrationConnection).order_by(IntegrationConnection.created_at.desc())).scalars().all()
    return {"integrations": [serialize_integration(c) for c in conns]}


@router.post("/integrations/home-assistant")
async def connect_home_assistant(
    payload: HomeAssistantConnectRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    encrypted_token = runtime.vault.encrypt(payload.access_token, aad="integration:home_assistant")
    runtime.home_assistant_adapter.configure_connection(
        base_url=payload.endpoint_url,
        access_token=payload.access_token,
    )

    status = "connected"
    health_msg = "Connected and verified"
    ha_meta = {}
    try:
        ha_meta = await runtime.home_assistant_adapter.test_connection()
    except Exception as exc:
        status = "unreachable"
        health_msg = f"Saved credentials encrypted; endpoint currently unreachable ({exc})"

    conn = IntegrationConnection(
        integration_type="home_assistant",
        name=payload.name,
        endpoint_url=payload.endpoint_url.rstrip("/"),
        encrypted_credentials=encrypted_token,
        status=status,
        health_message=health_msg,
        config_json=ha_meta,
        last_sync_at=datetime.now(timezone.utc) if status == "connected" else None,
    )
    db.add(conn)
    append_audit_log(
        db,
        severity="info",
        category="integrations",
        event_type="integration.home_assistant_configured",
        actor_type="user",
        actor_id=user.id,
        outcome=status,
        details={"endpoint_url": conn.endpoint_url},
    )
    db.commit()
    db.refresh(conn)

    synced_devices = []
    if status == "connected":
        synced_devices = await _sync_ha_entities_into_inventory(db, runtime, user.id)

    return {"integration": serialize_integration(conn), "synced_devices": [serialize_device(d) for d in synced_devices]}


async def _sync_ha_entities_into_inventory(db: Session, runtime: HomeOsRuntime, user_id: str) -> list[Device]:
    endpoints = await runtime.home_assistant_adapter.discover()
    now = datetime.now(timezone.utc)
    synced: list[Device] = []
    for ep in endpoints:
        ident = await runtime.home_assistant_adapter.identify(ep)
        entity_id = ep.raw_properties["entity_id"]
        dev = db.execute(select(Device).where(Device.hardware_fingerprint == ep.hardware_fingerprint)).scalar_one_or_none()
        if dev is None:
            dev = Device(
                hardware_fingerprint=ep.hardware_fingerprint,
                name=ident.name,
                category=ident.category,
                manufacturer=ident.manufacturer,
                model=ident.model,
                lifecycle_state="authenticated",
                discovery_source="home_assistant",
                adapter_type="home_assistant",
                connection_status="online",
                health_score=100,
                integration_health="healthy",
                metadata_json={"entity_id": entity_id, "state": ep.raw_properties.get("state")},
                first_seen_at=now,
                last_seen_at=now,
                enrolled_at=now,
            )
            db.add(dev)
            db.flush()
            tok = generate_secure_token(24)
            sec = generate_secure_token(24)
            db.add(
                DeviceCredential(
                    device_id=dev.id,
                    token_hash=hash_token(tok),
                    encrypted_hmac_secret=runtime.vault.encrypt(sec, aad=f"device:{dev.id}"),
                )
            )
            for scope in ("smarthome:read", "smarthome:control", "media:control", "volume:control"):
                db.add(DevicePermission(device_id=dev.id, permission_scope=scope, status="granted", granted_by_user_id=user_id))

        runtime.home_assistant_adapter.bind_device_entity(dev.id, entity_id)
        caps = await runtime.home_assistant_adapter.get_capabilities(dev.id)
        runtime.pairing.sync_device_capabilities(db, dev.id, [c.model_dump() for c in caps])
        synced.append(dev)

    db.commit()
    return synced


@router.post("/integrations/matter")
def connect_matter_server(
    payload: MatterConnectRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    runtime.matter_adapter.configure_server(payload.ws_url)
    conn = IntegrationConnection(
        integration_type="matter_server",
        name=payload.name,
        endpoint_url=payload.ws_url,
        encrypted_credentials=runtime.vault.encrypt("matter_local_ws", aad="integration:matter"),
        status="configured",
        health_message="Matter WebSocket bridge registered; ready for node discovery & commissioning",
    )
    db.add(conn)
    append_audit_log(
        db,
        severity="info",
        category="integrations",
        event_type="integration.matter_configured",
        actor_type="user",
        actor_id=user.id,
        outcome="configured",
        details={"ws_url": payload.ws_url},
    )
    db.commit()
    db.refresh(conn)
    return {"integration": serialize_integration(conn)}


@router.post("/integrations/{integration_id}/sync")
async def sync_integration(
    integration_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    conn = db.get(IntegrationConnection, integration_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="Integration not found")

    if conn.integration_type == "home_assistant":
        token = runtime.vault.decrypt(conn.encrypted_credentials, aad="integration:home_assistant")
        runtime.home_assistant_adapter.configure_connection(base_url=conn.endpoint_url, access_token=token)
        try:
            await runtime.home_assistant_adapter.test_connection()
            synced = await _sync_ha_entities_into_inventory(db, runtime, user.id)
            conn.status = "connected"
            conn.health_message = f"Synced {len(synced)} entities"
            conn.last_sync_at = datetime.now(timezone.utc)
            db.commit()
            return {"integration": serialize_integration(conn), "synced_count": len(synced)}
        except Exception as exc:
            conn.status = "unreachable"
            conn.health_message = str(exc)[:240]
            db.commit()
            raise HTTPException(status_code=502, detail=f"Home Assistant sync failed: {exc}")

    return {"integration": serialize_integration(conn), "synced_count": 0}


@router.delete("/integrations/{integration_id}")
def delete_integration(
    integration_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = db.get(IntegrationConnection, integration_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="Integration not found")
    db.delete(conn)
    append_audit_log(
        db,
        severity="warning",
        category="integrations",
        event_type="integration.removed",
        actor_type="user",
        actor_id=user.id,
        outcome="removed",
        details={"integration_id": integration_id},
    )
    db.commit()
    return {"status": "deleted"}


# --- PERMISSIONS & SECURITY CENTRE ---


@router.get("/security/permissions")
def list_all_permissions(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    devices = db.execute(select(Device).order_by(Device.name.asc())).scalars().all()
    matrix = []
    for d in devices:
        if d.lifecycle_state not in ("enrolled", "authenticated", "isolated"):
            continue
        matrix.append(
            {
                "device_id": d.id,
                "device_name": d.name,
                "category": d.category,
                "adapter_type": d.adapter_type,
                "lifecycle_state": d.lifecycle_state,
                "key_version": d.credential.key_version if d.credential else None,
                "permissions": [serialize_permission(p) for p in d.permissions],
            }
        )
    return {"matrix": matrix}


@router.get("/security/audit-logs")
def list_audit_logs(
    severity: str | None = Query(default=None),
    category: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if severity:
        stmt = stmt.where(AuditLog.severity == severity)
    if category:
        stmt = stmt.where(AuditLog.category == category)
    logs = db.execute(stmt).scalars().all()
    return {"logs": [serialize_audit(a) for a in logs]}


@router.get("/security/audit-verify")
def verify_audit_log_integrity(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    report = verify_audit_chain(db)
    return {
        "valid": report.valid,
        "checked_entries": report.checked_entries,
        "head_hash": report.head_hash,
        "broken_at_id": report.broken_at_id,
        "reason": report.reason,
    }


@router.post("/security/emergency-lockdown")
async def trigger_emergency_lockdown(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    summary = await runtime.pairing.emergency_lockdown(
        db=db,
        actor_user_id=user.id,
        adapter_registry=runtime.adapter_registry,
    )
    return {"status": "lockdown_active", **summary}

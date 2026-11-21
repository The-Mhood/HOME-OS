import hmac
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from home_os.adapters.base import CapabilityDescriptor
from home_os.api.deps import get_db, get_runtime, verify_authenticated_device
from home_os.db.models import Device, DeviceCredential
from home_os.runtime import HomeOsRuntime
from home_os.schemas.api import AgentCommunicationPayload, AgentTelemetryPayload
from home_os.security.audit import append_audit_log
from home_os.security.crypto import hash_token
from home_os.security.replay import ReplayAttackError

router = APIRouter()


@router.post("/agent/telemetry")
async def publish_agent_telemetry(
    payload: AgentTelemetryPayload,
    device: Device = Depends(verify_authenticated_device),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    prev_battery = device.battery_level
    device.connection_status = "online"
    device.integration_health = "healthy"
    device.health_score = payload.health_score
    if payload.battery_level is not None:
        device.battery_level = payload.battery_level
    if payload.is_charging is not None:
        device.is_charging = payload.is_charging
    device.health_details_json = payload.attributes
    device.last_seen_at = datetime.now(timezone.utc)

    if payload.capabilities is not None:
        runtime.pairing.sync_device_capabilities(db, device.id, payload.capabilities)

    if payload.media_session:
        ms = payload.media_session
        runtime.media_files.upsert_media_session(
            db=db,
            device_id=device.id,
            app_name=str(ms.get("app_name", "System Media")),
            title=str(ms.get("title", "Unknown Track")),
            artist=ms.get("artist"),
            album=ms.get("album"),
            playback_state=str(ms.get("playback_state", "playing")),
            position_ms=int(ms.get("position_ms", 0)),
            duration_ms=int(ms.get("duration_ms", 0)),
            volume_percent=ms.get("volume_percent"),
            is_muted=bool(ms.get("is_muted", False)),
        )

    db.commit()

    if payload.battery_level is not None and payload.battery_level != prev_battery:
        await runtime.automations.evaluate_device_state_change(
            db=db,
            device_id=device.id,
            field="battery_level",
            new_value=payload.battery_level,
        )

    await runtime.event_bus.publish(
        "device.telemetry",
        {
            "device_id": device.id,
            "name": device.name,
            "battery_level": device.battery_level,
            "health_score": device.health_score,
            "attributes": payload.attributes,
        },
    )
    return {"status": "accepted"}


@router.post("/agent/communications")
async def publish_agent_communication(
    payload: AgentCommunicationPayload,
    device: Device = Depends(verify_authenticated_device),
    db: Session = Depends(get_db),
    runtime: HomeOsRuntime = Depends(get_runtime),
):
    try:
        item = await runtime.communications.ingest_device_communication(
            db=db,
            device_id=device.id,
            source_type=payload.source_type,
            app_name=payload.app_name,
            sender=payload.sender,
            body_preview=payload.body_preview,
            title=payload.title,
            app_package=payload.app_package,
            can_reply=payload.can_reply,
            reply_action_id=payload.reply_action_id,
            external_id=payload.external_id,
            metadata=payload.metadata,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    return {"id": item.id, "access_level": item.access_level}


@router.websocket("/agent/ws")
async def agent_bidirectional_websocket(
    websocket: WebSocket,
    device_id: str = Query(...),
    token: str = Query(...),
    timestamp: int = Query(...),
    nonce: str = Query(...),
    signature: str = Query(...),
):
    runtime: HomeOsRuntime = websocket.app.state.runtime

    with runtime.SessionLocal() as db:
        device = db.get(Device, device_id)
        if device is None or device.lifecycle_state != "authenticated" or device.connection_status == "isolated":
            await websocket.close(code=4403, reason="Device not authorized")
            return
        cred = db.execute(select(DeviceCredential).where(DeviceCredential.device_id == device_id)).scalar_one_or_none()
        if cred is None or cred.revoked_at is not None:
            await websocket.close(code=4401, reason="Credentials invalid")
            return
        if not hmac.compare_digest(hash_token(token), cred.token_hash):
            await websocket.close(code=4401, reason="Token mismatch")
            return

        hmac_secret = runtime.vault.decrypt(cred.encrypted_hmac_secret, aad=f"device:{device_id}")
        try:
            runtime.replay_guard.verify_signed_request(
                device_id=device_id,
                secret=hmac_secret,
                method="WS",
                path="/api/v1/agent/ws",
                timestamp=timestamp,
                nonce=nonce,
                body=b"",
                signature=signature,
            )
        except ReplayAttackError as exc:
            append_audit_log(
                db,
                severity="critical",
                category="security",
                event_type="agent.ws_replay_blocked",
                actor_type="device",
                actor_id=device_id,
                device_id=device_id,
                outcome="blocked",
                details={"reason": str(exc)},
            )
            db.commit()
            await websocket.close(code=4401, reason="Replay/signature failure")
            return

        adapter_type = device.adapter_type
        device.connection_status = "online"
        device.integration_health = "healthy"
        device.last_seen_at = datetime.now(timezone.utc)
        db.commit()

    await websocket.accept()
    session = runtime.agent_hub.register_session(device_id, adapter_type, websocket)
    await runtime.event_bus.publish("device.connected", {"device_id": device_id, "adapter_type": adapter_type})
    with runtime.SessionLocal() as db:
        await runtime.automations.evaluate_event(db=db, event_type="device.connected", event_payload={"device_id": device_id})

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            mtype = msg.get("type")

            if mtype == "command.receipt":
                runtime.agent_hub.resolve_command_receipt(msg)

            elif mtype == "telemetry":
                data = msg.get("payload") or {}
                session.last_telemetry = data
                with runtime.SessionLocal() as db:
                    dev = db.get(Device, device_id)
                    if dev is not None:
                        dev.last_seen_at = datetime.now(timezone.utc)
                        dev.connection_status = "online"
                        dev.health_score = int(data.get("health_score", 100))
                        if "battery_level" in data and data["battery_level"] is not None:
                            dev.battery_level = int(data["battery_level"])
                        if "is_charging" in data:
                            dev.is_charging = data["is_charging"]
                        dev.health_details_json = data.get("attributes") or data
                        if "capabilities" in data and isinstance(data["capabilities"], list):
                            runtime.pairing.sync_device_capabilities(db, device_id, data["capabilities"])
                            session.capabilities = [CapabilityDescriptor(**c) for c in data["capabilities"]]
                        db.commit()
                await runtime.event_bus.publish(
                    "device.telemetry",
                    {"device_id": device_id, "telemetry": data},
                )

            elif mtype == "communication":
                cdata = msg.get("payload") or {}
                with runtime.SessionLocal() as db:
                    try:
                        await runtime.communications.ingest_device_communication(
                            db=db,
                            device_id=device_id,
                            source_type=str(cdata.get("source_type", "android_notification")),
                            app_name=str(cdata.get("app_name", "Companion")),
                            sender=str(cdata.get("sender", "System")),
                            body_preview=str(cdata.get("body_preview", "")),
                            title=cdata.get("title"),
                            app_package=cdata.get("app_package"),
                            can_reply=bool(cdata.get("can_reply", False)),
                            reply_action_id=cdata.get("reply_action_id"),
                            external_id=cdata.get("external_id"),
                            metadata=cdata.get("metadata"),
                        )
                    except PermissionError:
                        pass

            elif mtype == "ping":
                await websocket.send_json({"type": "pong", "ts": datetime.now(timezone.utc).isoformat()})

    except WebSocketDisconnect:
        pass
    finally:
        runtime.agent_hub.unregister_session(device_id)
        with runtime.SessionLocal() as db:
            dev = db.get(Device, device_id)
            if dev is not None and dev.lifecycle_state != "isolated":
                dev.connection_status = "offline"
                db.commit()
            await runtime.automations.evaluate_event(
                db=db,
                event_type="device.disconnected",
                event_payload={"device_id": device_id},
            )
        await runtime.event_bus.publish("device.disconnected", {"device_id": device_id})

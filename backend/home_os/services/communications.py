from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from home_os.db.models import CommandExecution, CommunicationItem, Device, DevicePermission
from home_os.security.audit import append_audit_log
from home_os.services.command_orchestrator import CommandOrchestrator, CommandPipelineError
from home_os.services.event_bus import EventBus

SOURCE_PERMISSION_MAP = {
    "android_notification": "notifications:read",
    "whatsapp_notification": "notifications:read",
    "messaging_notification": "notifications:read",
    "call_event": "notifications:read",
    "sms": "sms:read",
    "call_log": "calls:read",
}


class CommunicationsService:
    """Authorized communication ingestion & action dispatcher.

    Strictly distinguishes notification-level mirroring ('notification_only') from
    direct OS permission access ('direct_permission') and never accesses private app DBs.
    """

    def __init__(self, orchestrator: CommandOrchestrator, event_bus: EventBus):
        self.orchestrator = orchestrator
        self.event_bus = event_bus

    async def ingest_device_communication(
        self,
        *,
        db: Session,
        device_id: str,
        source_type: str,
        app_name: str,
        sender: str,
        body_preview: str,
        title: str | None = None,
        app_package: str | None = None,
        can_reply: bool = False,
        reply_action_id: str | None = None,
        external_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CommunicationItem:
        device = db.get(Device, device_id)
        if device is None or device.lifecycle_state != "authenticated":
            raise PermissionError("Device is not authenticated to publish communication events")

        required_scope = SOURCE_PERMISSION_MAP.get(source_type, "notifications:read")
        perm = db.execute(
            select(DevicePermission).where(
                DevicePermission.device_id == device_id,
                DevicePermission.permission_scope == required_scope,
                DevicePermission.status == "granted",
            )
        ).scalar_one_or_none()

        if perm is None or perm.revoked_at is not None:
            append_audit_log(
                db,
                severity="warning",
                category="communications",
                event_type="communication.ingest_blocked_no_permission",
                actor_type="device",
                actor_id=device_id,
                device_id=device_id,
                outcome="denied",
                details={"source_type": source_type, "required_permission": required_scope},
            )
            db.commit()
            raise PermissionError(f"Permission '{required_scope}' is not granted on device '{device.name}'")

        access_level = "direct_permission" if source_type in ("sms", "call_log") else "notification_only"
        item = CommunicationItem(
            device_id=device_id,
            source_type=source_type,
            access_level=access_level,
            app_package=app_package,
            app_name=app_name,
            sender=sender,
            title=title,
            body_preview=body_preview,
            can_reply=can_reply,
            reply_action_id=reply_action_id,
            external_id=external_id,
            metadata_json=metadata or {},
            received_at=datetime.now(timezone.utc),
        )
        db.add(item)
        db.commit()
        db.refresh(item)

        await self.event_bus.publish(
            "communication.received",
            {
                "id": item.id,
                "device_id": device_id,
                "source_type": item.source_type,
                "access_level": item.access_level,
                "app_name": item.app_name,
                "sender": item.sender,
                "title": item.title,
                "body_preview": item.body_preview,
                "can_reply": item.can_reply,
            },
        )
        return item

    async def reply_to_notification(
        self,
        *,
        db: Session,
        user_id: str,
        communication_id: str,
        reply_text: str,
    ) -> CommandExecution:
        item = db.get(CommunicationItem, communication_id)
        if item is None:
            raise CommandPipelineError("ITEM_NOT_FOUND", "Communication item not found", status_code=404)
        if not item.can_reply or not item.reply_action_id:
            raise CommandPipelineError(
                "REPLY_UNSUPPORTED",
                "This notification does not advertise an inline reply or SMS target",
                status_code=400,
            )

        if item.source_type == "sms" or item.reply_action_id.startswith("sms:"):
            to_addr = item.reply_action_id.removeprefix("sms:").strip() or item.sender
            return await self.orchestrator.submit_command(
                db=db,
                user_id=user_id,
                device_id=item.device_id,
                capability_key="sms.send",
                parameters={"to": to_addr, "message": reply_text},
            )

        return await self.orchestrator.submit_command(
            db=db,
            user_id=user_id,
            device_id=item.device_id,
            capability_key="notifications.reply",
            parameters={
                "reply_action_id": item.reply_action_id,
                "external_id": item.external_id,
                "message": reply_text,
            },
        )

    async def sync_device_messages(
        self,
        *,
        db: Session,
        user_id: str,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        stmt = select(Device).where(
            Device.lifecycle_state == "authenticated",
            Device.adapter_type.in_(["android_adb", "ios_native", "local_host"]),
        )
        if device_id:
            stmt = stmt.where(Device.id == device_id)
        devices = db.execute(stmt).scalars().all()

        existing_ext_ids = {
            (row[0], row[1])
            for row in db.execute(
                select(CommunicationItem.device_id, CommunicationItem.external_id).where(
                    CommunicationItem.external_id.is_not(None)
                )
            ).all()
        }

        synced_sms = 0
        synced_notifs = 0
        warnings: list[str] = []

        for dev in devices:
            try:
                cmd = await self.orchestrator.submit_command(
                    db=db,
                    user_id=user_id,
                    device_id=dev.id,
                    capability_key="sms.list",
                    parameters={"limit": 30},
                )
                res = cmd.result_json or {}
                if res.get("needs_full_disk_access"):
                    warnings.append(str(res.get("reason")))
                for m in res.get("messages") or []:
                    ext_id = f"sms-{m.get('id')}-{m.get('timestamp_ms')}"
                    if (dev.id, ext_id) in existing_ext_ids:
                        continue
                    existing_ext_ids.add((dev.id, ext_id))
                    ts_ms = int(m.get("timestamp_ms") or 0)
                    rec_dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc) if ts_ms > 0 else datetime.now(timezone.utc)
                    direction = m.get("direction", "incoming")
                    addr = str(m.get("address") or "Unknown")
                    prefix = "You → " if direction == "outgoing" else ""
                    db.add(
                        CommunicationItem(
                            device_id=dev.id,
                            source_type="sms",
                            access_level="direct_permission",
                            app_name=str(m.get("service") or "SMS Messages"),
                            sender=addr,
                            title=f"{prefix}{addr}",
                            body_preview=str(m.get("body") or "")[:500],
                            is_read=bool(m.get("read", True)),
                            can_reply=(addr != "Unknown"),
                            reply_action_id=f"sms:{addr}" if addr != "Unknown" else None,
                            external_id=ext_id,
                            metadata_json={"direction": direction},
                            received_at=rec_dt,
                        )
                    )
                    synced_sms += 1
            except Exception as exc:
                warnings.append(f"{dev.name}: {exc}")

            if dev.adapter_type == "android_adb":
                try:
                    ncmd = await self.orchestrator.submit_command(
                        db=db,
                        user_id=user_id,
                        device_id=dev.id,
                        capability_key="notifications.sync",
                        parameters={},
                    )
                    for n in (ncmd.result_json or {}).get("notifications") or []:
                        ext_id = f"notif-{n.get('package_name')}-{hash(n.get('title','') + n.get('body',''))}"
                        if (dev.id, ext_id) in existing_ext_ids:
                            continue
                        existing_ext_ids.add((dev.id, ext_id))
                        db.add(
                            CommunicationItem(
                                device_id=dev.id,
                                source_type="android_notification",
                                access_level="notification_only",
                                app_package=n.get("package_name"),
                                app_name=str(n.get("app_name") or "Android"),
                                sender=str(n.get("title") or n.get("app_name") or "Notification"),
                                title=str(n.get("title") or ""),
                                body_preview=str(n.get("body") or "")[:500],
                                is_read=False,
                                can_reply=False,
                                external_id=ext_id,
                                received_at=datetime.now(timezone.utc),
                            )
                        )
                        synced_notifs += 1
                except Exception:
                    pass

        db.commit()
        return {"synced_messages": synced_sms, "synced_notifications": synced_notifs, "warnings": warnings}

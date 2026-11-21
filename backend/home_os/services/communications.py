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
                "This notification does not advertise an Android RemoteInput inline reply action",
                status_code=400,
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

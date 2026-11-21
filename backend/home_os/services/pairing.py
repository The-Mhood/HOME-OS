import dataclasses
import io
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import qrcode
import qrcode.image.svg
from sqlalchemy import select
from sqlalchemy.orm import Session

from home_os.adapters.base import CapabilityDescriptor
from home_os.db.models import (
    AutomationRule,
    Device,
    DeviceCapability,
    DeviceCredential,
    DevicePermission,
    PairingSession,
)
from home_os.security.audit import append_audit_log
from home_os.security.crypto import (
    CredentialVault,
    generate_pairing_pin,
    generate_secure_token,
    hash_token,
)
from home_os.services.event_bus import EventBus


class PairingError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclasses.dataclass(frozen=True)
class PairingInitiationResult:
    session_id: str
    pin: str
    qr_token: str
    qr_uri: str
    deep_link_uri: str
    qr_svg: str
    challenge_nonce: str
    expires_at: datetime


@dataclasses.dataclass(frozen=True)
class EnrollmentCompletionResult:
    device: Device
    device_token: str
    hmac_secret: str
    key_version: int


def _ensure_utc(dt_val: datetime) -> datetime:
    if dt_val.tzinfo is None:
        return dt_val.replace(tzinfo=timezone.utc)
    return dt_val.astimezone(timezone.utc)


def _render_qr_svg(data: str) -> str:
    """Renders an ISO-18004 compliant, Retina-crisp QR SVG with a full 4-module white quiet zone."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    size = len(matrix)
    path_cmds: list[str] = []
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                path_cmds.append(f"M{x},{y}h1v1h-1z")
    d_attr = "".join(path_cmds)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'width="210" height="210" shape-rendering="crispEdges">'
        f'<rect width="{size}" height="{size}" fill="#FFFFFF"/>'
        f'<path d="{d_attr}" fill="#000000"/>'
        f"</svg>"
    )


DEFAULT_PLATFORM_CAPABILITIES: dict[str, list[dict[str, Any]]] = {
    "android_companion": [
        {"capability_key": "telemetry.status", "category": "telemetry", "display_name": "Battery & System Status", "supported": True, "requires_permission": "telemetry:read"},
        {"capability_key": "notifications.sync", "category": "communications", "display_name": "Notification Synchronization", "supported": True, "requires_permission": "notifications:read"},
        {"capability_key": "notifications.reply", "category": "communications", "display_name": "Notification Inline Reply (Messages / WhatsApp)", "supported": True, "requires_permission": "notifications:reply"},
        {"capability_key": "flashlight.set", "category": "hardware", "display_name": "Flashlight Torch", "supported": True, "requires_permission": "flashlight:control", "parameters_schema": {"required": ["enabled"], "properties": {"enabled": {"type": "boolean"}}}},
        {"capability_key": "media.control", "category": "media", "display_name": "Media Playback Transport", "supported": True, "requires_permission": "media:control", "parameters_schema": {"required": ["action"], "properties": {"action": {"type": "string", "enum": ["play", "pause", "next", "previous", "stop"]}}}},
        {"capability_key": "volume.set", "category": "audio", "display_name": "Stream Volume Control", "supported": True, "requires_permission": "volume:control", "parameters_schema": {"required": ["level"], "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}}}},
        {"capability_key": "apps.launch", "category": "applications", "display_name": "Launch Installed App via Intent", "supported": True, "requires_permission": "apps:launch", "parameters_schema": {"required": ["package_name"], "properties": {"package_name": {"type": "string"}}}},
        {"capability_key": "files.browse", "category": "files", "display_name": "Authorized Storage Access (SAF)", "supported": True, "requires_permission": "files:browse"},
        {"capability_key": "screen.share", "category": "screen", "display_name": "MediaProjection Screen Sharing", "supported": True, "requires_permission": "screen:view", "requires_confirmation": True},
        {"capability_key": "input.remote", "category": "input", "display_name": "Authorized Accessibility Gesture Input", "supported": False, "unavailable_reason": "AccessibilityService is disabled by default; enable in Android Accessibility Settings to unlock.", "requires_permission": "input:remote", "requires_confirmation": True},
        {"capability_key": "sms.send", "category": "communications", "display_name": "Direct SMS Transmission", "supported": False, "unavailable_reason": "Restricted by Google Play SMS role policy unless direct companion SMS permission is granted on device.", "requires_permission": "sms:send", "requires_confirmation": True},
        {"capability_key": "calls.log", "category": "communications", "display_name": "Call History Synchronization", "supported": False, "unavailable_reason": "Requires explicit READ_CALL_LOG runtime grant on eligible non-Play build; call notifications remain available via NotificationListenerService.", "requires_permission": "calls:read"},
    ],
    "ios_companion": [
        {"capability_key": "telemetry.status", "category": "telemetry", "display_name": "Battery, Thermal & Storage Telemetry", "supported": True, "requires_permission": "telemetry:read"},
        {"capability_key": "shortcuts.run", "category": "automation", "display_name": "Run Authorized Apple Shortcut / AppIntent", "supported": True, "requires_permission": "apps:launch", "parameters_schema": {"required": ["shortcut_name"], "properties": {"shortcut_name": {"type": "string"}}}},
        {"capability_key": "files.browse", "category": "files", "display_name": "User-Selected Folder Bookmark Sharing", "supported": True, "requires_permission": "files:browse"},
        {"capability_key": "media.control", "category": "media", "display_name": "System Music Player Controls", "supported": True, "requires_permission": "media:control", "parameters_schema": {"required": ["action"], "properties": {"action": {"type": "string", "enum": ["play", "pause", "next", "previous"]}}}},
        {"capability_key": "homekit.control", "category": "smarthome", "display_name": "HomeKit & Matter Bridge Control", "supported": True, "requires_permission": "smarthome:control"},
        {"capability_key": "notifications.sync", "category": "communications", "display_name": "System-Wide Notification Access", "supported": False, "unavailable_reason": "Prohibited by Apple iOS app sandbox; third-party iOS apps cannot read other apps' notifications.", "requires_permission": "notifications:read"},
        {"capability_key": "sms.send", "category": "communications", "display_name": "Unattended SMS / iMessage Access", "supported": False, "unavailable_reason": "Prohibited by Apple iOS sandbox; no public API exists for reading or sending background SMS/WhatsApp.", "requires_permission": "sms:send", "requires_confirmation": True},
        {"capability_key": "calls.log", "category": "communications", "display_name": "Call History Access", "supported": False, "unavailable_reason": "Prohibited by Apple iOS sandbox; CallLog database is inaccessible to third-party apps.", "requires_permission": "calls:read"},
        {"capability_key": "input.remote", "category": "input", "display_name": "System-Wide Remote Touch Control", "supported": False, "unavailable_reason": "Prohibited by Apple iOS security architecture; remote touch injection into other apps is not permitted.", "requires_permission": "input:remote", "requires_confirmation": True},
    ],
    "linux_agent": [
        {"capability_key": "telemetry.status", "category": "telemetry", "display_name": "CPU, Memory, Disk & Network Status", "supported": True, "requires_permission": "telemetry:read"},
        {"capability_key": "files.browse", "category": "files", "display_name": "Authorized Directory File Browser", "supported": True, "requires_permission": "files:browse"},
        {"capability_key": "files.transfer", "category": "files", "display_name": "SHA-256 Verified File Read/Write", "supported": True, "requires_permission": "files:write"},
        {"capability_key": "processes.list", "category": "applications", "display_name": "Active User Process Inspection", "supported": True, "requires_permission": "apps:manage"},
        {"capability_key": "apps.launch", "category": "applications", "display_name": "Launch Allowlisted Desktop Command", "supported": True, "requires_permission": "apps:launch", "parameters_schema": {"required": ["app_id"], "properties": {"app_id": {"type": "string"}}}},
        {"capability_key": "volume.set", "category": "audio", "display_name": "Audio Output Volume (PipeWire/PulseAudio/ALSA)", "supported": True, "requires_permission": "volume:control", "parameters_schema": {"required": ["level"], "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}}}},
        {"capability_key": "media.control", "category": "media", "display_name": "MPRIS2 Media Session Control", "supported": True, "requires_permission": "media:control"},
        {"capability_key": "screen.capture", "category": "screen", "display_name": "Authorized Display Snapshot (X11/Wayland)", "supported": True, "requires_permission": "screen:view", "requires_confirmation": True},
        {"capability_key": "input.remote", "category": "input", "display_name": "Remote Pointer/Key Input", "supported": False, "unavailable_reason": "Requires active X11 session or Wayland compositor portal consent.", "requires_permission": "input:remote", "requires_confirmation": True},
    ],
    "windows_agent": [
        {"capability_key": "telemetry.status", "category": "telemetry", "display_name": "WMI & Performance Counter Telemetry", "supported": True, "requires_permission": "telemetry:read"},
        {"capability_key": "files.browse", "category": "files", "display_name": "Authorized Folder File Browser", "supported": True, "requires_permission": "files:browse"},
        {"capability_key": "files.transfer", "category": "files", "display_name": "Verified File Transfer", "supported": True, "requires_permission": "files:write"},
        {"capability_key": "apps.launch", "category": "applications", "display_name": "Launch Registered Application", "supported": True, "requires_permission": "apps:launch", "parameters_schema": {"required": ["app_id"], "properties": {"app_id": {"type": "string"}}}},
        {"capability_key": "processes.list", "category": "applications", "display_name": "Session Process Monitor", "supported": True, "requires_permission": "apps:manage"},
        {"capability_key": "volume.set", "category": "audio", "display_name": "CoreAudio Master Volume", "supported": True, "requires_permission": "volume:control", "parameters_schema": {"required": ["level"], "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}}}},
        {"capability_key": "media.control", "category": "media", "display_name": "Windows GSMTC Media Transport", "supported": True, "requires_permission": "media:control"},
        {"capability_key": "screen.capture", "category": "screen", "display_name": "Authorized Desktop Frame Capture", "supported": True, "requires_permission": "screen:view", "requires_confirmation": True},
        {"capability_key": "input.remote", "category": "input", "display_name": "Win32 SendInput Remote Interaction (Non-UAC)", "supported": True, "requires_permission": "input:remote", "requires_confirmation": True},
    ],
    "macos_agent": [
        {"capability_key": "telemetry.status", "category": "telemetry", "display_name": "macOS Power, Thermal & Storage Telemetry", "supported": True, "requires_permission": "telemetry:read"},
        {"capability_key": "files.browse", "category": "files", "display_name": "TCC-Authorized Folder Browser", "supported": True, "requires_permission": "files:browse"},
        {"capability_key": "files.transfer", "category": "files", "display_name": "Verified File Transfer", "supported": True, "requires_permission": "files:write"},
        {"capability_key": "apps.launch", "category": "applications", "display_name": "NSWorkspace Application Launch", "supported": True, "requires_permission": "apps:launch", "parameters_schema": {"required": ["bundle_id"], "properties": {"bundle_id": {"type": "string"}}}},
        {"capability_key": "volume.set", "category": "audio", "display_name": "CoreAudio Output Volume", "supported": True, "requires_permission": "volume:control", "parameters_schema": {"required": ["level"], "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}}}},
        {"capability_key": "media.control", "category": "media", "display_name": "macOS Media Playback Controls", "supported": True, "requires_permission": "media:control"},
        {"capability_key": "screen.capture", "category": "screen", "display_name": "ScreenCaptureKit Viewing (Requires TCC Screen Recording)", "supported": False, "unavailable_reason": "Awaiting macOS TCC Screen Recording permission grant on target Mac.", "requires_permission": "screen:view", "requires_confirmation": True},
        {"capability_key": "input.remote", "category": "input", "display_name": "Quartz Event Input (Requires TCC Accessibility)", "supported": False, "unavailable_reason": "Awaiting macOS TCC Accessibility permission grant (AXIsProcessTrusted).", "requires_permission": "input:remote", "requires_confirmation": True},
    ],
    "google_tv": [
        {"capability_key": "tv.navigate", "category": "tv", "display_name": "D-Pad & Remote Navigation", "supported": True, "requires_permission": "media:control", "parameters_schema": {"required": ["direction"], "properties": {"direction": {"type": "string", "enum": ["DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT", "DPAD_CENTER", "BACK", "HOME"]}}}},
        {"capability_key": "media.control", "category": "media", "display_name": "TV Media Playback", "supported": True, "requires_permission": "media:control", "parameters_schema": {"required": ["action"], "properties": {"action": {"type": "string", "enum": ["play_pause", "next", "previous"]}}}},
        {"capability_key": "volume.set", "category": "audio", "display_name": "TV Volume & Mute", "supported": True, "requires_permission": "volume:control", "parameters_schema": {"required": ["action"], "properties": {"action": {"type": "string", "enum": ["up", "down", "mute"]}}}},
        {"capability_key": "apps.launch", "category": "applications", "display_name": "Launch TV Streaming App Link", "supported": True, "requires_permission": "apps:launch", "parameters_schema": {"required": ["app_link"], "properties": {"app_link": {"type": "string"}}}},
        {"capability_key": "power.set", "category": "power", "display_name": "Standby / Wake-on-LAN Power Toggle", "supported": True, "requires_permission": "power:control"},
    ],
}


class PairingService:
    """Manages cryptographic PIN/QR pairing, credential issuance, rotation, isolation, and emergency lockdown."""

    def __init__(self, vault: CredentialVault, event_bus: EventBus, tls_fingerprint: str, ttl_seconds: int = 300):
        self.vault = vault
        self.event_bus = event_bus
        self.tls_fingerprint = tls_fingerprint
        self.ttl_seconds = ttl_seconds

    def initiate_pairing(
        self,
        *,
        db: Session,
        user_id: str,
        adapter_type: str,
        hub_host: str,
        hub_port: int,
        device_id: str | None = None,
        requested_permissions: list[str] | None = None,
    ) -> PairingInitiationResult:
        pin = generate_pairing_pin()
        qr_token = generate_secure_token(16)
        nonce = generate_secure_token(16)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=self.ttl_seconds)
        perms = requested_permissions if requested_permissions is not None else ["telemetry:read"]

        session = PairingSession(
            device_id=device_id,
            adapter_type=adapter_type,
            pairing_code_hash=hash_token(pin),
            qr_token_hash=hash_token(qr_token),
            challenge_nonce=nonce,
            requested_permissions=perms,
            status="pending",
            initiated_by_user_id=user_id,
            created_at=now,
            expires_at=expires_at,
        )
        db.add(session)
        db.flush()

        # Short, universally scannable HTTP URL for iPhone/Android Camera & browsers on the same Wi-Fi
        qr_uri = f"http://{hub_host}:{hub_port}/p/{qr_token}"

        deep_query = urlencode(
            {
                "session_id": session.id,
                "hub": f"http://{hub_host}:{hub_port}",
                "token": qr_token,
                "nonce": nonce,
                "fp": self.tls_fingerprint[:32],
                "adapter": adapter_type,
            }
        )
        deep_link_uri = f"homeos://pair?{deep_query}"
        qr_svg = _render_qr_svg(qr_uri)

        append_audit_log(
            db,
            severity="info",
            category="pairing",
            event_type="pairing.initiated",
            actor_type="user",
            actor_id=user_id,
            device_id=device_id,
            outcome="pending",
            details={"pairing_session_id": session.id, "adapter_type": adapter_type, "lan_host": hub_host},
        )
        db.commit()

        return PairingInitiationResult(
            session_id=session.id,
            pin=pin,
            qr_token=qr_token,
            qr_uri=qr_uri,
            deep_link_uri=deep_link_uri,
            qr_svg=qr_svg,
            challenge_nonce=nonce,
            expires_at=expires_at,
        )

    async def complete_pairing(
        self,
        *,
        db: Session,
        pairing_session_id: str,
        pin_or_token: str,
        hardware_fingerprint: str,
        name: str,
        category: str,
        manufacturer: str | None = None,
        model: str | None = None,
        os_name: str | None = None,
        os_version: str | None = None,
        ip_address: str | None = None,
        port: int | None = None,
        capabilities: list[CapabilityDescriptor] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EnrollmentCompletionResult:
        session = db.get(PairingSession, pairing_session_id)
        if session is None:
            raise PairingError("Pairing session does not exist", status_code=404)
        if session.status != "pending":
            raise PairingError("Pairing session has already been used or cancelled", status_code=403)

        now = datetime.now(timezone.utc)
        if _ensure_utc(session.expires_at) <= now:
            session.status = "expired"
            db.commit()
            raise PairingError("Pairing session has expired", status_code=403)

        proof_hash = hash_token(pin_or_token.strip())
        if proof_hash not in (session.pairing_code_hash, session.qr_token_hash):
            append_audit_log(
                db,
                severity="warning",
                category="security",
                event_type="pairing.invalid_secret",
                actor_type="device",
                actor_id=hardware_fingerprint,
                outcome="rejected",
                details={"pairing_session_id": pairing_session_id},
            )
            db.commit()
            raise PairingError("Invalid pairing PIN or QR token", status_code=401)

        # Locate existing discovered device or create new inventory entry
        device = None
        if session.device_id:
            device = db.get(Device, session.device_id)
        if device is None:
            device = db.execute(
                select(Device).where(Device.hardware_fingerprint == hardware_fingerprint)
            ).scalar_one_or_none()

        if device is None:
            device = Device(
                hardware_fingerprint=hardware_fingerprint,
                name=name,
                category=category,
                manufacturer=manufacturer,
                model=model,
                os_name=os_name,
                os_version=os_version,
                ip_address=ip_address,
                port=port,
                lifecycle_state="authenticated",
                discovery_source="paired",
                adapter_type=session.adapter_type,
                connection_status="online",
                health_score=100,
                integration_health="healthy",
                metadata_json=metadata or {},
                first_seen_at=now,
                last_seen_at=now,
                enrolled_at=now,
            )
            db.add(device)
            db.flush()
        else:
            device.name = name or device.name
            device.category = category or device.category
            device.manufacturer = manufacturer or device.manufacturer
            device.model = model or device.model
            device.os_name = os_name or device.os_name
            device.os_version = os_version or device.os_version
            device.ip_address = ip_address or device.ip_address
            device.port = port or device.port
            device.adapter_type = session.adapter_type
            device.lifecycle_state = "authenticated"
            device.connection_status = "online"
            device.health_score = 100
            device.integration_health = "healthy"
            device.last_seen_at = now
            device.enrolled_at = now
            if metadata:
                merged = dict(device.metadata_json or {})
                merged.update(metadata)
                device.metadata_json = merged

        # Issue fresh non-plaintext credentials
        device_token = generate_secure_token(32)
        hmac_secret = generate_secure_token(32)
        encrypted_hmac = self.vault.encrypt(hmac_secret, aad=f"device:{device.id}")

        existing_cred = db.execute(select(DeviceCredential).where(DeviceCredential.device_id == device.id)).scalar_one_or_none()
        if existing_cred is None:
            cred = DeviceCredential(
                device_id=device.id,
                token_hash=hash_token(device_token),
                encrypted_hmac_secret=encrypted_hmac,
                key_version=1,
            )
            db.add(cred)
            key_ver = 1
        else:
            existing_cred.token_hash = hash_token(device_token)
            existing_cred.encrypted_hmac_secret = encrypted_hmac
            existing_cred.key_version += 1
            existing_cred.revoked_at = None
            existing_cred.rotated_at = now
            key_ver = existing_cred.key_version

        # Remove any unpaired placeholder capability
        for old_cap in list(device.capabilities):
            if old_cap.capability_key == "control.unpaired":
                db.delete(old_cap)
        db.flush()

        # Sync reported or platform-default capabilities
        cap_defs = (
            [c.model_dump() for c in capabilities]
            if capabilities is not None
            else DEFAULT_PLATFORM_CAPABILITIES.get(session.adapter_type, [])
        )
        self.sync_device_capabilities(db, device.id, cap_defs)

        # Grant explicitly requested permissions
        scopes = set(session.requested_permissions or ["telemetry:read"])
        scopes.add("telemetry:read")
        for scope in scopes:
            perm = db.execute(
                select(DevicePermission).where(
                    DevicePermission.device_id == device.id,
                    DevicePermission.permission_scope == scope,
                )
            ).scalar_one_or_none()
            if perm is None:
                db.add(
                    DevicePermission(
                        device_id=device.id,
                        permission_scope=scope,
                        status="granted",
                        granted_by_user_id=session.initiated_by_user_id,
                    )
                )
            else:
                perm.status = "granted"
                perm.revoked_at = None

        session.status = "completed"
        session.device_id = device.id
        session.completed_at = now

        append_audit_log(
            db,
            severity="info",
            category="pairing",
            event_type="device.enrolled_and_authenticated",
            actor_type="user",
            actor_id=session.initiated_by_user_id,
            device_id=device.id,
            outcome="success",
            details={
                "adapter_type": device.adapter_type,
                "granted_permissions": sorted(scopes),
                "key_version": key_ver,
            },
        )
        db.commit()
        db.refresh(device)

        await self.event_bus.publish(
            "device.enrolled",
            {"device_id": device.id, "name": device.name, "adapter_type": device.adapter_type},
        )

        return EnrollmentCompletionResult(
            device=device,
            device_token=device_token,
            hmac_secret=hmac_secret,
            key_version=key_ver,
        )

    def sync_device_capabilities(self, db: Session, device_id: str, cap_dicts: list[dict[str, Any]]) -> None:
        now = datetime.now(timezone.utc)
        for item in cap_dicts:
            key = item["capability_key"]
            existing = db.execute(
                select(DeviceCapability).where(
                    DeviceCapability.device_id == device_id,
                    DeviceCapability.capability_key == key,
                )
            ).scalar_one_or_none()
            schema = item.get("parameters_schema") or item.get("parameters_schema_json") or {}
            if existing is None:
                db.add(
                    DeviceCapability(
                        device_id=device_id,
                        capability_key=key,
                        category=item.get("category", "general"),
                        display_name=item.get("display_name", key),
                        description=item.get("description"),
                        supported=bool(item.get("supported", True)),
                        unavailable_reason=item.get("unavailable_reason"),
                        requires_permission=item.get("requires_permission", "telemetry:read"),
                        requires_confirmation=bool(item.get("requires_confirmation", False)),
                        parameters_schema_json=schema,
                        updated_at=now,
                    )
                )
            else:
                existing.category = item.get("category", existing.category)
                existing.display_name = item.get("display_name", existing.display_name)
                existing.description = item.get("description", existing.description)
                existing.supported = bool(item.get("supported", True))
                existing.unavailable_reason = item.get("unavailable_reason")
                existing.requires_permission = item.get("requires_permission", existing.requires_permission)
                existing.requires_confirmation = bool(item.get("requires_confirmation", False))
                existing.parameters_schema_json = schema
                existing.updated_at = now

    def rotate_credentials(self, *, db: Session, device_id: str, actor_user_id: str) -> EnrollmentCompletionResult:
        device = db.get(Device, device_id)
        if device is None:
            raise PairingError("Device not found", status_code=404)
        cred = db.execute(select(DeviceCredential).where(DeviceCredential.device_id == device_id)).scalar_one_or_none()
        if cred is None:
            raise PairingError("Device has no active credentials to rotate", status_code=400)

        new_token = generate_secure_token(32)
        new_hmac = generate_secure_token(32)
        now = datetime.now(timezone.utc)

        cred.token_hash = hash_token(new_token)
        cred.encrypted_hmac_secret = self.vault.encrypt(new_hmac, aad=f"device:{device_id}")
        cred.key_version += 1
        cred.rotated_at = now
        cred.revoked_at = None

        append_audit_log(
            db,
            severity="info",
            category="security",
            event_type="credential.rotated",
            actor_type="user",
            actor_id=actor_user_id,
            device_id=device_id,
            outcome="success",
            details={"key_version": cred.key_version},
        )
        db.commit()
        db.refresh(device)
        return EnrollmentCompletionResult(
            device=device,
            device_token=new_token,
            hmac_secret=new_hmac,
            key_version=cred.key_version,
        )

    async def isolate_device(self, *, db: Session, device_id: str, actor_user_id: str, reason: str, adapter_registry=None) -> Device:
        device = db.get(Device, device_id)
        if device is None:
            raise PairingError("Device not found", status_code=404)
        device.lifecycle_state = "isolated"
        device.connection_status = "isolated"
        device.integration_health = "quarantined"
        if adapter_registry:
            adapter = adapter_registry.get(device.adapter_type)
            if adapter:
                await adapter.disconnect(device.id, reason="isolated_by_operator")

        append_audit_log(
            db,
            severity="critical",
            category="security",
            event_type="device.isolated",
            actor_type="user",
            actor_id=actor_user_id,
            device_id=device_id,
            outcome="isolated",
            details={"reason": reason},
        )
        db.commit()
        await self.event_bus.publish("device.isolated", {"device_id": device.id, "reason": reason})
        return device

    async def emergency_lockdown(self, *, db: Session, actor_user_id: str, adapter_registry=None) -> dict[str, int]:
        devices = db.execute(select(Device).where(Device.lifecycle_state.in_(["authenticated", "enrolled"]))).scalars().all()
        isolated_count = 0
        for dev in devices:
            dev.lifecycle_state = "isolated"
            dev.connection_status = "isolated"
            dev.integration_health = "emergency_lockdown"
            isolated_count += 1
            if adapter_registry:
                adapter = adapter_registry.get(dev.adapter_type)
                if adapter:
                    await adapter.disconnect(dev.id, reason="emergency_lockdown")

        rules = db.execute(select(AutomationRule).where(AutomationRule.enabled.is_(True))).scalars().all()
        paused_rules = 0
        for rule in rules:
            rule.enabled = False
            paused_rules += 1

        append_audit_log(
            db,
            severity="critical",
            category="security",
            event_type="security.emergency_lockdown",
            actor_type="user",
            actor_id=actor_user_id,
            outcome="executed",
            details={"devices_isolated": isolated_count, "automations_paused": paused_rules},
        )
        db.commit()
        await self.event_bus.publish(
            "security.emergency_lockdown",
            {"devices_isolated": isolated_count, "automations_paused": paused_rules},
        )
        return {"devices_isolated": isolated_count, "automations_paused": paused_rules}

    def ensure_local_host_enrolled(self, db: Session) -> Device:
        import platform
        import socket
        import sys
        from home_os.adapters.local_host import LOCAL_HOST_CAPABILITIES, get_local_host_telemetry
        from home_os.services.discovery import get_primary_lan_ipv4

        fp = "local-host-" + socket.gethostname().lower()[:40]
        existing = db.execute(select(Device).where(Device.hardware_fingerprint == fp)).scalar_one_or_none()
        is_mac = sys.platform == "darwin"
        telem = get_local_host_telemetry()
        now = datetime.now(timezone.utc)

        if existing is None:
            existing = Device(
                hardware_fingerprint=fp,
                name=f"{socket.gethostname()} (This {'Mac' if is_mac else 'Computer'})",
                category="laptop" if is_mac else "desktop",
                manufacturer="Apple" if is_mac else platform.system(),
                model=platform.machine(),
                os_name="macOS" if is_mac else platform.system(),
                os_version=platform.mac_ver()[0] if is_mac else platform.release(),
                ip_address=get_primary_lan_ipv4(),
                hostname=socket.gethostname(),
                lifecycle_state="authenticated",
                discovery_source="local_host",
                adapter_type="local_host",
                connection_status="online",
                health_score=100,
                battery_level=telem.get("battery_level"),
                is_charging=telem.get("is_charging"),
                integration_health="healthy",
                health_details_json=telem,
                enrolled_at=now,
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(existing)
            db.flush()
        else:
            existing.ip_address = get_primary_lan_ipv4()
            existing.battery_level = telem.get("battery_level")
            existing.is_charging = telem.get("is_charging")
            existing.health_details_json = telem
            if existing.lifecycle_state != "isolated":
                existing.lifecycle_state = "authenticated"
                existing.connection_status = "online"
                existing.integration_health = "healthy"
            existing.last_seen_at = now

        cred = db.execute(select(DeviceCredential).where(DeviceCredential.device_id == existing.id)).scalar_one_or_none()
        if cred is None:
            tok = generate_secure_token(32)
            hm = generate_secure_token(32)
            db.add(
                DeviceCredential(
                    device_id=existing.id,
                    key_version=1,
                    token_hash=hash_token(tok),
                    encrypted_hmac_secret=self.vault.encrypt(hm, aad=f"device:{existing.id}"),
                )
            )

        self.sync_device_capabilities(
            db,
            existing.id,
            LOCAL_HOST_CAPABILITIES,
        )

        existing_scopes = {
            p.permission_scope
            for p in db.execute(select(DevicePermission).where(DevicePermission.device_id == existing.id)).scalars().all()
        }
        for scope in (
            "telemetry:read",
            "volume:control",
            "media:control",
            "notifications:read",
            "notifications:reply",
            "apps:launch",
            "apps:manage",
            "files:browse",
            "files:write",
            "power:control",
        ):
            if scope not in existing_scopes:
                db.add(
                    DevicePermission(
                        device_id=existing.id,
                        permission_scope=scope,
                        status="granted",
                    )
                )

        db.commit()
        db.refresh(existing)
        return existing

    def enroll_network_device(self, *, db: Session, device_id: str, actor_user_id: str) -> Device:
        from home_os.adapters.generic_network import ENROLLED_NETWORK_CAPABILITIES

        device = db.get(Device, device_id)
        if device is None:
            raise PairingError("Device not found", status_code=404)

        now = datetime.now(timezone.utc)
        device.adapter_type = "generic_network"
        device.lifecycle_state = "authenticated"
        device.connection_status = "online"
        device.integration_health = "healthy"
        device.health_score = 95
        device.enrolled_at = device.enrolled_at or now
        device.last_seen_at = now

        cred = db.execute(select(DeviceCredential).where(DeviceCredential.device_id == device.id)).scalar_one_or_none()
        if cred is None or cred.revoked_at is not None:
            tok = generate_secure_token(32)
            hm = generate_secure_token(32)
            if cred is None:
                db.add(
                    DeviceCredential(
                        device_id=device.id,
                        key_version=1,
                        token_hash=hash_token(tok),
                        encrypted_hmac_secret=self.vault.encrypt(hm, aad=f"device:{device.id}"),
                    )
                )
            else:
                cred.key_version += 1
                cred.token_hash = hash_token(tok)
                cred.encrypted_hmac_secret = self.vault.encrypt(hm, aad=f"device:{device.id}")
                cred.revoked_at = None

        self.sync_device_capabilities(
            db,
            device.id,
            ENROLLED_NETWORK_CAPABILITIES,
        )

        existing_scopes = {
            p.permission_scope: p
            for p in db.execute(select(DevicePermission).where(DevicePermission.device_id == device.id)).scalars().all()
        }
        for scope in ("telemetry:read", "power:control"):
            if scope in existing_scopes:
                existing_scopes[scope].status = "granted"
                existing_scopes[scope].revoked_at = None
            else:
                db.add(
                    DevicePermission(
                        device_id=device.id,
                        permission_scope=scope,
                        status="granted",
                        granted_by_user_id=actor_user_id,
                    )
                )

        append_audit_log(
            db,
            severity="info",
            category="pairing",
            event_type="device.network_tools_enrolled",
            actor_type="user",
            actor_id=actor_user_id,
            device_id=device.id,
            outcome="success",
            details={"ip_address": device.ip_address, "mac_address": device.mac_address},
        )
        db.commit()
        db.refresh(device)
        return device

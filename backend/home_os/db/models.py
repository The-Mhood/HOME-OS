import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="owner")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sessions: Mapped[list["UserSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    floor: Mapped[str | None] = mapped_column(String(40), nullable=True)
    icon: Mapped[str] = mapped_column(String(40), nullable=False, default="home")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    devices: Mapped[list["Device"]] = relationship(back_populates="room")


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    hardware_fingerprint: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    os_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    mac_address: Mapped[str | None] = mapped_column(String(32), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(128), nullable=True)
    room_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True, index=True)
    group_tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    # 7-stage security lifecycle: discovered -> identified -> enrolled -> authenticated | isolated | revoked
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False, default="discovered", index=True)
    discovery_source: Mapped[str] = mapped_column(String(48), nullable=False, default="manual")
    adapter_type: Mapped[str] = mapped_column(String(48), nullable=False, default="generic_network", index=True)
    connection_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unpaired", index=True)

    battery_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_charging: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    health_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    integration_health: Mapped[str] = mapped_column(String(32), nullable=False, default="unverified")
    health_details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    room: Mapped["Room | None"] = relationship(back_populates="devices")
    credential: Mapped["DeviceCredential | None"] = relationship(back_populates="device", uselist=False, cascade="all, delete-orphan")
    capabilities: Mapped[list["DeviceCapability"]] = relationship(back_populates="device", cascade="all, delete-orphan")
    permissions: Mapped[list["DevicePermission"]] = relationship(back_populates="device", cascade="all, delete-orphan")


class DeviceCredential(Base):
    __tablename__ = "device_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(String(36), ForeignKey("devices.id", ondelete="CASCADE"), unique=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    encrypted_hmac_secret: Mapped[str] = mapped_column(Text, nullable=False)
    client_cert_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    device: Mapped["Device"] = relationship(back_populates="credential")


class PairingSession(Base):
    __tablename__ = "pairing_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, index=True)
    adapter_type: Mapped[str] = mapped_column(String(48), nullable=False)
    pairing_code_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    qr_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    challenge_nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_permissions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    initiated_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeviceCapability(Base):
    __tablename__ = "device_capabilities"
    __table_args__ = (UniqueConstraint("device_id", "capability_key", name="uq_device_capability_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(String(36), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    capability_key: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)
    supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    unavailable_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    requires_permission: Mapped[str] = mapped_column(String(64), nullable=False)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parameters_schema_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    device: Mapped["Device"] = relationship(back_populates="capabilities")


class DevicePermission(Base):
    __tablename__ = "device_permissions"
    __table_args__ = (UniqueConstraint("device_id", "permission_scope", name="uq_device_permission_scope"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(String(36), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    permission_scope: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="granted")
    granted_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(256), nullable=True)

    device: Mapped["Device"] = relationship(back_populates="permissions")


class CommandExecution(Base):
    __tablename__ = "command_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(String(36), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    initiated_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    automation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    capability_key: Mapped[str] = mapped_column(String(80), nullable=False)
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # pending_confirmation | queued | dispatched | acknowledged | verified | failed | timed_out | rejected
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    verification_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)

    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=8000)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CommunicationItem(Base):
    __tablename__ = "communication_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(String(36), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    # Explicitly distinguishes notification mirroring vs direct OS permission
    access_level: Mapped[str] = mapped_column(String(32), nullable=False, default="notification_only")
    app_package: Mapped[str | None] = mapped_column(String(120), nullable=True)
    app_name: Mapped[str] = mapped_column(String(80), nullable=False)
    sender: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    body_preview: Mapped[str] = mapped_column(Text, nullable=False)
    can_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reply_action_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dismissed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class MediaItem(Base):
    __tablename__ = "media_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(String(36), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    media_type: Mapped[str] = mapped_column(String(24), nullable=False, index=True)  # photo | video | audio
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    remote_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(96), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ActiveMediaSession(Base):
    __tablename__ = "active_media_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(String(36), ForeignKey("devices.id", ondelete="CASCADE"), unique=True, nullable=False)
    app_name: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    artist: Mapped[str | None] = mapped_column(String(160), nullable=True)
    album: Mapped[str | None] = mapped_column(String(160), nullable=True)
    playback_state: Mapped[str] = mapped_column(String(24), nullable=False, default="paused")  # playing | paused | stopped
    position_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    volume_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_muted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_seek: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_skip: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class FileTransfer(Base):
    __tablename__ = "file_transfers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    direction: Mapped[str] = mapped_column(String(32), nullable=False)  # upload | download | device_to_device
    source_device_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True)
    target_device_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True)
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    target_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    staged_local_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    file_name: Mapped[str] = mapped_column(String(256), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(96), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transferred_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    authorized_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AutomationRule(Base):
    __tablename__ = "automation_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(140), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # event | schedule | device_state
    trigger_config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    conditions_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    actions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    retry_policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class AutomationRun(Base):
    __tablename__ = "automation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    rule_id: Mapped[str] = mapped_column(String(36), ForeignKey("automation_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    trigger_reason: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running", index=True)
    step_results_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IntegrationConnection(Base):
    __tablename__ = "integration_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    integration_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)  # home_assistant | matter_server
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    endpoint_url: Mapped[str] = mapped_column(String(256), nullable=False)
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="disconnected")
    health_message: Mapped[str | None] = mapped_column(String(256), nullable=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_category_created", "category", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # info | warning | critical
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)  # user | device | automation | system
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

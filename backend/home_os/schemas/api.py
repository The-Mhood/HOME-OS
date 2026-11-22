from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


class SetupOwnerRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    username: str
    password: str


class RoomCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    floor: str | None = None
    icon: str = "home"


class ManualDeviceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = "network"
    adapter_type: str = "generic_network"
    ip_address: str | None = None
    port: int | None = None
    mac_address: str | None = None
    manufacturer: str | None = None
    model: str | None = None


class DeviceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    room_id: str | None = None
    clear_room: bool = False
    group_tags: list[str] | None = None


class PairingInitiateRequest(BaseModel):
    adapter_type: str
    device_id: str | None = None
    hub_host: str | None = None
    hub_port: int | None = None
    requested_permissions: list[str] = Field(
        default_factory=lambda: [
            "telemetry:read",
            "files:browse",
            "files:write",
            "media:control",
            "volume:control",
            "apps:launch",
            "apps:manage",
        ]
    )


class PairingCompleteRequest(BaseModel):
    pairing_session_id: str
    pin_or_token: str
    hardware_fingerprint: str
    name: str
    category: str = "desktop"
    manufacturer: str | None = None
    model: str | None = None
    os_name: str | None = None
    os_version: str | None = None
    ip_address: str | None = None
    port: int | None = None
    capabilities: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PermissionUpdateRequest(BaseModel):
    permission_scope: str = Field(min_length=3, max_length=64)
    status: str = Field(pattern="^(granted|revoked)$")
    reason: str | None = None


class IsolateDeviceRequest(BaseModel):
    reason: str = "Manual operator quarantine"


class SubmitCommandRequest(BaseModel):
    capability_key: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False
    timeout_ms: int = Field(default=8000, ge=500, le=60000)
    max_retries: int = Field(default=1, ge=0, le=5)


class AgentTelemetryPayload(BaseModel):
    battery_level: int | None = Field(default=None, ge=0, le=100)
    is_charging: bool | None = None
    health_score: int | None = Field(default=None, ge=0, le=100)
    attributes: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[dict[str, Any]] | None = None
    media_session: dict[str, Any] | None = None


class AgentCommunicationPayload(BaseModel):
    source_type: str
    app_name: str
    sender: str
    body_preview: str
    title: str | None = None
    app_package: str | None = None
    can_reply: bool = False
    reply_action_id: str | None = None
    external_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NotificationReplyRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class DeviceToDeviceTransferRequest(BaseModel):
    source_device_id: str
    source_path: str
    target_device_id: str
    target_dir: str = ""


class AutomationRuleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=140)
    description: str | None = None
    enabled: bool = True
    trigger_type: str = Field(pattern="^(event|schedule|device_state)$")
    trigger_config: dict[str, Any]
    conditions: dict[str, Any] = Field(default_factory=lambda: {"operator": "all", "clauses": []})
    actions: list[dict[str, Any]] = Field(min_length=1)
    retry_policy: dict[str, Any] = Field(default_factory=lambda: {"max_retries": 1})
    requires_confirmation: bool = False


class AutomationRulePatchRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    requires_confirmation: bool | None = None


class HomeAssistantConnectRequest(BaseModel):
    name: str = "Home Assistant Core"
    endpoint_url: str
    access_token: str = Field(min_length=8)


class MatterConnectRequest(BaseModel):
    name: str = "Matter Fabric Controller"
    ws_url: str

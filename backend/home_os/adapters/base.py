from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from pydantic import BaseModel, Field


class DiscoveredEndpoint(BaseModel):
    hardware_fingerprint: str
    discovery_source: str  # mdns | ssdp | arp | manual | home_assistant | matter
    ip_address: str | None = None
    port: int | None = None
    mac_address: str | None = None
    hostname: str | None = None
    service_type: str | None = None
    raw_properties: dict[str, Any] = Field(default_factory=dict)


class DeviceIdentity(BaseModel):
    hardware_fingerprint: str
    name: str
    category: str  # smartphone | desktop | laptop | tv | speaker | appliance | smarthome | network
    manufacturer: str | None = None
    model: str | None = None
    os_name: str | None = None
    os_version: str | None = None
    adapter_type: str
    ip_address: str | None = None
    port: int | None = None
    mac_address: str | None = None
    hostname: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PairingRequest(BaseModel):
    method: str = "pin"  # pin | qr | token | certificate
    pin_or_token: str | None = None
    requested_permissions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PairingChallenge(BaseModel):
    pairing_session_id: str
    challenge_nonce: str
    expires_at: datetime
    qr_uri: str | None = None
    display_pin: str | None = None
    instructions: str = ""


class AuthProof(BaseModel):
    pairing_session_id: str | None = None
    pin_or_token: str
    challenge_signature: str | None = None
    client_public_key: str | None = None
    device_metadata: dict[str, Any] = Field(default_factory=dict)


class AuthResult(BaseModel):
    authenticated: bool
    device_token: str | None = None
    hmac_secret: str | None = None
    key_version: int = 1
    error_reason: str | None = None


class CapabilityDescriptor(BaseModel):
    capability_key: str
    category: str
    display_name: str
    description: str | None = None
    supported: bool = True
    unavailable_reason: str | None = None
    requires_permission: str
    requires_confirmation: bool = False
    parameters_schema: dict[str, Any] = Field(default_factory=dict)


class DeviceStatusSnapshot(BaseModel):
    device_id: str
    online: bool
    battery_level: int | None = None
    is_charging: bool | None = None
    health_score: int | None = 100
    integration_health: str = "healthy"
    attributes: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AdapterCommand(BaseModel):
    command_id: str
    capability_key: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = 8000


class CommandExecutionReceipt(BaseModel):
    command_id: str
    acknowledged: bool
    state_verified: bool = False
    verification_method: str = "dispatch_ack_only"
    result: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None


EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


class BaseDeviceAdapter(ABC):
    """Versioned Common Device Adapter Contract (v1.0) implemented by every device integration."""

    adapter_type: str = "base"
    contract_version: str = "1.0"

    @abstractmethod
    async def discover(self, timeout: float = 3.0) -> list[DiscoveredEndpoint]:
        ...

    @abstractmethod
    async def identify(self, endpoint: DiscoveredEndpoint) -> DeviceIdentity:
        ...

    @abstractmethod
    async def pair(self, device_id: str, request: PairingRequest) -> PairingChallenge:
        ...

    @abstractmethod
    async def authenticate(self, device_id: str, proof: AuthProof) -> AuthResult:
        ...

    @abstractmethod
    async def get_status(self, device_id: str) -> DeviceStatusSnapshot:
        ...

    @abstractmethod
    async def get_capabilities(self, device_id: str) -> list[CapabilityDescriptor]:
        ...

    @abstractmethod
    async def execute_command(self, device_id: str, command: AdapterCommand) -> CommandExecutionReceipt:
        ...

    @abstractmethod
    async def subscribe_events(self, device_id: str, callback: EventCallback) -> Any:
        ...

    @abstractmethod
    async def disconnect(self, device_id: str, reason: str = "operator_requested") -> None:
        ...

    @abstractmethod
    async def revoke_access(self, device_id: str, reason: str = "permission_revoked") -> None:
        ...


class AdapterRegistry:
    def __init__(self):
        self._adapters: dict[str, BaseDeviceAdapter] = {}

    def register(self, adapter: BaseDeviceAdapter) -> None:
        self._adapters[adapter.adapter_type] = adapter

    def get(self, adapter_type: str) -> BaseDeviceAdapter | None:
        return self._adapters.get(adapter_type)

    def all(self) -> list[BaseDeviceAdapter]:
        return list(self._adapters.values())

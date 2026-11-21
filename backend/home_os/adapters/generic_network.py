import asyncio
from datetime import datetime, timezone

from home_os.adapters.base import (
    AdapterCommand,
    AuthProof,
    AuthResult,
    BaseDeviceAdapter,
    CapabilityDescriptor,
    CommandExecutionReceipt,
    DeviceIdentity,
    DeviceStatusSnapshot,
    DiscoveredEndpoint,
    EventCallback,
    PairingChallenge,
    PairingRequest,
)
from home_os.services.discovery import lookup_mac_vendor


class GenericNetworkDeviceAdapter(BaseDeviceAdapter):
    """Honest adapter for unknown or unpaired network devices observed via ARP/mDNS/SSDP."""

    adapter_type = "generic_network"

    async def discover(self, timeout: float = 3.0) -> list[DiscoveredEndpoint]:
        return []

    async def identify(self, endpoint: DiscoveredEndpoint) -> DeviceIdentity:
        vendor = lookup_mac_vendor(endpoint.mac_address)
        return DeviceIdentity(
            hardware_fingerprint=endpoint.hardware_fingerprint,
            name=endpoint.hostname or endpoint.ip_address or "Unidentified Network Host",
            category="network",
            manufacturer=vendor,
            adapter_type=self.adapter_type,
            ip_address=endpoint.ip_address,
            port=endpoint.port,
            mac_address=endpoint.mac_address,
            hostname=endpoint.hostname,
        )

    async def pair(self, device_id: str, request: PairingRequest) -> PairingChallenge:
        raise PermissionError("Generic unauthenticated LAN host does not advertise a pairing protocol; install a HOME OS companion agent or connect via a supported integration.")

    async def authenticate(self, device_id: str, proof: AuthProof) -> AuthResult:
        return AuthResult(authenticated=False, error_reason="No authentication interface available on generic LAN host")

    async def get_status(self, device_id: str) -> DeviceStatusSnapshot:
        return DeviceStatusSnapshot(
            device_id=device_id,
            online=False,
            health_score=None,
            integration_health="unverified_until_paired",
            attributes={"note": "Passive LAN discovery only; no authenticated control channel."},
            observed_at=datetime.now(timezone.utc),
        )

    async def get_capabilities(self, device_id: str) -> list[CapabilityDescriptor]:
        return [
            CapabilityDescriptor(
                capability_key="control.unpaired",
                category="system",
                display_name="Remote Control Unavailable",
                description="No companion agent or supported manufacturer protocol enrolled.",
                supported=False,
                unavailable_reason="Unknown or unpaired LAN device; install a HOME OS agent or enroll a supported integration.",
                requires_permission="telemetry:read",
            )
        ]

    async def execute_command(self, device_id: str, command: AdapterCommand) -> CommandExecutionReceipt:
        return CommandExecutionReceipt(
            command_id=command.command_id,
            acknowledged=False,
            state_verified=False,
            error_code="UNSUPPORTED_GENERIC_DEVICE",
            error_message="Cannot execute commands on an unauthenticated generic network device",
        )

    async def subscribe_events(self, device_id: str, callback: EventCallback):
        return None

    async def disconnect(self, device_id: str, reason: str = "operator_requested") -> None:
        return None

    async def revoke_access(self, device_id: str, reason: str = "permission_revoked") -> None:
        return None

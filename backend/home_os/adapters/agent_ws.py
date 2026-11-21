import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

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
from home_os.services.pairing import DEFAULT_PLATFORM_CAPABILITIES


class ConnectedAgentSession:
    def __init__(self, device_id: str, adapter_type: str, websocket: WebSocket):
        self.device_id = device_id
        self.adapter_type = adapter_type
        self.websocket = websocket
        self.connected_at = datetime.now(timezone.utc)
        self.last_telemetry: dict[str, Any] = {}
        self.capabilities: list[CapabilityDescriptor] = []


class CompanionAgentHub:
    """Manages authenticated bidirectional WebSocket channels for Linux, Windows, macOS, Android, and iOS agents."""

    def __init__(self):
        self._sessions: dict[str, ConnectedAgentSession] = {}
        self._pending_commands: dict[str, asyncio.Future[CommandExecutionReceipt]] = {}
        self._event_callbacks: dict[str, list[EventCallback]] = {}

    def register_session(self, device_id: str, adapter_type: str, websocket: WebSocket) -> ConnectedAgentSession:
        session = ConnectedAgentSession(device_id=device_id, adapter_type=adapter_type, websocket=websocket)
        self._sessions[device_id] = session
        return session

    def unregister_session(self, device_id: str) -> None:
        self._sessions.pop(device_id, None)

    def get_session(self, device_id: str) -> ConnectedAgentSession | None:
        return self._sessions.get(device_id)

    def is_connected(self, device_id: str) -> bool:
        return device_id in self._sessions

    def resolve_command_receipt(self, payload: dict[str, Any]) -> None:
        command_id = str(payload.get("command_id", ""))
        fut = self._pending_commands.pop(command_id, None)
        if fut and not fut.done():
            receipt = CommandExecutionReceipt(
                command_id=command_id,
                acknowledged=bool(payload.get("acknowledged", True)),
                state_verified=bool(payload.get("state_verified", False)),
                verification_method=str(payload.get("verification_method", "agent_state_readback")),
                result=payload.get("result") or {},
                error_code=payload.get("error_code"),
                error_message=payload.get("error_message"),
            )
            fut.set_result(receipt)

    async def dispatch_command(self, device_id: str, command: AdapterCommand) -> CommandExecutionReceipt:
        session = self._sessions.get(device_id)
        if session is None:
            return CommandExecutionReceipt(
                command_id=command.command_id,
                acknowledged=False,
                state_verified=False,
                error_code="DEVICE_OFFLINE",
                error_message="Companion agent does not currently have an active WebSocket session",
            )

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[CommandExecutionReceipt] = loop.create_future()
        self._pending_commands[command.command_id] = fut
        try:
            await session.websocket.send_json(
                {
                    "type": "command.execute",
                    "command_id": command.command_id,
                    "capability_key": command.capability_key,
                    "parameters": command.parameters,
                    "timeout_ms": command.timeout_ms,
                }
            )
            return await asyncio.wait_for(fut, timeout=max(0.5, command.timeout_ms / 1000.0))
        except (TimeoutError, asyncio.TimeoutError):
            self._pending_commands.pop(command.command_id, None)
            raise TimeoutError(f"Agent {device_id} did not return receipt within {command.timeout_ms}ms")
        except Exception as exc:
            self._pending_commands.pop(command.command_id, None)
            return CommandExecutionReceipt(
                command_id=command.command_id,
                acknowledged=False,
                state_verified=False,
                error_code="TRANSPORT_ERROR",
                error_message=str(exc),
            )

    async def force_disconnect(self, device_id: str, reason: str) -> None:
        session = self._sessions.pop(device_id, None)
        if session is not None:
            try:
                await session.websocket.send_json({"type": "session.terminated", "reason": reason})
                await session.websocket.close(code=4001, reason=reason[:100])
            except Exception:
                pass


class PlatformAgentAdapter(BaseDeviceAdapter):
    """Concrete adapter backed by CompanionAgentHub with platform-accurate capability profiles."""

    def __init__(self, adapter_type: str, category: str, os_name: str, agent_hub: CompanionAgentHub):
        self.adapter_type = adapter_type
        self.category = category
        self.os_name = os_name
        self.agent_hub = agent_hub

    async def discover(self, timeout: float = 3.0) -> list[DiscoveredEndpoint]:
        return []

    async def identify(self, endpoint: DiscoveredEndpoint) -> DeviceIdentity:
        return DeviceIdentity(
            hardware_fingerprint=endpoint.hardware_fingerprint,
            name=endpoint.hostname or f"{self.os_name} Device",
            category=self.category,
            os_name=self.os_name,
            adapter_type=self.adapter_type,
            ip_address=endpoint.ip_address,
            port=endpoint.port,
            hostname=endpoint.hostname,
        )

    async def pair(self, device_id: str, request: PairingRequest) -> PairingChallenge:
        raise NotImplementedError("Managed centrally via PairingService")

    async def authenticate(self, device_id: str, proof: AuthProof) -> AuthResult:
        raise NotImplementedError("Managed centrally via PairingService + ReplayGuard")

    async def get_status(self, device_id: str) -> DeviceStatusSnapshot:
        session = self.agent_hub.get_session(device_id)
        if session is None:
            return DeviceStatusSnapshot(
                device_id=device_id,
                online=False,
                health_score=40,
                integration_health="offline",
                attributes={},
            )
        telem = session.last_telemetry
        return DeviceStatusSnapshot(
            device_id=device_id,
            online=True,
            battery_level=telem.get("battery_level"),
            is_charging=telem.get("is_charging"),
            health_score=telem.get("health_score", 100),
            integration_health="healthy",
            attributes=telem,
        )

    async def get_capabilities(self, device_id: str) -> list[CapabilityDescriptor]:
        session = self.agent_hub.get_session(device_id)
        if session and session.capabilities:
            return session.capabilities
        defaults = DEFAULT_PLATFORM_CAPABILITIES.get(self.adapter_type, [])
        return [CapabilityDescriptor(**item) for item in defaults]

    async def execute_command(self, device_id: str, command: AdapterCommand) -> CommandExecutionReceipt:
        return await self.agent_hub.dispatch_command(device_id, command)

    async def subscribe_events(self, device_id: str, callback: EventCallback):
        self.agent_hub._event_callbacks.setdefault(device_id, []).append(callback)
        return True

    async def disconnect(self, device_id: str, reason: str = "operator_requested") -> None:
        await self.agent_hub.force_disconnect(device_id, reason)

    async def revoke_access(self, device_id: str, reason: str = "permission_revoked") -> None:
        await self.agent_hub.force_disconnect(device_id, reason)

# 07 — Device Adapter Developer Guide

HOME OS decouples core orchestration, permissions, automations, and UI from transport-specific protocols through the **Common Device Adapter Contract (`DeviceAdapter`)** in `backend/home_os/adapters/base.py`.

## 1. Required Adapter Interface
Every adapter inherits from `DeviceAdapter` and implements 10 lifecycle methods:

```python
class DeviceAdapter(ABC):
    adapter_type: str

    async def discover(self, timeout: float = 3.0) -> list[DiscoveredEndpoint]: ...
    async def identify(self, endpoint: DiscoveredEndpoint) -> dict[str, Any]: ...
    async def pair(self, device_id: str, pairing_payload: dict[str, Any]) -> dict[str, Any]: ...
    async def authenticate(self, device_id: str, credentials: dict[str, Any]) -> bool: ...
    async def get_status(self, device_id: str) -> AdapterStatusReport: ...
    async def get_capabilities(self, device_id: str) -> list[CapabilityDescriptor]: ...
    async def execute_command(
        self,
        device_id: str,
        capability_key: str,
        parameters: dict[str, Any],
        credentials: dict[str, Any] | None = None,
    ) -> AdapterCommandOutcome: ...
    async def subscribe_events(self, device_id: str) -> AsyncIterator[dict[str, Any]]: ...
    async def disconnect(self, device_id: str) -> None: ...
    async def revoke_access(self, device_id: str) -> None: ...
```

## 2. Truthful Execution Reporting Rules
When implementing `execute_command`:
- Return `AdapterCommandOutcome(accepted=True, verified=True, verification_method="...", result={...})` **only** when the target device or controller has confirmed state change or returned a verified payload (e.g., SHA-256 digest, HTTP 200 entity state readback, or signed agent command acknowledgment).
- If a protocol is fire-and-forget (such as Wake-on-LAN magic packet broadcast), set `accepted=True, verified=False, verification_method="magic_packet_dispatched_unverified"` so the UI and audit log report `acknowledged` rather than falsely claiming `verified`.
- Never return placeholder data for unsupported capabilities: emit `CapabilityDescriptor(..., supported=False, unavailable_reason="...")`.

## 3. Registering a New Adapter
1. Add your adapter module in `backend/home_os/adapters/<your_adapter>.py`.
2. Register the instance in `HomeOsRuntime._register_adapters()` (`backend/home_os/runtime.py`).
3. Add unit and integration tests in `tests/` covering discovery, pairing, permission enforcement, verified vs failed command execution, and revocation.

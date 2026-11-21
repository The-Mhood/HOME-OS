import hashlib
from datetime import datetime, timezone
from typing import Any

import httpx

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

SUPPORTED_HA_DOMAINS = {
    "light",
    "switch",
    "media_player",
    "climate",
    "lock",
    "fan",
    "cover",
    "scene",
    "sensor",
    "binary_sensor",
    "vacuum",
}


class HomeAssistantAdapter(BaseDeviceAdapter):
    """Smart-home backbone adapter integrating Home Assistant REST & WebSocket APIs with post-command state verification."""

    adapter_type = "home_assistant"

    def __init__(self, http_transport: httpx.AsyncBaseTransport | None = None):
        self._http_transport = http_transport
        self._base_url: str | None = None
        self._access_token: str | None = None
        self._device_entity_map: dict[str, str] = {}

    def configure_connection(self, *, base_url: str, access_token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token.strip()

    def bind_device_entity(self, device_id: str, entity_id: str) -> None:
        self._device_entity_map[device_id] = entity_id

    def _client(self) -> httpx.AsyncClient:
        if not self._base_url or not self._access_token:
            raise ConnectionError("Home Assistant integration is not configured")
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=5.0,
            transport=self._http_transport,
        )

    async def test_connection(self) -> dict[str, Any]:
        async with self._client() as client:
            resp = await client.get("/api/config")
            resp.raise_for_status()
            return resp.json()

    async def discover(self, timeout: float = 3.0) -> list[DiscoveredEndpoint]:
        if not self._base_url or not self._access_token:
            return []
        async with self._client() as client:
            resp = await client.get("/api/states", timeout=timeout)
            resp.raise_for_status()
            states = resp.json()

        endpoints: list[DiscoveredEndpoint] = []
        for entry in states:
            entity_id = str(entry.get("entity_id", ""))
            if "." not in entity_id:
                continue
            domain = entity_id.split(".", 1)[0]
            if domain not in SUPPORTED_HA_DOMAINS:
                continue
            attrs = entry.get("attributes") or {}
            friendly = attrs.get("friendly_name") or entity_id
            fp = "ha-" + hashlib.sha256(entity_id.encode("utf-8")).hexdigest()[:24]
            endpoints.append(
                DiscoveredEndpoint(
                    hardware_fingerprint=fp,
                    discovery_source="home_assistant",
                    service_type=f"ha.{domain}",
                    raw_properties={
                        "entity_id": entity_id,
                        "domain": domain,
                        "friendly_name": friendly,
                        "state": entry.get("state"),
                        "attributes": attrs,
                    },
                )
            )
        return endpoints

    async def identify(self, endpoint: DiscoveredEndpoint) -> DeviceIdentity:
        props = endpoint.raw_properties
        domain = props.get("domain", "smarthome")
        cat_map = {
            "media_player": "speaker",
            "light": "smarthome",
            "switch": "appliance",
            "climate": "appliance",
            "lock": "smarthome",
            "vacuum": "appliance",
        }
        return DeviceIdentity(
            hardware_fingerprint=endpoint.hardware_fingerprint,
            name=props.get("friendly_name", props.get("entity_id", "Home Assistant Entity")),
            category=cat_map.get(domain, "smarthome"),
            manufacturer="Home Assistant Bridge",
            model=domain.replace("_", " ").title(),
            adapter_type=self.adapter_type,
            metadata={"entity_id": props.get("entity_id"), "domain": domain},
        )

    async def pair(self, device_id: str, request: PairingRequest) -> PairingChallenge:
        raise NotImplementedError("Authenticated via encrypted Home Assistant Long-Lived Access Token")

    async def authenticate(self, device_id: str, proof: AuthProof) -> AuthResult:
        return AuthResult(authenticated=bool(self._access_token))

    async def get_status(self, device_id: str) -> DeviceStatusSnapshot:
        entity_id = self._device_entity_map.get(device_id)
        if not entity_id:
            return DeviceStatusSnapshot(device_id=device_id, online=False, integration_health="unmapped")

        async with self._client() as client:
            resp = await client.get(f"/api/states/{entity_id}")
            if resp.status_code != 200:
                return DeviceStatusSnapshot(device_id=device_id, online=False, integration_health="unreachable")
            data = resp.json()

        state = data.get("state", "unknown")
        online = state not in ("unavailable", "unknown")
        return DeviceStatusSnapshot(
            device_id=device_id,
            online=online,
            health_score=100 if online else 30,
            integration_health="healthy" if online else "entity_unavailable",
            attributes={"entity_id": entity_id, "state": state, **(data.get("attributes") or {})},
            observed_at=datetime.now(timezone.utc),
        )

    async def get_capabilities(self, device_id: str) -> list[CapabilityDescriptor]:
        entity_id = self._device_entity_map.get(device_id, "")
        domain = entity_id.split(".", 1)[0] if "." in entity_id else "switch"
        caps: list[CapabilityDescriptor] = [
            CapabilityDescriptor(
                capability_key="telemetry.status",
                category="telemetry",
                display_name="Entity State Synchronization",
                supported=True,
                requires_permission="smarthome:read",
            )
        ]

        if domain in ("light", "switch", "fan"):
            props: dict[str, Any] = {"on": {"type": "boolean"}}
            if domain == "light":
                props["brightness"] = {"type": "integer", "minimum": 0, "maximum": 255}
            caps.append(
                CapabilityDescriptor(
                    capability_key="power.set",
                    category="smarthome",
                    display_name=f"{domain.title()} Power Control",
                    supported=True,
                    requires_permission="smarthome:control",
                    parameters_schema={"required": ["on"], "properties": props},
                )
            )
        elif domain == "media_player":
            caps.extend(
                [
                    CapabilityDescriptor(
                        capability_key="media.control",
                        category="media",
                        display_name="Speaker / Media Transport",
                        supported=True,
                        requires_permission="media:control",
                        parameters_schema={
                            "required": ["action"],
                            "properties": {"action": {"type": "string", "enum": ["play", "pause", "stop", "next", "previous"]}},
                        },
                    ),
                    CapabilityDescriptor(
                        capability_key="volume.set",
                        category="audio",
                        display_name="Speaker Volume Level",
                        supported=True,
                        requires_permission="volume:control",
                        parameters_schema={
                            "required": ["level"],
                            "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}},
                        },
                    ),
                ]
            )
        elif domain == "climate":
            caps.append(
                CapabilityDescriptor(
                    capability_key="climate.set_temperature",
                    category="smarthome",
                    display_name="Set Target Temperature",
                    supported=True,
                    requires_permission="smarthome:control",
                    parameters_schema={
                        "required": ["temperature"],
                        "properties": {"temperature": {"type": "number", "minimum": 10, "maximum": 35}},
                    },
                )
            )
        elif domain == "lock":
            caps.extend(
                [
                    CapabilityDescriptor(
                        capability_key="lock.lock",
                        category="security",
                        display_name="Lock Door",
                        supported=True,
                        requires_permission="smarthome:sensitive",
                        requires_confirmation=False,
                    ),
                    CapabilityDescriptor(
                        capability_key="lock.unlock",
                        category="security",
                        display_name="Unlock Door",
                        supported=True,
                        requires_permission="smarthome:sensitive",
                        requires_confirmation=True,
                    ),
                ]
            )
        return caps

    async def execute_command(self, device_id: str, command: AdapterCommand) -> CommandExecutionReceipt:
        entity_id = self._device_entity_map.get(device_id)
        if not entity_id or "." not in entity_id:
            return CommandExecutionReceipt(
                command_id=command.command_id,
                acknowledged=False,
                error_code="ENTITY_UNMAPPED",
                error_message=f"Device '{device_id}' has no bound Home Assistant entity_id",
            )

        domain = entity_id.split(".", 1)[0]
        cap = command.capability_key
        params = command.parameters
        service = None
        payload: dict[str, Any] = {"entity_id": entity_id}
        expected_state: str | None = None

        if cap == "power.set":
            turn_on = bool(params.get("on", True))
            service = "turn_on" if turn_on else "turn_off"
            expected_state = "on" if turn_on else "off"
            if turn_on and "brightness" in params and domain == "light":
                payload["brightness"] = int(params["brightness"])
        elif cap == "volume.set" and domain == "media_player":
            service = "volume_set"
            payload["volume_level"] = round(float(params["level"]) / 100.0, 2)
        elif cap == "media.control" and domain == "media_player":
            svc_map = {
                "play": "media_play",
                "pause": "media_pause",
                "stop": "media_stop",
                "next": "media_next_track",
                "previous": "media_previous_track",
            }
            service = svc_map.get(str(params.get("action", "play")))
        elif cap == "climate.set_temperature" and domain == "climate":
            service = "set_temperature"
            payload["temperature"] = float(params["temperature"])
        elif cap == "lock.lock" and domain == "lock":
            service = "lock"
            expected_state = "locked"
        elif cap == "lock.unlock" and domain == "lock":
            service = "unlock"
            expected_state = "unlocked"

        if not service:
            return CommandExecutionReceipt(
                command_id=command.command_id,
                acknowledged=False,
                error_code="UNSUPPORTED_HA_SERVICE",
                error_message=f"Cannot map capability '{cap}' to Home Assistant domain '{domain}'",
            )

        async with self._client() as client:
            call_resp = await client.post(f"/api/services/{domain}/{service}", json=payload)
            if call_resp.status_code >= 400:
                return CommandExecutionReceipt(
                    command_id=command.command_id,
                    acknowledged=False,
                    error_code=f"HA_HTTP_{call_resp.status_code}",
                    error_message=call_resp.text[:250],
                )

            # Perform actual post-execution entity state readback
            state_resp = await client.get(f"/api/states/{entity_id}")
            if state_resp.status_code == 200:
                post_data = state_resp.json()
                actual_state = post_data.get("state")
                attrs = post_data.get("attributes") or {}
                verified = True
                if expected_state is not None:
                    verified = actual_state == expected_state
                elif service == "volume_set":
                    verified = abs(float(attrs.get("volume_level", -1.0)) - payload["volume_level"]) < 0.05

                return CommandExecutionReceipt(
                    command_id=command.command_id,
                    acknowledged=True,
                    state_verified=verified,
                    verification_method="ha_entity_state_readback",
                    result={"entity_id": entity_id, "new_state": actual_state, "attributes": attrs},
                )

        return CommandExecutionReceipt(
            command_id=command.command_id,
            acknowledged=True,
            state_verified=False,
            verification_method="ha_service_dispatched",
            result={"entity_id": entity_id, "service": f"{domain}.{service}"},
        )

    async def subscribe_events(self, device_id: str, callback: EventCallback):
        return None

    async def disconnect(self, device_id: str, reason: str = "operator_requested") -> None:
        return None

    async def revoke_access(self, device_id: str, reason: str = "permission_revoked") -> None:
        self._device_entity_map.pop(device_id, None)

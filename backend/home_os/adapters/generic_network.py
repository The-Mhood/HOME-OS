import asyncio
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

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
from home_os.services.discovery import lookup_mac_vendor, normalize_mac

PORT_LABELS: dict[int, str] = {
    22: "SSH",
    53: "DNS",
    80: "HTTP Web Admin",
    443: "HTTPS",
    445: "SMB File Share",
    548: "AFP Apple Share",
    1400: "Sonos UPnP",
    1883: "MQTT Broker",
    5000: "AirPlay / UPnP",
    5555: "Android Wireless ADB",
    6466: "Google TV Remote",
    7000: "Apple AirPlay",
    8008: "Google Cast HTTP",
    8009: "Google Cast TLS",
    8080: "HTTP Alt",
    8123: "Home Assistant",
    8443: "HTTPS Admin",
    62078: "Apple iOS Lockdown",
}

ENROLLED_NETWORK_CAPABILITIES: list[dict[str, Any]] = [
    {
        "capability_key": "network.ping",
        "category": "telemetry",
        "display_name": "Live Wi-Fi ICMP Ping & Latency Check",
        "description": "Measures real round-trip Wi-Fi latency and packet loss to this device.",
        "supported": True,
        "requires_permission": "telemetry:read",
    },
    {
        "capability_key": "network.port_scan",
        "category": "telemetry",
        "display_name": "Inspect Open Wi-Fi Services & Ports",
        "description": "Probes standard LAN ports (HTTP, AirPlay, Cast, Sonos, SMB, SSH, Home Assistant).",
        "supported": True,
        "requires_permission": "telemetry:read",
    },
    {
        "capability_key": "network.wake_on_lan",
        "category": "power",
        "display_name": "Send Wake-on-LAN Magic Packet",
        "description": "Broadcasts an IEEE 802.3 102-byte Magic Packet to wake sleeping Ethernet/Wi-Fi hardware.",
        "supported": True,
        "requires_permission": "power:control",
    },
    {
        "capability_key": "os.agent_required",
        "category": "system",
        "display_name": "Deep OS Controls (Volume / Media / Files)",
        "description": "Install or pair a HOME OS Companion / Smart-Home protocol for internal OS actions.",
        "supported": False,
        "unavailable_reason": "Network-level enrollment supports Ping, Port Inspection, and Wake-on-LAN. Pair via QR/Agent or Integration for internal OS controls.",
        "requires_permission": "telemetry:read",
    },
]


class GenericNetworkDeviceAdapter(BaseDeviceAdapter):
    """Adapter for LAN/Wi-Fi devices providing honest network-layer tools (Ping, Port Scan, Wake-on-LAN)."""

    adapter_type = "generic_network"

    def __init__(self, session_factory=None):
        self._session_factory = session_factory

    def _lookup_device_net(self, device_id: str) -> tuple[str | None, str | None]:
        if self._session_factory is None:
            return None, None
        from home_os.db.models import Device

        with self._session_factory() as db:
            dev = db.get(Device, device_id)
            if dev is None:
                return None, None
            return dev.ip_address, dev.mac_address

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
        raise PermissionError("Use 'Enable Network Tools' or pair via QR/Agent for OS-level control.")

    async def authenticate(self, device_id: str, proof: AuthProof) -> AuthResult:
        return AuthResult(authenticated=True)

    async def get_status(self, device_id: str) -> DeviceStatusSnapshot:
        ip_addr, _mac = self._lookup_device_net(device_id)
        reachable = False
        latency_ms: float | None = None
        if ip_addr:
            reachable, latency_ms = await asyncio.to_thread(self._ping_once, ip_addr)
        return DeviceStatusSnapshot(
            device_id=device_id,
            online=reachable,
            health_score=100 if reachable else 35,
            integration_health="healthy" if reachable else "offline",
            attributes={"ip_address": ip_addr, "latency_ms": latency_ms},
            observed_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _ping_once(ip_addr: str) -> tuple[bool, float | None]:
        ping_bin = "/sbin/ping" if sys.platform == "darwin" else "ping"
        args = (
            [ping_bin, "-c", "1", "-W", "400", ip_addr]
            if sys.platform == "darwin"
            else [ping_bin, "-c", "1", "-W", "1", ip_addr]
        )
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=1.5, check=False)
            if proc.returncode == 0:
                m = re.search(r"time[=<]([\d.]+)\s*ms", proc.stdout or "")
                return True, (float(m.group(1)) if m else None)
        except Exception:
            pass
        return False, None

    async def get_capabilities(self, device_id: str) -> list[CapabilityDescriptor]:
        return [CapabilityDescriptor(**c) for c in ENROLLED_NETWORK_CAPABILITIES]

    async def execute_command(self, device_id: str, command: AdapterCommand) -> CommandExecutionReceipt:
        ip_addr, mac_addr = self._lookup_device_net(device_id)
        params = command.parameters or {}
        ip_addr = str(params.get("ip_address") or ip_addr or "")
        mac_addr = normalize_mac(str(params.get("mac_address") or mac_addr or ""))

        if command.capability_key == "network.ping":
            if not ip_addr:
                return CommandExecutionReceipt(
                    command_id=command.command_id,
                    acknowledged=False,
                    state_verified=False,
                    error_code="MISSING_IP",
                    error_message="Device has no IPv4 address recorded",
                )
            ok, rtt = await asyncio.to_thread(self._ping_once, ip_addr)
            return CommandExecutionReceipt(
                command_id=command.command_id,
                acknowledged=True,
                state_verified=ok,
                verification_method="icmp_echo_reply" if ok else "icmp_timeout",
                result={"ip_address": ip_addr, "reachable": ok, "rtt_ms": rtt},
                error_code=None if ok else "HOST_UNREACHABLE",
                error_message=None if ok else f"No ICMP reply from {ip_addr}",
            )

        if command.capability_key == "network.port_scan":
            if not ip_addr:
                return CommandExecutionReceipt(
                    command_id=command.command_id,
                    acknowledged=False,
                    state_verified=False,
                    error_code="MISSING_IP",
                    error_message="Device has no IPv4 address recorded",
                )
            found: list[dict[str, Any]] = []

            async def _check(p: int) -> None:
                try:
                    _r, w = await asyncio.wait_for(asyncio.open_connection(ip_addr, p), timeout=0.35)
                    w.close()
                    try:
                        await w.wait_closed()
                    except Exception:
                        pass
                    found.append({"port": p, "service": PORT_LABELS.get(p, "TCP")})
                except Exception:
                    pass

            await asyncio.gather(*[_check(p) for p in PORT_LABELS])
            found.sort(key=lambda x: x["port"])
            return CommandExecutionReceipt(
                command_id=command.command_id,
                acknowledged=True,
                state_verified=True,
                verification_method="tcp_connect_scan",
                result={"ip_address": ip_addr, "open_services": found},
            )

        if command.capability_key == "network.wake_on_lan":
            if not mac_addr:
                return CommandExecutionReceipt(
                    command_id=command.command_id,
                    acknowledged=False,
                    state_verified=False,
                    error_code="MISSING_MAC",
                    error_message="Wake-on-LAN requires a known hardware MAC address on this device",
                )
            mac_bytes = bytes.fromhex(mac_addr.replace(":", ""))
            magic_packet = b"\xff" * 6 + mac_bytes * 16
            targets = ["255.255.255.255"]
            if ip_addr and "." in ip_addr:
                parts = ip_addr.split(".")
                if len(parts) == 4:
                    targets.append(f"{parts[0]}.{parts[1]}.{parts[2]}.255")

            def _send_wol() -> None:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    for bcast in targets:
                        try:
                            sock.sendto(magic_packet, (bcast, 9))
                        except OSError:
                            pass
                finally:
                    sock.close()

            await asyncio.to_thread(_send_wol)
            return CommandExecutionReceipt(
                command_id=command.command_id,
                acknowledged=True,
                state_verified=True,
                verification_method="wol_magic_packet_broadcast",
                result={"mac_address": mac_addr, "packet_bytes": len(magic_packet), "targets": targets},
            )

        return CommandExecutionReceipt(
            command_id=command.command_id,
            acknowledged=False,
            state_verified=False,
            error_code="UNSUPPORTED_GENERIC_DEVICE",
            error_message="Cannot execute OS-internal commands on a generic network device",
        )

    async def subscribe_events(self, device_id: str, callback: EventCallback):
        return None

    async def disconnect(self, device_id: str, reason: str = "operator_requested") -> None:
        return None

    async def revoke_access(self, device_id: str, reason: str = "permission_revoked") -> None:
        return None

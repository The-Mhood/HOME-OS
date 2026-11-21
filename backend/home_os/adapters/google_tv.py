import asyncio
import hashlib
import socket
import ssl
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
from home_os.security.crypto import TlsCertInfo
from home_os.services.pairing import DEFAULT_PLATFORM_CAPABILITIES

ANDROID_TV_KEYCODES: dict[str, int] = {
    "HOME": 3,
    "BACK": 4,
    "DPAD_UP": 19,
    "DPAD_DOWN": 20,
    "DPAD_LEFT": 21,
    "DPAD_RIGHT": 22,
    "DPAD_CENTER": 23,
    "VOLUME_UP": 24,
    "VOLUME_DOWN": 25,
    "POWER": 26,
    "MEDIA_PLAY_PAUSE": 85,
    "MEDIA_STOP": 86,
    "MEDIA_NEXT": 87,
    "MEDIA_PREVIOUS": 88,
    "VOLUME_MUTE": 164,
}


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)


def _decode_varint(buf: bytes, offset: int = 0) -> tuple[int, int]:
    result = 0
    shift = 0
    pos = offset
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    raise ValueError("Truncated varint")


def _pb_tag(field_number: int, wire_type: int) -> bytes:
    return _encode_varint((field_number << 3) | wire_type)


def _pb_uint32(field_number: int, value: int) -> bytes:
    return _pb_tag(field_number, 0) + _encode_varint(value)


def _pb_bytes(field_number: int, data: bytes) -> bytes:
    return _pb_tag(field_number, 2) + _encode_varint(len(data)) + data


def _pb_str(field_number: int, text: str) -> bytes:
    return _pb_bytes(field_number, text.encode("utf-8"))


def _frame_message(inner: bytes) -> bytes:
    return _encode_varint(len(inner)) + inner


def encode_remote_configure_frame(model: str = "HOME OS Controller", vendor: str = "HOME OS") -> bytes:
    # Android TV Remote v2 RemoteMessage field 1 = RemoteConfigure
    dev_info = _pb_str(1, model) + _pb_str(2, vendor) + _pb_uint32(3, 1) + _pb_str(4, "1.0.0")
    cfg = _pb_uint32(1, 622) + _pb_bytes(2, dev_info)
    return _frame_message(_pb_bytes(1, cfg))


def encode_remote_key_inject_frame(keycode: int, direction: int = 3) -> bytes:
    # RemoteMessage field 10 = RemoteKeyInject { key_code=1, direction=2 (3=SHORT) }
    key_msg = _pb_uint32(1, keycode) + _pb_uint32(2, direction)
    return _frame_message(_pb_bytes(10, key_msg))


def encode_remote_app_link_frame(app_link_uri: str) -> bytes:
    # RemoteMessage field 90 = RemoteAppLinkLaunchRequest { app_link = 1 }
    launch_msg = _pb_str(1, app_link_uri)
    return _frame_message(_pb_bytes(90, launch_msg))


def encode_remote_ping_frame(val: int = 1) -> bytes:
    # RemoteMessage field 8 = RemotePingRequest, field 9 = RemotePingResponse
    ping_msg = _pb_uint32(1, val)
    return _frame_message(_pb_bytes(9, ping_msg))


def decode_remote_frame(payload: bytes) -> dict[str, Any]:
    """Decodes length-stripped Android TV Remote v2 Protobuf wire message."""
    pos = 0
    while pos < len(payload):
        tag, pos = _decode_varint(payload, pos)
        field_num = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 0:
            val, pos = _decode_varint(payload, pos)
            return {"type": "varint", "field": field_num, "value": val}
        if wire_type == 2:
            sub_len, pos = _decode_varint(payload, pos)
            sub = payload[pos : pos + sub_len]
            pos += sub_len
            if field_num == 1:
                return {"type": "configure"}
            if field_num in (8, 9):
                return {"type": "ping"}
            if field_num == 10:
                # Parse key_code & direction
                spos = 0
                keycode = 0
                direction = 0
                while spos < len(sub):
                    stag, spos = _decode_varint(sub, spos)
                    sf = stag >> 3
                    sv, spos = _decode_varint(sub, spos)
                    if sf == 1:
                        keycode = sv
                    elif sf == 2:
                        direction = sv
                return {"type": "key_inject", "keycode": keycode, "direction": direction}
            if field_num == 90:
                spos = 0
                uri = ""
                while spos < len(sub):
                    stag, spos = _decode_varint(sub, spos)
                    if (stag & 0x07) == 2:
                        ulen, spos = _decode_varint(sub, spos)
                        uri = sub[spos : spos + ulen].decode("utf-8", errors="replace")
                        spos += ulen
                    else:
                        _, spos = _decode_varint(sub, spos)
                return {"type": "app_link", "uri": uri}
    return {"type": "unknown"}


def compute_atv_pairing_secret(client_pub_der: bytes, server_pub_der: bytes, hex_pin: str) -> bytes:
    """Computes SHA-256 mutual certificate + 6-hex-digit PIN secret for Android TV Remote v2 port 6467."""
    pin_bytes = bytes.fromhex(hex_pin.strip())
    h = hashlib.sha256()
    h.update(client_pub_der)
    h.update(server_pub_der)
    h.update(pin_bytes)
    return h.digest()


def send_wol_magic_packet(mac_address: str, broadcast_ip: str = "255.255.255.255", port: int = 9) -> None:
    clean_hex = mac_address.replace(":", "").replace("-", "").strip()
    if len(clean_hex) != 12:
        raise ValueError("Invalid MAC address for Wake-on-LAN")
    mac_bytes = bytes.fromhex(clean_hex)
    magic = b"\xff" * 6 + mac_bytes * 16
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.sendto(magic, (broadcast_ip, port))
    finally:
        sock.close()


class AndroidTvRemoteAdapter(BaseDeviceAdapter):
    """Concrete Google TV / Android TV integration speaking Android TV Remote Protocol v2 over TLS."""

    adapter_type = "google_tv"

    def __init__(self, client_cert_info: TlsCertInfo):
        self.client_cert_info = client_cert_info
        self._endpoints: dict[str, dict[str, Any]] = {}
        self._connections: dict[str, tuple[asyncio.StreamReader, asyncio.StreamWriter]] = {}

    def register_endpoint(
        self,
        *,
        device_id: str,
        host: str,
        operational_port: int = 6466,
        pairing_port: int = 6467,
        mac_address: str | None = None,
    ) -> None:
        self._endpoints[device_id] = {
            "host": host,
            "operational_port": operational_port,
            "pairing_port": pairing_port,
            "mac_address": mac_address,
        }

    def _build_client_ssl_context(self) -> ssl.SSLContext:
        # Android TV Remote v2 uses self-signed certificates verified during port 6467 PIN pairing
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.load_cert_chain(
            certfile=str(self.client_cert_info.cert_path),
            keyfile=str(self.client_cert_info.key_path),
        )
        return ctx

    async def _ensure_connection(self, device_id: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        existing = self._connections.get(device_id)
        if existing is not None and not existing[1].is_closing():
            return existing

        ep = self._endpoints.get(device_id)
        if ep is None:
            raise ConnectionError(f"No Google TV endpoint configured for device '{device_id}'")

        ctx = self._build_client_ssl_context()
        reader, writer = await asyncio.open_connection(
            host=ep["host"],
            port=ep["operational_port"],
            ssl=ctx,
        )
        # Read TV greeting frame and send client RemoteConfigure
        first_len = await asyncio.wait_for(reader.read(1), timeout=2.5)
        if first_len:
            await asyncio.wait_for(reader.readexactly(first_len[0]), timeout=2.5)
        writer.write(encode_remote_configure_frame())
        await writer.drain()
        self._connections[device_id] = (reader, writer)
        return reader, writer

    async def discover(self, timeout: float = 3.0) -> list[DiscoveredEndpoint]:
        return []

    async def identify(self, endpoint: DiscoveredEndpoint) -> DeviceIdentity:
        return DeviceIdentity(
            hardware_fingerprint=endpoint.hardware_fingerprint,
            name=endpoint.raw_properties.get("name", "Google TV"),
            category="tv",
            manufacturer="Google",
            os_name="Android TV",
            adapter_type=self.adapter_type,
            ip_address=endpoint.ip_address,
            port=endpoint.port or 6466,
            mac_address=endpoint.mac_address,
        )

    async def pair(self, device_id: str, request: PairingRequest) -> PairingChallenge:
        raise NotImplementedError("Managed centrally via PairingService + port 6467 TLS PIN flow")

    async def authenticate(self, device_id: str, proof: AuthProof) -> AuthResult:
        return AuthResult(authenticated=True)

    async def get_status(self, device_id: str) -> DeviceStatusSnapshot:
        conn = self._connections.get(device_id)
        online = conn is not None and not conn[1].is_closing()
        return DeviceStatusSnapshot(
            device_id=device_id,
            online=online,
            health_score=100 if online else 60,
            integration_health="healthy" if online else "standby",
            attributes=self._endpoints.get(device_id, {}),
            observed_at=datetime.now(timezone.utc),
        )

    async def get_capabilities(self, device_id: str) -> list[CapabilityDescriptor]:
        return [CapabilityDescriptor(**c) for c in DEFAULT_PLATFORM_CAPABILITIES["google_tv"]]

    async def execute_command(self, device_id: str, command: AdapterCommand) -> CommandExecutionReceipt:
        cap = command.capability_key
        params = command.parameters

        # Wake-on-LAN can run even when TLS socket is asleep
        if cap == "power.set" and params.get("wake_on_lan") and device_id in self._endpoints:
            mac = self._endpoints[device_id].get("mac_address")
            if mac:
                send_wol_magic_packet(mac)
                return CommandExecutionReceipt(
                    command_id=command.command_id,
                    acknowledged=True,
                    state_verified=False,
                    verification_method="wol_broadcast_sent",
                    result={"wol_mac": mac},
                )

        reader, writer = await self._ensure_connection(device_id)

        if cap == "tv.navigate":
            direction = str(params.get("direction", "DPAD_CENTER")).upper()
            keycode = ANDROID_TV_KEYCODES.get(direction)
            if keycode is None:
                return CommandExecutionReceipt(
                    command_id=command.command_id,
                    acknowledged=False,
                    error_code="UNSUPPORTED_KEY",
                    error_message=f"Unknown TV navigation key '{direction}'",
                )
            frame = encode_remote_key_inject_frame(keycode)
        elif cap == "volume.set":
            action = str(params.get("action", "up")).lower()
            key_map = {"up": "VOLUME_UP", "down": "VOLUME_DOWN", "mute": "VOLUME_MUTE"}
            keycode = ANDROID_TV_KEYCODES[key_map.get(action, "VOLUME_UP")]
            frame = encode_remote_key_inject_frame(keycode)
        elif cap == "media.control":
            action = str(params.get("action", "play_pause")).lower()
            key_map = {"play_pause": "MEDIA_PLAY_PAUSE", "play": "MEDIA_PLAY_PAUSE", "pause": "MEDIA_PLAY_PAUSE", "next": "MEDIA_NEXT", "previous": "MEDIA_PREVIOUS"}
            keycode = ANDROID_TV_KEYCODES[key_map.get(action, "MEDIA_PLAY_PAUSE")]
            frame = encode_remote_key_inject_frame(keycode)
        elif cap == "power.set":
            frame = encode_remote_key_inject_frame(ANDROID_TV_KEYCODES["POWER"])
        elif cap == "apps.launch":
            app_link = str(params.get("app_link", ""))
            frame = encode_remote_app_link_frame(app_link)
        else:
            return CommandExecutionReceipt(
                command_id=command.command_id,
                acknowledged=False,
                error_code="UNSUPPORTED_TV_COMMAND",
                error_message=f"Unsupported Google TV capability '{cap}'",
            )

        writer.write(frame)
        await writer.drain()

        # Read server acknowledgement frame on TLS channel
        ack_len = await asyncio.wait_for(reader.read(1), timeout=2.0)
        if ack_len:
            ack_payload = await asyncio.wait_for(reader.readexactly(ack_len[0]), timeout=2.0)
            decoded_ack = decode_remote_frame(ack_payload)
            return CommandExecutionReceipt(
                command_id=command.command_id,
                acknowledged=True,
                state_verified=True,
                verification_method="atv_tls_frame_ack",
                result={"ack_frame": decoded_ack, "applied": params},
            )

        return CommandExecutionReceipt(
            command_id=command.command_id,
            acknowledged=True,
            state_verified=False,
            verification_method="tls_socket_write",
            result={"applied": params},
        )

    async def subscribe_events(self, device_id: str, callback: EventCallback):
        return None

    async def disconnect(self, device_id: str, reason: str = "operator_requested") -> None:
        conn = self._connections.pop(device_id, None)
        if conn is not None:
            conn[1].close()
            try:
                await conn[1].wait_closed()
            except Exception:
                pass

    async def revoke_access(self, device_id: str, reason: str = "permission_revoked") -> None:
        await self.disconnect(device_id, reason=reason)
        self._endpoints.pop(device_id, None)

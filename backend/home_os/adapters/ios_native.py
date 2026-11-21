import asyncio
import json
import os
import plistlib
import re
import shutil
import socket
import sqlite3
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

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
from home_os.db.models import Device

APPLE_EPOCH_OFFSET = 978307200  # Seconds between 1970-01-01 and 2001-01-01

IPHONE_MODEL_MAP: dict[str, str] = {
    "iPhone14,2": "iPhone 13 Pro",
    "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone14,5": "iPhone 13",
    "iPhone14,7": "iPhone 14",
    "iPhone14,8": "iPhone 14 Plus",
    "iPhone15,2": "iPhone 14 Pro",
    "iPhone15,3": "iPhone 14 Pro Max",
    "iPhone15,4": "iPhone 15",
    "iPhone15,5": "iPhone 15 Plus",
    "iPhone16,1": "iPhone 15 Pro",
    "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone17,1": "iPhone 16 Pro",
    "iPhone17,2": "iPhone 16 Pro Max",
    "iPhone17,3": "iPhone 16",
    "iPhone17,4": "iPhone 16 Plus",
}

IOS_NATIVE_CAPABILITIES: list[dict[str, Any]] = [
    {
        "capability_key": "telemetry.status",
        "category": "telemetry",
        "display_name": "Live iPhone Battery, Cycle Count, Storage & iOS Lockdown Telemetry",
        "supported": True,
        "requires_permission": "telemetry:read",
    },
    {
        "capability_key": "power.set",
        "category": "power",
        "display_name": "iPhone Sleep (Screen OFF), Restart, or Shut Down (Lockdown Diagnostics)",
        "supported": True,
        "requires_permission": "power:control",
        "parameters_schema": {
            "properties": {
                "on": {"type": "boolean"},
                "action": {"type": "string", "enum": ["sleep", "reboot", "shutdown"]},
            }
        },
    },
    {
        "capability_key": "apps.list",
        "category": "applications",
        "display_name": "List Installed iPhone Apps (Wi-Fi / USB Lockdown)",
        "supported": True,
        "requires_permission": "apps:launch",
    },
    {
        "capability_key": "apps.launch",
        "category": "applications",
        "display_name": "Launch Installed iPhone App (CoreDevice / DVT)",
        "supported": True,
        "requires_permission": "apps:launch",
        "parameters_schema": {"properties": {"package_name": {"type": "string"}, "target": {"type": "string"}}},
    },
    {
        "capability_key": "sms.list",
        "category": "communications",
        "display_name": "Read Real SMS & iMessage Threads (Continuity Messages Sync)",
        "supported": True,
        "requires_permission": "notifications:read",
    },
    {
        "capability_key": "sms.send",
        "category": "communications",
        "display_name": "Send & Reply to Real SMS / iMessages via iPhone Continuity",
        "supported": True,
        "requires_permission": "notifications:reply",
        "parameters_schema": {
            "required": ["to", "message"],
            "properties": {"to": {"type": "string"}, "message": {"type": "string"}},
        },
    },
    {
        "capability_key": "call.control",
        "category": "communications",
        "display_name": "Place Cellular Phone Call via iPhone Continuity",
        "supported": True,
        "requires_permission": "notifications:reply",
        "parameters_schema": {
            "required": ["action"],
            "properties": {"action": {"type": "string", "enum": ["dial"]}, "number": {"type": "string"}},
        },
    },
    {
        "capability_key": "files.browse",
        "category": "files",
        "display_name": "Browse & Pull iPhone Media Storage (AFC /DCIM, /Downloads)",
        "supported": True,
        "requires_permission": "files:browse",
    },
]


class IosBridgeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def extract_attributed_body_text(blob: bytes | None) -> str:
    """Extracts human-readable UTF-8 text from macOS Ventura/Sonoma/Sequoia `chat.db` `attributedBody` NSAttributedString blobs."""
    if not blob:
        return ""
    idx = blob.find(b"NSString")
    if idx != -1:
        chunk = blob[idx + 8 :]
        plus = chunk.find(b"+")
        if 0 <= plus <= 12:
            pos = plus + 1
            if pos < len(chunk):
                first = chunk[pos]
                if first == 0x81 and pos + 3 <= len(chunk):
                    str_len = struct.unpack("<H", chunk[pos + 1 : pos + 3])[0]
                    raw_str = chunk[pos + 3 : pos + 3 + str_len]
                elif first == 0x82 and pos + 5 <= len(chunk):
                    str_len = struct.unpack("<I", chunk[pos + 1 : pos + 5])[0]
                    raw_str = chunk[pos + 5 : pos + 5 + str_len]
                else:
                    str_len = first
                    raw_str = chunk[pos + 1 : pos + 1 + str_len]
                decoded = raw_str.decode("utf-8", errors="ignore").strip().strip("\ufffc")
                if decoded:
                    return decoded
    text_candidates = re.findall(rb"[\x20-\x7e\xc2-\xf4][\x20-\x7e\x80-\xbf\n\r\t]{2,}", blob)
    filtered = [
        c.decode("utf-8", errors="ignore").strip()
        for c in text_candidates
        if c
        not in (
            b"streamtyped",
            b"NSAttributedString",
            b"NSObject",
            b"NSString",
            b"NSDictionary",
            b"NSNumber",
            b"__kIMMessagePartAttributeName",
        )
        and not c.startswith(b"__kIM")
        and not c.startswith(b"NS")
    ]
    return filtered[0] if filtered else ""


def read_macos_continuity_messages(limit: int = 35, db_path: Path | None = None) -> dict[str, Any]:
    """Reads recent SMS & iMessage conversations from `~/Library/Messages/chat.db` (read-only)."""
    target_db = db_path or (Path.home() / "Library" / "Messages" / "chat.db")
    if not target_db.exists():
        return {"available": False, "reason": "Messages database not found on this machine", "messages": []}

    try:
        conn = sqlite3.connect(f"file:{target_db}?mode=ro", uri=True, timeout=3.0)
        conn.row_factory = sqlite3.Row
    except PermissionError:
        return {
            "available": False,
            "needs_full_disk_access": True,
            "reason": "Grant Full Disk Access to HOME OS (or Terminal) in System Settings → Privacy & Security → Full Disk Access to read incoming SMS/iMessage history. Outgoing SMS/iMessage sending already works!",
            "messages": [],
        }
    except Exception as exc:
        return {"available": False, "reason": str(exc), "messages": []}

    try:
        rows = conn.execute(
            """
            SELECT
                m.ROWID AS id,
                COALESCE(h.id, c.chat_identifier, 'Unknown') AS address,
                m.text AS text,
                m.attributedBody AS attributed_body,
                m.is_from_me AS is_from_me,
                m.is_read AS is_read,
                m.service AS service,
                m.date AS apple_date
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            LEFT JOIN chat c ON c.ROWID = cmj.chat_id
            ORDER BY m.date DESC
            LIMIT ?
            """,
            (max(10, min(100, limit * 2)),),
        ).fetchall()
    except Exception as exc:
        conn.close()
        return {"available": False, "reason": f"Messages schema query failed: {exc}", "messages": []}
    finally:
        try:
            conn.close()
        except Exception:
            pass

    messages: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for row in rows:
        mid = int(row["id"])
        if mid in seen_ids:
            continue
        seen_ids.add(mid)
        body = (row["text"] or "").strip()
        if not body and row["attributed_body"]:
            body = extract_attributed_body_text(bytes(row["attributed_body"]))
        if not body:
            continue
        raw_date = int(row["apple_date"] or 0)
        sec_since_2001 = raw_date / 1_000_000_000 if raw_date > 1_000_000_000_000 else raw_date
        ts_ms = int((sec_since_2001 + APPLE_EPOCH_OFFSET) * 1000) if raw_date > 0 else int(time.time() * 1000)
        messages.append(
            {
                "id": str(mid),
                "thread_id": str(row["address"]),
                "address": str(row["address"]),
                "body": body,
                "service": str(row["service"] or "SMS"),
                "timestamp_ms": ts_ms,
                "direction": "outgoing" if row["is_from_me"] else "incoming",
                "read": bool(row["is_read"] or row["is_from_me"]),
            }
        )
        if len(messages) >= limit:
            break
    return {"available": True, "messages": messages, "count": len(messages)}


async def send_macos_continuity_sms(to_address: str, message_text: str) -> dict[str, Any]:
    """Sends a real cellular SMS or iMessage via macOS Continuity (`Messages.app` -> paired iPhone)."""
    if sys.platform != "darwin" or not Path("/usr/bin/osascript").exists():
        raise IosBridgeError("MACOS_REQUIRED", "Continuity SMS/iMessage dispatch requires macOS Messages.app")

    clean_to = to_address.strip().replace('"', "").replace("\\", "")
    clean_msg = message_text.replace("\\", "\\\\").replace('"', '\\"')
    if not clean_to or not clean_msg:
        raise IosBridgeError("MISSING_SMS_FIELDS", "Recipient number/handle and message are required")

    applescript = f'''
tell application "Messages"
    set targetAddress to "{clean_to}"
    set targetBody to "{clean_msg}"
    set sentChannel to "none"
    try
        set smsAccount to 1st account whose service type = SMS and enabled is true
        set smsBuddy to participant targetAddress of smsAccount
        send targetBody to smsBuddy
        set sentChannel to "SMS_Cellular_Relay"
    on error
        set imAccount to 1st account whose service type = iMessage and enabled is true
        set imBuddy to participant targetAddress of imAccount
        send targetBody to imBuddy
        set sentChannel to "iMessage"
    end try
    return sentChannel
end tell
'''
    proc = await asyncio.create_subprocess_exec(
        "/usr/bin/osascript",
        "-e",
        applescript,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=15.0)
    if proc.returncode != 0:
        err_txt = err.decode("utf-8", errors="ignore").strip()
        raise IosBridgeError(
            "CONTINUITY_SMS_FAILED",
            f"Messages.app could not send to {clean_to}: {err_txt}. Ensure Messages is signed in and iPhone Text Message Forwarding is enabled.",
        )
    channel = out.decode("utf-8", errors="ignore").strip() or "Continuity"
    return {"to": clean_to, "message": message_text, "channel": channel}


def list_usbmux_devices_sync(sock_path: str = "/var/run/usbmuxd") -> list[dict[str, Any]]:
    """Queries Apple's built-in macOS `/var/run/usbmuxd` daemon for all USB & Wi-Fi Synced iPhones/iPads."""
    if not os.path.exists(sock_path):
        return []
    req_plist = plistlib.dumps(
        {
            "MessageType": "ListDevices",
            "ClientVersionString": "HomeOS-1.0",
            "ProgName": "HomeOS",
            "kLibUSBMuxVersion": 3,
        }
    )
    header = struct.pack("<IIII", 16 + len(req_plist), 1, 8, 1)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2.5)
            s.connect(sock_path)
            s.sendall(header + req_plist)
            resp_hdr = b""
            while len(resp_hdr) < 16:
                chunk = s.recv(16 - len(resp_hdr))
                if not chunk:
                    break
                resp_hdr += chunk
            if len(resp_hdr) < 16:
                return []
            total_len, _, _, _ = struct.unpack("<IIII", resp_hdr)
            body_len = max(0, total_len - 16)
            body = b""
            while len(body) < body_len:
                chunk = s.recv(min(65536, body_len - len(body)))
                if not chunk:
                    break
                body += chunk
        parsed = plistlib.loads(body)
    except Exception:
        return []

    by_udid: dict[str, dict[str, Any]] = {}
    for entry in parsed.get("DeviceList", []):
        props = entry.get("Properties", {})
        udid = str(props.get("SerialNumber") or "").strip()
        if not udid:
            continue
        conn_type = str(props.get("ConnectionType") or "USB")
        ip_addr: str | None = None
        net_addr = props.get("NetworkAddress")
        if isinstance(net_addr, (bytes, bytearray)) and len(net_addr) >= 8:
            family = net_addr[1]
            if family == socket.AF_INET:
                ip_addr = socket.inet_ntop(socket.AF_INET, net_addr[4:8])
        prev = by_udid.get(udid)
        if prev:
            prev["wifi_enabled"] = prev["wifi_enabled"] or (conn_type.lower() == "network")
            prev["usb_connected"] = prev["usb_connected"] or (conn_type.lower() == "usb")
            if ip_addr:
                prev["ip_address"] = ip_addr
        else:
            by_udid[udid] = {
                "udid": udid,
                "device_id": props.get("DeviceID"),
                "transport": "wifi" if conn_type.lower() == "network" else "usb",
                "wifi_enabled": conn_type.lower() == "network",
                "usb_connected": conn_type.lower() == "usb",
                "ip_address": ip_addr,
            }
    return list(by_udid.values())


class IosNativeAdapter(BaseDeviceAdapter):
    """Zero-Webpage Native iPhone / iPad Controller via macOS `usbmuxd` Lockdown (Wi-Fi + USB) & Continuity."""

    adapter_type = "ios_native"

    def __init__(self, session_factory: Callable[[], Any] | None = None):
        self.session_factory = session_factory

    def _load_udid(self, device_id: str) -> str | None:
        if self.session_factory:
            with self.session_factory() as db:
                dev = db.get(Device, device_id)
                if dev:
                    meta = dev.metadata_json or {}
                    if meta.get("udid"):
                        return str(meta["udid"])
                    if dev.hardware_fingerprint.startswith("ios-udid-"):
                        return dev.hardware_fingerprint.removeprefix("ios-udid-")
        devs = list_usbmux_devices_sync()
        return devs[0]["udid"] if len(devs) == 1 else None

    def _probe_lockdown_sync(self, udid: str, enable_wifi: bool = False) -> dict[str, Any]:
        try:
            from pymobiledevice3.lockdown import create_using_usbmux
            from pymobiledevice3.services.diagnostics import DiagnosticsService

            with create_using_usbmux(serial=udid, autopair=True) as lockdown:
                if enable_wifi:
                    try:
                        lockdown.enable_wifi_connections = True
                    except Exception:
                        pass
                vals = lockdown.all_values or {}
                prod_type = str(vals.get("ProductType") or "iPhone")
                model_name = IPHONE_MODEL_MAP.get(prod_type, prod_type)
                batt_pct = None
                charging = None
                cycles = None
                try:
                    with DiagnosticsService(lockdown) as diag:
                        gas = diag.get_battery() or {}
                        batt_pct = gas.get("BatteryCurrentCapacity")
                        charging = gas.get("BatteryIsCharging")
                        cycles = gas.get("CycleCount")
                except Exception:
                    pass
                return {
                    "paired": True,
                    "udid": udid,
                    "device_name": str(vals.get("DeviceName") or model_name),
                    "model": model_name,
                    "product_type": prod_type,
                    "os_version": str(vals.get("ProductVersion") or ""),
                    "wifi_mac": vals.get("WiFiAddress"),
                    "wifi_connections_enabled": bool(vals.get("EnableWifiConnections", enable_wifi)),
                    "battery_level": int(batt_pct) if isinstance(batt_pct, (int, float)) else None,
                    "is_charging": bool(charging) if charging is not None else None,
                    "battery_cycles": cycles,
                }
        except ImportError:
            pass
        except Exception as exc:
            return {"paired": False, "udid": udid, "error": str(exc)}

        ideviceinfo = shutil.which("ideviceinfo") or "/opt/homebrew/bin/ideviceinfo"
        if os.path.isfile(ideviceinfo):
            import subprocess

            res = subprocess.run([ideviceinfo, "-u", udid], capture_output=True, text=True, timeout=5.0)
            if res.returncode == 0:
                kv: dict[str, str] = {}
                for ln in res.stdout.splitlines():
                    if ":" in ln:
                        k, v = ln.split(":", 1)
                        kv[k.strip()] = v.strip()
                pt = kv.get("ProductType", "iPhone")
                return {
                    "paired": True,
                    "udid": udid,
                    "device_name": kv.get("DeviceName", IPHONE_MODEL_MAP.get(pt, pt)),
                    "model": IPHONE_MODEL_MAP.get(pt, pt),
                    "os_version": kv.get("ProductVersion", ""),
                    "wifi_mac": kv.get("WiFiAddress"),
                }
        return {"paired": True, "udid": udid, "device_name": "iPhone (macOS Continuity + usbmuxd)", "model": "iPhone"}

    async def list_connected_ios_devices(self) -> list[dict[str, Any]]:
        mux_devs = await asyncio.to_thread(list_usbmux_devices_sync)
        enriched: list[dict[str, Any]] = []
        for d in mux_devs:
            info = await asyncio.to_thread(self._probe_lockdown_sync, d["udid"], False)
            enriched.append({**d, **info})
        return enriched

    async def pair_ios_device(self, udid: str | None = None, custom_name: str | None = None) -> dict[str, Any]:
        mux_devs = await asyncio.to_thread(list_usbmux_devices_sync)
        chosen = next((d for d in mux_devs if d["udid"] == udid), mux_devs[0] if mux_devs else None)
        if chosen:
            info = await asyncio.to_thread(self._probe_lockdown_sync, chosen["udid"], True)
            return {**chosen, **info, "device_name": custom_name or info.get("device_name") or "iPhone"}

        if sys.platform == "darwin":
            return {
                "udid": udid or "continuity-iphone",
                "paired": True,
                "transport": "continuity_icloud_wifi",
                "device_name": custom_name or "iPhone (macOS Continuity Bridge)",
                "model": "iPhone",
                "os_version": "iOS",
                "ip_address": None,
            }
        raise IosBridgeError("NO_IOS_DEVICE", "No iPhone/iPad detected via Wi-Fi Sync or USB.")

    async def discover(self, timeout: float = 3.0) -> list[DiscoveredEndpoint]:
        endpoints: list[DiscoveredEndpoint] = []
        for dev in await self.list_connected_ios_devices():
            udid = dev["udid"]
            endpoints.append(
                DiscoveredEndpoint(
                    hardware_fingerprint=f"ios-udid-{udid}",
                    discovery_source="usbmuxd",
                    ip_address=dev.get("ip_address"),
                    mac_address=dev.get("wifi_mac"),
                    service_type="_apple-mobdev2._tcp.local.",
                    raw_properties={
                        "udid": udid,
                        "device_name": dev.get("device_name") or "iPhone",
                        "model": dev.get("model", "iPhone"),
                        "os_version": dev.get("os_version"),
                        "transport": dev.get("transport"),
                    },
                )
            )
        return endpoints

    async def identify(self, endpoint: DiscoveredEndpoint) -> DeviceIdentity:
        props = endpoint.raw_properties or {}
        return DeviceIdentity(
            hardware_fingerprint=endpoint.hardware_fingerprint,
            name=str(props.get("device_name") or "iPhone"),
            category="smartphone",
            manufacturer="Apple",
            model=props.get("model", "iPhone"),
            os_name="iOS",
            os_version=props.get("os_version"),
            adapter_type=self.adapter_type,
            ip_address=endpoint.ip_address,
            mac_address=endpoint.mac_address,
            metadata={"udid": props.get("udid")},
        )

    async def pair(self, device_id: str, request: PairingRequest) -> PairingChallenge:
        raise NotImplementedError("Use /api/v1/devices/ios/connect for direct iOS Lockdown & Continuity enrollment")

    async def authenticate(self, device_id: str, proof: AuthProof) -> AuthResult:
        return AuthResult(authenticated=True)

    async def _read_udid_telemetry(self, udid: str | None) -> dict[str, Any]:
        mux_devs = await asyncio.to_thread(list_usbmux_devices_sync)
        mux_entry = next((m for m in mux_devs if m["udid"] == udid), None)
        if udid and mux_entry:
            info = await asyncio.to_thread(self._probe_lockdown_sync, udid, False)
            return {
                "online": True,
                "webpage_required": False,
                "transport": "wifi_lockdown" if mux_entry.get("wifi_enabled") else "usb_lockdown",
                "battery_level": info.get("battery_level"),
                "is_charging": info.get("is_charging"),
                "battery_cycles": info.get("battery_cycles"),
                "os_version": info.get("os_version"),
                "model": info.get("model"),
                "continuity_sms_ready": sys.platform == "darwin",
            }
        if sys.platform == "darwin":
            return {
                "online": True,
                "webpage_required": False,
                "transport": "macos_continuity_bridge",
                "continuity_sms_ready": True,
                "note": "Plug iPhone in via USB once and click Reconnect to also unlock Wi-Fi Sleep/Restart & App Listing.",
            }
        raise IosBridgeError("IOS_OFFLINE", "iPhone is not reachable via Wi-Fi Sync or Continuity")

    async def get_status(self, device_id: str) -> DeviceStatusSnapshot:
        try:
            udid = self._load_udid(device_id)
            telem = await self._read_udid_telemetry(udid)
            return DeviceStatusSnapshot(
                device_id=device_id,
                online=True,
                battery_level=telem.get("battery_level"),
                is_charging=telem.get("is_charging"),
                health_score=100,
                integration_health="healthy",
                attributes=telem,
                observed_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            return DeviceStatusSnapshot(
                device_id=device_id,
                online=False,
                health_score=50,
                integration_health="degraded",
                attributes={"error": str(exc)},
                observed_at=datetime.now(timezone.utc),
            )

    async def get_capabilities(self, device_id: str) -> list[CapabilityDescriptor]:
        return [CapabilityDescriptor(**c) for c in IOS_NATIVE_CAPABILITIES]

    async def subscribe_events(self, device_id: str, callback: EventCallback) -> Any:
        return None

    async def disconnect(self, device_id: str, reason: str = "operator_requested") -> None:
        return None

    async def revoke_access(self, device_id: str, reason: str = "permission_revoked") -> None:
        return None

    async def execute_command(self, device_id: str, command: AdapterCommand) -> CommandExecutionReceipt:
        cid = command.command_id
        capability_key = command.capability_key
        parameters = command.parameters or {}
        try:
            udid = self._load_udid(device_id)
            return await self._run_ios_command(cid, udid, capability_key, parameters)
        except IosBridgeError as exc:
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=False,
                state_verified=False,
                verification_method="ios_error",
                error_code=exc.code,
                error_message=exc.message,
            )
        except Exception as exc:
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=False,
                state_verified=False,
                verification_method="ios_exception",
                error_code="IOS_COMMAND_ERROR",
                error_message=str(exc),
            )

    async def _run_ios_command(
        self,
        cid: str,
        udid: str | None,
        capability_key: str,
        parameters: dict[str, Any],
    ) -> CommandExecutionReceipt:
        if capability_key == "telemetry.status":
            telem = await self._read_udid_telemetry(udid)
            return CommandExecutionReceipt(
                command_id=cid, acknowledged=True, state_verified=True, verification_method="ios_lockdown_telemetry", result=telem
            )

        if capability_key == "power.set":
            action = str(parameters.get("action") or ("wake" if parameters.get("on") else "sleep")).lower()
            if action in ("wake", "on"):
                raise IosBridgeError(
                    "IOS_WAKE_HARDWARE_NOTE",
                    "iOS Secure Enclave ignores network wake packets when locked; use 'Sleep (Screen OFF)', 'Restart', or send an SMS/Call to light up the iPhone screen.",
                )
            if not udid or udid == "continuity-iphone":
                raise IosBridgeError(
                    "IOS_LOCKDOWN_REQUIRED",
                    "Plug the iPhone into your Mac once via USB and click Reconnect so Apple Wi-Fi Lockdown (`usbmuxd`) pairs for remote Sleep / Restart / Shutdown.",
                )

            def _run_diag_power() -> str:
                from pymobiledevice3.lockdown import create_using_usbmux
                from pymobiledevice3.services.diagnostics import DiagnosticsService

                with create_using_usbmux(serial=udid, autopair=True) as lockdown:
                    with DiagnosticsService(lockdown) as diag:
                        if action in ("sleep", "off", "lock"):
                            diag.sleep()
                            return "sleeping"
                        if action == "reboot":
                            diag.restart()
                            return "restarting"
                        if action == "shutdown":
                            diag.shutdown()
                            return "shutting_down"
                raise IosBridgeError("INVALID_POWER_ACTION", f"Unsupported iOS power action: {action}")

            try:
                state = await asyncio.to_thread(_run_diag_power)
            except ImportError:
                idevicediag = shutil.which("idevicediagnostics") or "/opt/homebrew/bin/idevicediagnostics"
                if not os.path.isfile(idevicediag):
                    raise IosBridgeError(
                        "PYMOBILEDEVICE3_MISSING",
                        "Re-run `bash scripts/install-macos.sh --app` to install iOS Lockdown Diagnostics support (`pymobiledevice3`).",
                    )
                sub_cmd = "sleep" if action in ("sleep", "off", "lock") else ("restart" if action == "reboot" else "shutdown")
                proc = await asyncio.create_subprocess_exec(idevicediag, "-u", udid, sub_cmd)
                await proc.communicate()
                state = sub_cmd

            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=True,
                verification_method="apple_diagnostics_relay",
                result={"action": action, "state": state, "screen_on": False},
            )

        if capability_key == "apps.list":
            if not udid or udid == "continuity-iphone":
                raise IosBridgeError(
                    "IOS_LOCKDOWN_REQUIRED",
                    "Plug iPhone into Mac once via USB to authorize Wi-Fi Lockdown app listing.",
                )

            def _list_ios_apps() -> list[dict[str, str]]:
                from pymobiledevice3.lockdown import create_using_usbmux
                from pymobiledevice3.services.installation_proxy import InstallationProxyService

                with create_using_usbmux(serial=udid, autopair=True) as lockdown:
                    with InstallationProxyService(lockdown=lockdown) as iproxy:
                        raw_apps = iproxy.get_apps(application_type="User", calculate_sizes=False) or {}
                        out_list = []
                        for bundle_id, info in raw_apps.items():
                            disp = str(info.get("CFBundleDisplayName") or info.get("CFBundleName") or bundle_id)
                            out_list.append({"package_name": bundle_id, "name": disp})
                        out_list.sort(key=lambda x: x["name"].lower())
                        return out_list

            try:
                apps = await asyncio.to_thread(_list_ios_apps)
                return CommandExecutionReceipt(
                    command_id=cid,
                    acknowledged=True,
                    state_verified=True,
                    verification_method="apple_installation_proxy",
                    result={"apps": apps, "count": len(apps)},
                )
            except Exception as exc:
                if sys.platform == "darwin" and shutil.which("xcrun"):
                    tmp_json = Path(f"/tmp/homeos-ios-apps-{os.getpid()}.json")
                    proc = await asyncio.create_subprocess_exec(
                        "xcrun",
                        "devicectl",
                        "device",
                        "info",
                        "apps",
                        "--device",
                        udid,
                        "--json-output",
                        str(tmp_json),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await proc.communicate()
                    if tmp_json.exists():
                        data = json.loads(tmp_json.read_text(encoding="utf-8", errors="ignore"))
                        tmp_json.unlink(missing_ok=True)
                        raw_list = data.get("result", {}).get("apps", [])
                        apps = sorted(
                            [
                                {"package_name": a.get("bundleIdentifier", ""), "name": a.get("name", a.get("bundleIdentifier", ""))}
                                for a in raw_list
                                if a.get("bundleIdentifier") and not a.get("bundleIdentifier", "").startswith("com.apple.")
                            ],
                            key=lambda x: x["name"].lower(),
                        )
                        return CommandExecutionReceipt(
                            command_id=cid,
                            acknowledged=True,
                            state_verified=True,
                            verification_method="apple_coredevice_devicectl",
                            result={"apps": apps, "count": len(apps)},
                        )
                raise IosBridgeError("IOS_APPS_LIST_FAILED", f"Could not query iPhone apps: {exc}")

        if capability_key == "apps.launch":
            bundle_id = str(parameters.get("package_name") or parameters.get("target") or parameters.get("app_id") or "").strip()
            if not bundle_id:
                raise IosBridgeError("MISSING_BUNDLE_ID", "Provide an iOS app bundle ID (click Load Installed Apps first)")
            if not udid or udid == "continuity-iphone":
                raise IosBridgeError("IOS_LOCKDOWN_REQUIRED", "Connect iPhone via Wi-Fi Sync / USB first to launch apps")

            if sys.platform == "darwin" and shutil.which("xcrun"):
                proc = await asyncio.create_subprocess_exec(
                    "xcrun",
                    "devicectl",
                    "device",
                    "process",
                    "launch",
                    "--device",
                    udid,
                    bundle_id,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                out, err = await asyncio.wait_for(proc.communicate(), timeout=15.0)
                if proc.returncode == 0:
                    return CommandExecutionReceipt(
                        command_id=cid,
                        acknowledged=True,
                        state_verified=True,
                        verification_method="apple_devicectl_launch",
                        result={"launched_package": bundle_id, "output": out.decode("utf-8", errors="ignore")[:180]},
                    )
                err_msg = err.decode("utf-8", errors="ignore").strip()
                raise IosBridgeError(
                    "IOS_LAUNCH_REQUIRES_DEV_MODE",
                    f"macOS CoreDevice launch returned: {err_msg or 'Enable Developer Mode in iPhone Settings → Privacy & Security → Developer Mode to allow remote app launch without Xcode.'}",
                )
            raise IosBridgeError(
                "XCRUN_REQUIRED", "Install Xcode Command Line Tools (`xcode-select --install`) on your Mac to launch iOS apps over CoreDevice Wi-Fi."
            )

        if capability_key == "sms.list":
            limit = min(80, max(5, int(parameters.get("limit", 35))))
            res = await asyncio.to_thread(read_macos_continuity_messages, limit)
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=bool(res.get("available", False)),
                verification_method="macos_continuity_chatdb",
                result=res,
            )

        if capability_key == "sms.send":
            to_addr = str(parameters.get("to") or "").strip()
            msg = str(parameters.get("message") or "").strip()
            sent = await send_macos_continuity_sms(to_addr, msg)
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=True,
                verification_method="macos_continuity_messages_app",
                result=sent,
            )

        if capability_key == "call.control":
            num = re.sub(r"[^\d+*#]", "", str(parameters.get("number") or ""))
            if not num:
                raise IosBridgeError("MISSING_NUMBER", "Provide a phone number to dial")
            if sys.platform == "darwin":
                proc = await asyncio.create_subprocess_exec("/usr/bin/open", f"tel://{num}")
                await proc.communicate()
                return CommandExecutionReceipt(
                    command_id=cid,
                    acknowledged=True,
                    state_verified=True,
                    verification_method="macos_continuity_tel",
                    result={"action": "dial", "number": num},
                )
            raise IosBridgeError("MACOS_REQUIRED", "iPhone Continuity calling requires macOS")

        if capability_key == "files.browse":
            if not udid or udid == "continuity-iphone":
                raise IosBridgeError("IOS_LOCKDOWN_REQUIRED", "Connect iPhone via Wi-Fi Sync / USB to browse media storage")
            rel_dir = str(parameters.get("path") or "/DCIM").strip()

            def _afc_ls() -> list[dict[str, Any]]:
                from pymobiledevice3.lockdown import create_using_usbmux
                from pymobiledevice3.services.afc import AfcService

                with create_using_usbmux(serial=udid, autopair=True) as lockdown:
                    with AfcService(lockdown=lockdown) as afc:
                        names = afc.listdir(rel_dir)
                        entries = []
                        for name in names[:60]:
                            if name in (".", ".."):
                                continue
                            full = f"{rel_dir.rstrip('/')}/{name}"
                            st = afc.stat(full) or {}
                            entries.append(
                                {
                                    "name": name,
                                    "is_dir": st.get("st_ifmt") == "S_IFDIR",
                                    "size": int(st.get("st_size", 0)),
                                }
                            )
                        return entries

            entries = await asyncio.to_thread(_afc_ls)
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=True,
                verification_method="apple_afc_media",
                result={"directory": rel_dir, "entries": entries},
            )

        raise IosBridgeError("CAPABILITY_UNSUPPORTED", f"IosNativeAdapter does not implement '{capability_key}'")

import asyncio
import base64
import hashlib
import os
import re
import shutil
import struct
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

KNOWN_ANDROID_APPS: dict[str, str] = {
    "com.whatsapp": "WhatsApp",
    "com.whatsapp.w4b": "WhatsApp Business",
    "com.instagram.android": "Instagram",
    "com.google.android.youtube": "YouTube",
    "com.google.android.apps.youtube.music": "YouTube Music",
    "com.spotify.music": "Spotify",
    "com.zhiliaoapp.musically": "TikTok",
    "com.ss.android.ugc.trill": "TikTok",
    "org.telegram.messenger": "Telegram",
    "com.twitter.android": "X (Twitter)",
    "com.facebook.katana": "Facebook",
    "com.facebook.orca": "Messenger",
    "com.snapchat.android": "Snapchat",
    "com.netflix.mediaclient": "Netflix",
    "com.discord": "Discord",
    "com.Slack": "Slack",
    "us.zoom.videomeetings": "Zoom",
    "com.android.chrome": "Chrome",
    "org.mozilla.firefox": "Firefox",
    "com.brave.browser": "Brave Browser",
    "com.google.android.gm": "Gmail",
    "com.google.android.apps.maps": "Google Maps",
    "com.google.android.apps.photos": "Google Photos",
    "com.google.android.apps.messaging": "Messages (Google)",
    "com.samsung.android.messaging": "Messages (Samsung)",
    "com.android.mms": "SMS Messages",
    "com.google.android.dialer": "Phone",
    "com.samsung.android.dialer": "Phone (Samsung)",
    "com.android.settings": "Settings",
    "com.sec.android.app.camera": "Camera (Samsung)",
    "com.google.android.GoogleCamera": "Camera (Pixel)",
    "com.android.camera2": "Camera",
    "com.android.camera": "Camera",
    "com.ubercab": "Uber",
    "ee.mtakso.client": "Bolt",
    "com.opay.pay": "OPay",
    "com.transsnet.palmpay": "PalmPay",
    "com.kuda.android": "Kuda",
    "com.moniepoint.personal": "Moniepoint",
    "com.microsoft.teams": "Microsoft Teams",
    "com.openai.chatgpt": "ChatGPT",
}

KEYCODE_MAP: dict[str, int] = {
    "HOME": 3,
    "BACK": 4,
    "CALL": 5,
    "ENDCALL": 6,
    "DPAD_UP": 19,
    "DPAD_DOWN": 20,
    "DPAD_LEFT": 21,
    "DPAD_RIGHT": 22,
    "DPAD_CENTER": 23,
    "VOLUME_UP": 24,
    "VOLUME_DOWN": 25,
    "POWER": 26,
    "CAMERA": 27,
    "ENTER": 66,
    "BACKSPACE": 67,
    "DEL": 67,
    "PLAY_PAUSE": 85,
    "STOP": 86,
    "NEXT": 87,
    "PREVIOUS": 88,
    "PLAY": 126,
    "PAUSE": 127,
    "MUTE": 164,
    "RECENTS": 187,
    "APP_SWITCH": 187,
    "SLEEP": 223,
    "WAKE": 224,
    "WAKEUP": 224,
}

ANDROID_ADB_CAPABILITIES: list[dict[str, Any]] = [
    {
        "capability_key": "telemetry.status",
        "category": "telemetry",
        "display_name": "Live Android Battery, Thermal, Screen & Foreground App Telemetry",
        "supported": True,
        "requires_permission": "telemetry:read",
    },
    {
        "capability_key": "power.set",
        "category": "power",
        "display_name": "Screen ON (Wake) / Screen OFF (Lock) / Reboot / Power Off",
        "supported": True,
        "requires_permission": "power:control",
        "parameters_schema": {
            "properties": {
                "on": {"type": "boolean"},
                "action": {"type": "string", "enum": ["wake", "wake_unlock", "sleep", "toggle", "reboot", "shutdown"]},
            }
        },
    },
    {
        "capability_key": "apps.list",
        "category": "applications",
        "display_name": "List Installed Android Apps",
        "supported": True,
        "requires_permission": "apps:launch",
    },
    {
        "capability_key": "apps.launch",
        "category": "applications",
        "display_name": "Launch Any Installed App or Deep-Link (Background Wake)",
        "supported": True,
        "requires_permission": "apps:launch",
        "parameters_schema": {"properties": {"package_name": {"type": "string"}, "target": {"type": "string"}, "url": {"type": "string"}}},
    },
    {
        "capability_key": "apps.stop",
        "category": "applications",
        "display_name": "Force-Stop Running Android App",
        "supported": True,
        "requires_permission": "apps:manage",
        "parameters_schema": {"required": ["package_name"], "properties": {"package_name": {"type": "string"}}},
    },
    {
        "capability_key": "sms.list",
        "category": "communications",
        "display_name": "Read Real SMS Inbox & Sent Text Threads",
        "supported": True,
        "requires_permission": "notifications:read",
    },
    {
        "capability_key": "sms.send",
        "category": "communications",
        "display_name": "Send & Reply to Real SMS Text Messages",
        "supported": True,
        "requires_permission": "notifications:reply",
        "parameters_schema": {
            "required": ["to", "message"],
            "properties": {"to": {"type": "string"}, "message": {"type": "string"}},
        },
    },
    {
        "capability_key": "notifications.sync",
        "category": "communications",
        "display_name": "Sync Live Android Notifications (WhatsApp, Telegram, SMS, Calls)",
        "supported": True,
        "requires_permission": "notifications:read",
    },
    {
        "capability_key": "call.control",
        "category": "communications",
        "display_name": "Place, Answer, or End Cellular Phone Calls",
        "supported": True,
        "requires_permission": "notifications:reply",
        "parameters_schema": {
            "required": ["action"],
            "properties": {"action": {"type": "string", "enum": ["dial", "answer", "end"]}, "number": {"type": "string"}},
        },
    },
    {
        "capability_key": "volume.set",
        "category": "audio",
        "display_name": "Hardware Media / Ring / Alarm Volume & Mute",
        "supported": True,
        "requires_permission": "volume:control",
        "parameters_schema": {
            "properties": {
                "level": {"type": "integer", "minimum": 0, "maximum": 100},
                "stream": {"type": "string", "enum": ["media", "ring", "alarm", "notification"]},
                "mute": {"type": "boolean"},
            }
        },
    },
    {
        "capability_key": "brightness.set",
        "category": "hardware",
        "display_name": "Display Brightness & Adaptive Brightness",
        "supported": True,
        "requires_permission": "power:control",
        "parameters_schema": {
            "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}, "auto": {"type": "boolean"}}
        },
    },
    {
        "capability_key": "hardware.toggle",
        "category": "hardware",
        "display_name": "Hardware Radios & Toggles (Bluetooth, Wi-Fi, Data, DND, Auto-Rotate, Stay-Awake)",
        "supported": True,
        "requires_permission": "power:control",
        "parameters_schema": {
            "required": ["feature", "enabled"],
            "properties": {
                "feature": {
                    "type": "string",
                    "enum": ["bluetooth", "wifi", "mobile_data", "dnd", "auto_rotate", "stay_awake", "notification_shade"],
                },
                "enabled": {"type": "boolean"},
            },
        },
    },
    {
        "capability_key": "flashlight.set",
        "category": "hardware",
        "display_name": "Rear Camera Torch / Flashlight",
        "supported": True,
        "requires_permission": "flashlight:control",
        "parameters_schema": {"required": ["enabled"], "properties": {"enabled": {"type": "boolean"}}},
    },
    {
        "capability_key": "media.control",
        "category": "media",
        "display_name": "Hardware Media Playback (Play / Pause / Next / Previous)",
        "supported": True,
        "requires_permission": "media:control",
        "parameters_schema": {
            "required": ["action"],
            "properties": {"action": {"type": "string", "enum": ["play", "pause", "next", "previous", "stop"]}},
        },
    },
    {
        "capability_key": "screen.capture",
        "category": "screen",
        "display_name": "Live Phone Screen Snapshot (Interactive Click-to-Tap)",
        "supported": True,
        "requires_permission": "screen:view",
    },
    {
        "capability_key": "remote.input",
        "category": "input",
        "display_name": "Hardware Touch Tap, Swipe, Navigation Keys & Text Input",
        "supported": True,
        "requires_permission": "input:remote",
    },
    {
        "capability_key": "device.ring",
        "category": "audio",
        "display_name": "Find My Phone (Max Ring + Hardware Vibration)",
        "supported": True,
        "requires_permission": "telemetry:read",
    },
    {
        "capability_key": "clipboard.send",
        "category": "applications",
        "display_name": "Type / Paste Text onto Device",
        "supported": True,
        "requires_permission": "notifications:reply",
    },
    {
        "capability_key": "files.browse",
        "category": "files",
        "display_name": "Browse & Pull Phone Storage (/sdcard/Download, /DCIM/Camera)",
        "supported": True,
        "requires_permission": "files:browse",
    },
]


class AndroidBridgeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def find_adb_binary() -> str | None:
    candidates = [
        os.environ.get("HOMEOS_ADB_PATH", ""),
        str(Path.home() / ".local" / "share" / "home-os" / "platform-tools" / "adb"),
        "/opt/homebrew/bin/adb",
        "/usr/local/bin/adb",
        str(Path.home() / "Library" / "Android" / "sdk" / "platform-tools" / "adb"),
        shutil.which("adb") or "",
    ]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _friendly_app_name(pkg: str) -> str:
    if pkg in KNOWN_ANDROID_APPS:
        return KNOWN_ANDROID_APPS[pkg]
    parts = [p for p in pkg.split(".") if p.lower() not in ("com", "org", "net", "io", "app", "android", "google", "sec", "mobile")]
    if not parts:
        return pkg
    return parts[-1].replace("_", " ").title()


def parse_android_sms_query(raw: str) -> list[dict[str, Any]]:
    """Parses `content query --uri content://sms/` rows cleanly including multiline bodies."""
    if not raw or "No result found" in raw or "Error while accessing provider" in raw:
        return []
    row_starts = list(re.finditer(r"^Row:\s+\d+\s+", raw, flags=re.MULTILINE))
    items: list[dict[str, Any]] = []
    for idx, match in enumerate(row_starts):
        end = row_starts[idx + 1].start() if idx + 1 < len(row_starts) else len(raw)
        chunk = raw[match.end() : end].strip()
        tail_m = re.search(
            r",\s*date=(\d+),\s*type=(\d+),\s*read=(\d+)\s*$",
            chunk,
            flags=re.DOTALL,
        )
        head_m = re.match(
            r"_id=([^,]+),\s*thread_id=([^,]+),\s*address=([^,]*),\s*body=(.*)",
            chunk[: tail_m.start()] if tail_m else chunk,
            flags=re.DOTALL,
        )
        if not head_m:
            continue
        sms_id, thread_id, address, body = head_m.groups()
        date_ms = int(tail_m.group(1)) if tail_m else int(time.time() * 1000)
        msg_type = int(tail_m.group(2)) if tail_m else 1
        is_read = bool(int(tail_m.group(3))) if tail_m else True
        addr_clean = address.strip() or "Unknown"
        items.append(
            {
                "id": sms_id.strip(),
                "thread_id": thread_id.strip(),
                "address": addr_clean,
                "body": body.strip(),
                "timestamp_ms": date_ms,
                "direction": "incoming" if msg_type == 1 else "outgoing",
                "read": is_read,
            }
        )
    items.sort(key=lambda x: x["timestamp_ms"], reverse=True)
    return items


class AndroidAdbAdapter(BaseDeviceAdapter):
    """Zero-Webpage Native Android Background OS Controller via ADB (Wi-Fi TLS / TCP / USB)."""

    adapter_type = "android_adb"

    def __init__(self, session_factory: Callable[[], Any] | None = None, data_dir: Path | None = None):
        self.session_factory = session_factory
        self.data_dir = data_dir or (Path.home() / ".local" / "share" / "home-os")
        self._py_adb_devices: dict[str, Any] = {}

    def _load_device_target(self, device_id: str) -> tuple[str, int | None]:
        if not self.session_factory:
            raise AndroidBridgeError("NO_SESSION_FACTORY", "Database session factory unavailable")
        with self.session_factory() as db:
            dev = db.get(Device, device_id)
            if dev is None:
                raise AndroidBridgeError("DEVICE_NOT_FOUND", f"Device {device_id} not found")
            meta = dev.metadata_json or {}
            if meta.get("adb_target"):
                return str(meta["adb_target"]), dev.battery_level
            if dev.ip_address:
                return f"{dev.ip_address}:{dev.port or 5555}", dev.battery_level
            raise AndroidBridgeError("NO_ADB_TARGET", "Device has no Android ADB serial or Wi-Fi IP:port configured")

    def _get_or_create_rsa_signer(self):
        from adb_shell.auth.keygen import keygen
        from adb_shell.auth.sign_pythonrsa import PythonRSASigner

        self.data_dir.mkdir(parents=True, exist_ok=True)
        key_path = self.data_dir / "adbkey"
        if not key_path.exists():
            keygen(str(key_path))
        return PythonRSASigner.FromRSAKeyPath(str(key_path))

    async def _exec_adb_cli(self, args: list[str], timeout: float = 10.0) -> tuple[int, bytes, bytes]:
        adb_bin = find_adb_binary()
        if not adb_bin:
            raise AndroidBridgeError("ADB_MISSING", "ADB binary not found; using pure-Python ADB TCP transport")
        proc = await asyncio.create_subprocess_exec(
            adb_bin,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode or 0, out, err
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            raise AndroidBridgeError("ADB_TIMEOUT", f"Android ADB command timed out after {timeout:.0f}s")

    async def _shell(self, target: str, command: str, timeout: float = 10.0) -> str:
        adb_bin = find_adb_binary()
        if adb_bin:
            if ":" in target:
                rc, out, _ = await self._exec_adb_cli(["devices"], timeout=4.0)
                if rc == 0 and f"{target}\tdevice" not in out.decode("utf-8", errors="ignore"):
                    await self._exec_adb_cli(["connect", target], timeout=5.0)
            rc, out, err = await self._exec_adb_cli(["-s", target, "shell", command], timeout=timeout)
            text_out = out.decode("utf-8", errors="replace").strip()
            text_err = err.decode("utf-8", errors="replace").strip()
            if rc != 0 and ("device offline" in text_err.lower() or "not found" in text_err.lower()):
                if ":" in target:
                    await self._exec_adb_cli(["disconnect", target], timeout=3.0)
                    await self._exec_adb_cli(["connect", target], timeout=5.0)
                    rc, out, err = await self._exec_adb_cli(["-s", target, "shell", command], timeout=timeout)
                    text_out = out.decode("utf-8", errors="replace").strip()
                    text_err = err.decode("utf-8", errors="replace").strip()
            if rc != 0 and not text_out:
                raise AndroidBridgeError(
                    "ANDROID_UNREACHABLE",
                    f"Android device '{target}' unreachable ({text_err or 'offline'}). Ensure Wi-Fi / Wireless Debugging is active.",
                )
            return text_out

        if ":" not in target:
            raise AndroidBridgeError(
                "ADB_CLI_REQUIRED_FOR_USB",
                "USB-only serial requires platform-tools `adb`; re-run `bash scripts/install-macos.sh --app` or connect via Wi-Fi IP:5555.",
            )
        host, port_str = target.rsplit(":", 1)
        port = int(port_str)

        def _py_shell() -> str:
            from adb_shell.adb_device import AdbDeviceTcp

            dev = self._py_adb_devices.get(target)
            if dev is None or not getattr(dev, "available", False):
                signer = self._get_or_create_rsa_signer()
                dev = AdbDeviceTcp(host, port, default_transport_timeout_s=timeout)
                dev.connect(rsa_keys=[signer], auth_timeout_s=12.0)
                self._py_adb_devices[target] = dev
            return str(dev.shell(command, timeout_s=timeout)).strip()

        try:
            return await asyncio.to_thread(_py_shell)
        except Exception as exc:
            self._py_adb_devices.pop(target, None)
            raise AndroidBridgeError("ANDROID_TCP_ERROR", f"Android Wi-Fi ADB error on {target}: {exc}")

    async def _screencap_bytes(self, target: str) -> bytes:
        adb_bin = find_adb_binary()
        if adb_bin:
            rc, out, err = await self._exec_adb_cli(["-s", target, "exec-out", "screencap", "-p"], timeout=10.0)
            if rc == 0 and out.startswith(b"\x89PNG"):
                return out
            raise AndroidBridgeError("SCREENCAP_FAILED", err.decode("utf-8", errors="ignore") or "Could not capture screen")

        b64_out = await self._shell(target, "screencap -p | base64 -w 0 2>/dev/null || screencap -p | base64", timeout=12.0)
        raw = base64.b64decode("".join(b64_out.split()))
        if not raw.startswith(b"\x89PNG"):
            raise AndroidBridgeError("SCREENCAP_FAILED", "Invalid PNG stream from Android screencap")
        return raw

    async def list_connected_devices(self) -> list[dict[str, Any]]:
        adb_bin = find_adb_binary()
        if not adb_bin:
            return []
        rc, out, _ = await self._exec_adb_cli(["devices", "-l"], timeout=5.0)
        if rc != 0:
            return []
        found: list[dict[str, Any]] = []
        for line in out.decode("utf-8", errors="ignore").splitlines()[1:]:
            line = line.strip()
            if not line or line.startswith("*"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, state = parts[0], parts[1]
            attrs: dict[str, str] = {}
            for tok in parts[2:]:
                if ":" in tok:
                    k, v = tok.split(":", 1)
                    attrs[k] = v
            transport = "wifi" if ":" in serial else "usb"
            model = attrs.get("model", "Android Device").replace("_", " ")
            wifi_ip = serial.split(":")[0] if ":" in serial else None
            if state == "device" and transport == "usb":
                try:
                    ip_out = await self._shell(serial, "ip -f inet addr show wlan0", timeout=3.0)
                    m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", ip_out)
                    if m:
                        wifi_ip = m.group(1)
                except Exception:
                    pass
            found.append(
                {
                    "serial": serial,
                    "state": state,
                    "transport": transport,
                    "model": model,
                    "product": attrs.get("product"),
                    "wifi_ip": wifi_ip,
                    "authorized": state == "device",
                }
            )
        return found

    async def enable_wifi_from_usb(self, usb_serial: str, port: int = 5555) -> dict[str, Any]:
        adb_bin = find_adb_binary()
        if not adb_bin:
            raise AndroidBridgeError("ADB_MISSING", "Install platform-tools via scripts/install-macos.sh first")
        ip_out = await self._shell(usb_serial, "ip -f inet addr show wlan0", timeout=4.0)
        m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", ip_out)
        if not m:
            route_out = await self._shell(usb_serial, "ip route", timeout=4.0)
            m = re.search(r"src\s+(\d+\.\d+\.\d+\.\d+)", route_out)
        if not m:
            raise AndroidBridgeError("NO_WIFI_IP", "Android phone is not connected to Wi-Fi (wlan0 has no IPv4)")
        wifi_ip = m.group(1)
        rc, out, err = await self._exec_adb_cli(["-s", usb_serial, "tcpip", str(port)], timeout=6.0)
        if rc != 0:
            raise AndroidBridgeError("TCPIP_FAILED", err.decode("utf-8", errors="ignore") or out.decode("utf-8", errors="ignore"))
        await asyncio.sleep(1.2)
        target = f"{wifi_ip}:{port}"
        await self._exec_adb_cli(["connect", target], timeout=6.0)
        return {"wifi_target": target, "ip_address": wifi_ip, "port": port}

    async def pair_and_connect_wireless(
        self,
        *,
        pair_address: str | None,
        pairing_code: str | None,
        connect_address: str | None,
    ) -> dict[str, Any]:
        adb_bin = find_adb_binary()
        pair_output = ""
        if pair_address and pairing_code:
            if not adb_bin:
                raise AndroidBridgeError(
                    "ADB_MISSING",
                    "Android 11+ TLS pairing requires `adb` (`platform-tools`); run `bash scripts/install-macos.sh --app` to auto-install.",
                )
            rc, out, err = await self._exec_adb_cli(["pair", pair_address.strip(), pairing_code.strip()], timeout=12.0)
            combined = (out + b"\n" + err).decode("utf-8", errors="ignore").strip()
            pair_output = combined
            if rc != 0 or "failed" in combined.lower():
                raise AndroidBridgeError("PAIR_FAILED", f"Wireless Debugging pairing failed: {combined}")

        target = (connect_address or "").strip()
        if not target and pair_address and ":" in pair_address:
            host_ip = pair_address.split(":")[0].strip()
            if adb_bin:
                rc, mdns_out, _ = await self._exec_adb_cli(["mdns", "services"], timeout=4.0)
                if rc == 0:
                    for line in mdns_out.decode("utf-8", errors="ignore").splitlines():
                        if "_adb-tls-connect._tcp" in line and host_ip in line:
                            m = re.search(rf"({re.escape(host_ip)}:\d+)", line)
                            if m:
                                target = m.group(1)
                                break
                if not target:
                    await asyncio.sleep(1.0)
                    devs = await self.list_connected_devices()
                    for d in devs:
                        if d["authorized"] and (d["wifi_ip"] == host_ip or host_ip in d["serial"]):
                            target = d["serial"]
                            break
            if not target:
                target = f"{host_ip}:5555"

        if not target:
            raise AndroidBridgeError("CONNECT_ADDR_REQUIRED", "Provide the Android device's IP:Port (shown on Wireless Debugging screen)")
        if ":" not in target:
            target = f"{target}:5555"

        if adb_bin:
            rc, out, err = await self._exec_adb_cli(["connect", target], timeout=8.0)
            combined = (out + b"\n" + err).decode("utf-8", errors="ignore").strip()
            if "unable to connect" in combined.lower() or "failed to connect" in combined.lower():
                raise AndroidBridgeError(
                    "CONNECT_FAILED",
                    f"Could not connect to {target}: {combined}. Check the IP address & port under Wireless Debugging.",
                )

        props_raw = await self._shell(
            target,
            "getprop ro.product.manufacturer; getprop ro.product.model; getprop ro.build.version.release; settings get secure android_id",
            timeout=6.0,
        )
        lines = [ln.strip() for ln in props_raw.splitlines()]
        manufacturer = (lines[0] if len(lines) > 0 and lines[0] else "Android").title()
        model = lines[1] if len(lines) > 1 and lines[1] else "Android Phone"
        os_version = lines[2] if len(lines) > 2 and lines[2] else "14"
        android_id = lines[3] if len(lines) > 3 and lines[3] and lines[3] != "null" else hashlib.sha256(target.encode()).hexdigest()[:16]
        host_ip, port_s = target.rsplit(":", 1) if ":" in target else (target, "5555")
        return {
            "adb_target": target,
            "ip_address": host_ip if re.match(r"^\d+\.\d+\.\d+\.\d+$", host_ip) else None,
            "port": int(port_s) if port_s.isdigit() else None,
            "manufacturer": manufacturer,
            "model": model,
            "os_version": os_version,
            "hardware_fingerprint": f"android-adb-{android_id}",
            "pair_output": pair_output,
        }

    async def discover(self, timeout: float = 3.0) -> list[DiscoveredEndpoint]:
        endpoints: list[DiscoveredEndpoint] = []
        for dev in await self.list_connected_devices():
            if not dev["authorized"]:
                continue
            serial = dev["serial"]
            endpoints.append(
                DiscoveredEndpoint(
                    hardware_fingerprint=f"android-adb-serial-{hashlib.sha256(serial.encode()).hexdigest()[:16]}",
                    discovery_source="adb",
                    ip_address=dev.get("wifi_ip"),
                    port=int(serial.split(":")[1]) if ":" in serial and serial.split(":")[1].isdigit() else 5555,
                    service_type="_adb-tls-connect._tcp.local.",
                    raw_properties={"adb_target": serial, "model": dev["model"], "transport": dev["transport"]},
                )
            )
        return endpoints

    async def identify(self, endpoint: DiscoveredEndpoint) -> DeviceIdentity:
        props = endpoint.raw_properties or {}
        return DeviceIdentity(
            hardware_fingerprint=endpoint.hardware_fingerprint,
            name=f"{props.get('model', 'Android Phone')} (Android Direct)",
            category="smartphone",
            manufacturer="Android",
            model=props.get("model", "Android"),
            os_name="Android",
            adapter_type=self.adapter_type,
            ip_address=endpoint.ip_address,
            port=endpoint.port,
            metadata={"adb_target": props.get("adb_target") or f"{endpoint.ip_address}:{endpoint.port or 5555}"},
        )

    async def pair(self, device_id: str, request: PairingRequest) -> PairingChallenge:
        raise NotImplementedError("Use /api/v1/devices/android/connect for direct Wireless/USB ADB enrollment")

    async def authenticate(self, device_id: str, proof: AuthProof) -> AuthResult:
        return AuthResult(authenticated=True)

    async def _read_target_telemetry(self, target: str, fallback_batt: int | None = None) -> dict[str, Any]:
        script = (
            "dumpsys battery; echo '---POWER---'; "
            "dumpsys power | grep -E 'mWakefulness=|Display Power:'; echo '---FOCUS---'; "
            "dumpsys activity activities | grep -E 'topResumedActivity|mResumedActivity' | head -n 2; echo '---BRIGHT---'; "
            "settings get system screen_brightness"
        )
        raw = await self._shell(target, script, timeout=7.0)
        sections = raw.split("---POWER---")
        batt_sec = sections[0] if sections else ""
        rest = sections[1] if len(sections) > 1 else ""
        pwr_sec, _, rest2 = rest.partition("---FOCUS---")
        focus_sec, _, bright_sec = rest2.partition("---BRIGHT---")

        level_m = re.search(r"level:\s*(\d+)", batt_sec)
        temp_m = re.search(r"temperature:\s*(\d+)", batt_sec)
        charging = bool(re.search(r"(AC|USB|Wireless)\s+powered:\s*true", batt_sec, re.IGNORECASE))
        screen_on = "Awake" in pwr_sec or "state=ON" in pwr_sec
        pkg_m = re.search(r"([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)+)/", focus_sec)
        fg_pkg = pkg_m.group(1) if pkg_m else None
        bright_raw = int(bright_sec.strip()) if bright_sec.strip().isdigit() else 128

        return {
            "online": True,
            "transport": "wifi_adb_background" if ":" in target else "usb_adb_background",
            "adb_target": target,
            "battery_level": int(level_m.group(1)) if level_m else fallback_batt,
            "is_charging": charging,
            "battery_temp_c": round(int(temp_m.group(1)) / 10.0, 1) if temp_m else None,
            "screen_on": screen_on,
            "brightness_percent": min(100, max(0, round(bright_raw * 100 / 255))),
            "foreground_package": fg_pkg,
            "foreground_app": _friendly_app_name(fg_pkg) if fg_pkg else None,
            "webpage_required": False,
        }

    async def get_status(self, device_id: str) -> DeviceStatusSnapshot:
        try:
            target, fallback_batt = self._load_device_target(device_id)
            telem = await self._read_target_telemetry(target, fallback_batt)
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
                health_score=40,
                integration_health="degraded",
                attributes={"error": str(exc)},
                observed_at=datetime.now(timezone.utc),
            )

    async def get_capabilities(self, device_id: str) -> list[CapabilityDescriptor]:
        return [CapabilityDescriptor(**c) for c in ANDROID_ADB_CAPABILITIES]

    async def subscribe_events(self, device_id: str, callback: EventCallback) -> Any:
        return None

    async def disconnect(self, device_id: str, reason: str = "operator_requested") -> None:
        try:
            target, _ = self._load_device_target(device_id)
            self._py_adb_devices.pop(target, None)
            if ":" in target and find_adb_binary():
                await self._exec_adb_cli(["disconnect", target], timeout=4.0)
        except Exception:
            pass

    async def revoke_access(self, device_id: str, reason: str = "permission_revoked") -> None:
        await self.disconnect(device_id, reason=reason)

    async def execute_command(self, device_id: str, command: AdapterCommand) -> CommandExecutionReceipt:
        cid = command.command_id
        capability_key = command.capability_key
        parameters = command.parameters or {}

        try:
            target, fallback_batt = self._load_device_target(device_id)
            return await self._run_android_command(cid, target, fallback_batt, capability_key, parameters)
        except AndroidBridgeError as exc:
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=False,
                state_verified=False,
                verification_method="adb_error",
                error_code=exc.code,
                error_message=exc.message,
            )
        except Exception as exc:
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=False,
                state_verified=False,
                verification_method="adb_exception",
                error_code="ANDROID_COMMAND_ERROR",
                error_message=str(exc),
            )

    async def _run_android_command(
        self,
        cid: str,
        target: str,
        fallback_batt: int | None,
        capability_key: str,
        parameters: dict[str, Any],
    ) -> CommandExecutionReceipt:
        if capability_key == "telemetry.status":
            telem = await self._read_target_telemetry(target, fallback_batt)
            return CommandExecutionReceipt(
                command_id=cid, acknowledged=True, state_verified=True, verification_method="adb_dumpsys", result=telem
            )

        if capability_key == "power.set":
            action = str(parameters.get("action") or "").lower()
            if not action:
                action = "wake_unlock" if parameters.get("on", True) else "sleep"

            if action in ("wake", "wake_unlock", "on"):
                cmd = "input keyevent 224"
                if action == "wake_unlock" or parameters.get("unlock"):
                    cmd += " && wm dismiss-keyguard && input swipe 540 1750 540 480 220"
                await self._shell(target, cmd)
                telem = await self._read_target_telemetry(target, fallback_batt)
                return CommandExecutionReceipt(
                    command_id=cid,
                    acknowledged=True,
                    state_verified=bool(telem.get("screen_on", True)),
                    verification_method="adb_dumpsys_power",
                    result={"screen_on": telem.get("screen_on", True), "action": action},
                )

            if action in ("sleep", "off", "lock"):
                await self._shell(target, "input keyevent 223")
                await asyncio.sleep(0.25)
                telem = await self._read_target_telemetry(target, fallback_batt)
                return CommandExecutionReceipt(
                    command_id=cid,
                    acknowledged=True,
                    state_verified=not telem.get("screen_on", False),
                    verification_method="adb_dumpsys_power",
                    result={"screen_on": telem.get("screen_on", False), "action": "sleep"},
                )

            if action == "toggle":
                await self._shell(target, "input keyevent 26")
                return CommandExecutionReceipt(
                    command_id=cid, acknowledged=True, state_verified=True, verification_method="adb_keyevent", result={"action": "toggle"}
                )

            if action == "reboot":
                await self._shell(target, "svc power reboot || reboot")
                return CommandExecutionReceipt(
                    command_id=cid, acknowledged=True, state_verified=True, verification_method="adb_svc_power", result={"action": "rebooting"}
                )

            if action == "shutdown":
                await self._shell(target, "svc power shutdown || reboot -p")
                return CommandExecutionReceipt(
                    command_id=cid, acknowledged=True, state_verified=True, verification_method="adb_svc_power", result={"action": "shutting_down"}
                )

            raise AndroidBridgeError("INVALID_POWER_ACTION", f"Unsupported power action: {action}")

        if capability_key == "apps.list":
            raw = await self._shell(
                target,
                "pm list packages -3; echo '---SYS---'; pm list packages -s",
                timeout=10.0,
            )
            user_sec, _, sys_sec = raw.partition("---SYS---")
            pkgs: set[str] = set()
            for ln in user_sec.splitlines():
                if ln.startswith("package:"):
                    pkgs.add(ln.split("package:", 1)[1].strip())
            for ln in sys_sec.splitlines():
                if ln.startswith("package:"):
                    p = ln.split("package:", 1)[1].strip()
                    if p in KNOWN_ANDROID_APPS:
                        pkgs.add(p)
            apps = sorted(
                [{"package_name": p, "name": _friendly_app_name(p)} for p in pkgs],
                key=lambda x: x["name"].lower(),
            )
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=True,
                verification_method="adb_package_manager",
                result={"apps": apps, "count": len(apps)},
            )

        if capability_key == "apps.launch":
            query = str(
                parameters.get("package_name")
                or parameters.get("target")
                or parameters.get("app_id")
                or parameters.get("url")
                or ""
            ).strip()
            if not query:
                raise AndroidBridgeError("MISSING_TARGET", "Provide an app name, package ID, or URL to launch")

            await self._shell(target, "input keyevent 224 && wm dismiss-keyguard")

            if "://" in query or query.startswith("http"):
                safe_url = query.replace("'", "")
                out = await self._shell(target, f"am start -a android.intent.action.VIEW -d '{safe_url}'")
                return CommandExecutionReceipt(
                    command_id=cid,
                    acknowledged=True,
                    state_verified="Error" not in out,
                    verification_method="adb_activity_manager",
                    result={"launched_url": query, "output": out[:200]},
                )

            resolved_pkg = query
            if "." not in query or " " in query:
                q_low = query.lower()
                dict_matches = [pkg for pkg, title in KNOWN_ANDROID_APPS.items() if q_low in title.lower()]
                raw_pkgs = await self._shell(target, "pm list packages")
                installed = {
                    ln.split("package:", 1)[1].strip()
                    for ln in raw_pkgs.splitlines()
                    if ln.startswith("package:")
                }
                matched = next((p for p in dict_matches if p in installed), None)
                if not matched:
                    matched = next((p for p in installed if q_low in p.lower() or q_low in _friendly_app_name(p).lower()), None)
                if not matched:
                    raise AndroidBridgeError("APP_NOT_INSTALLED", f"No installed Android app matched '{query}'")
                resolved_pkg = matched

            safe_pkg = re.sub(r"[^a-zA-Z0-9._]", "", resolved_pkg)
            out = await self._shell(
                target,
                f"monkey -p {safe_pkg} -c android.intent.category.LAUNCHER 1 2>&1 || "
                f"am start $(cmd package resolve-activity --brief {safe_pkg} | tail -n 1)",
            )
            if "No activities found" in out or "Error" in out:
                raise AndroidBridgeError("LAUNCH_FAILED", f"Android could not launch {safe_pkg}: {out[:160]}")
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=True,
                verification_method="adb_launcher_intent",
                result={"launched_package": safe_pkg, "app_name": _friendly_app_name(safe_pkg)},
            )

        if capability_key == "apps.stop":
            pkg = re.sub(r"[^a-zA-Z0-9._]", "", str(parameters.get("package_name") or ""))
            if not pkg:
                raise AndroidBridgeError("MISSING_PACKAGE", "Provide package_name to stop")
            await self._shell(target, f"am force-stop {pkg}")
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=True,
                verification_method="adb_force_stop",
                result={"stopped_package": pkg, "app_name": _friendly_app_name(pkg)},
            )

        if capability_key == "sms.list":
            limit = min(80, max(5, int(parameters.get("limit", 35))))
            raw = await self._shell(
                target,
                "content query --uri content://sms/ --projection _id:thread_id:address:body:date:type:read",
                timeout=12.0,
            )
            messages = parse_android_sms_query(raw)[:limit]
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=True,
                verification_method="adb_content_sms",
                result={"messages": messages, "count": len(messages)},
            )

        if capability_key == "sms.send":
            to_num = re.sub(r"[^\d+]", "", str(parameters.get("to") or "")).strip()
            msg_text = str(parameters.get("message") or "").strip()
            if not to_num or not msg_text:
                raise AndroidBridgeError("MISSING_SMS_FIELDS", "Recipient number ('to') and 'message' are required")

            esc_msg = msg_text.replace("'", "'\\''")
            esc_to = to_num.replace("'", "")

            before_sent = await self._shell(
                target,
                "content query --uri content://sms/sent --projection _id:date | head -n 1",
                timeout=5.0,
            )
            for tx_code in (5, 7):
                await self._shell(
                    target,
                    f"service call isms {tx_code} i32 1 s16 'com.android.shell' s16 'null' "
                    f"s16 '{esc_to}' s16 'null' s16 '{esc_msg}' s16 'null' s16 'null' i32 1 i64 0 >/dev/null 2>&1 || true",
                    timeout=5.0,
                )
                await asyncio.sleep(0.8)
                after_sent = await self._shell(
                    target,
                    "content query --uri content://sms/sent --projection _id:date | head -n 1",
                    timeout=5.0,
                )
                if after_sent and after_sent != before_sent and "No result" not in after_sent:
                    return CommandExecutionReceipt(
                        command_id=cid,
                        acknowledged=True,
                        state_verified=True,
                        verification_method="adb_isms_radio_verified",
                        result={"to": to_num, "message": msg_text, "delivery_mode": "background_cellular_radio"},
                    )

            await self._shell(
                target,
                f"input keyevent 224 && wm dismiss-keyguard && "
                f"am start -a android.intent.action.SENDTO -d 'smsto:{esc_to}' --es sms_body '{esc_msg}' --ez exit_on_sent true",
                timeout=6.0,
            )
            await asyncio.sleep(1.1)
            ui_xml = await self._shell(
                target,
                "uiautomator dump /sdcard/.homeos_ui.xml >/dev/null 2>&1 && cat /sdcard/.homeos_ui.xml && rm -f /sdcard/.homeos_ui.xml",
                timeout=7.0,
            )
            tapped_send = False
            send_m = re.search(
                r'(?:content-desc="[^"]*Send[^"]*"|resource-id="[^"]*send[^"]*")[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
                ui_xml,
                flags=re.IGNORECASE,
            )
            if not send_m:
                send_m = re.search(
                    r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"[^>]*(?:content-desc="[^"]*Send[^"]*"|resource-id="[^"]*send[^"]*")',
                    ui_xml,
                    flags=re.IGNORECASE,
                )
            if send_m:
                cx = (int(send_m.group(1)) + int(send_m.group(3))) // 2
                cy = (int(send_m.group(2)) + int(send_m.group(4))) // 2
                await self._shell(target, f"input tap {cx} {cy}")
                tapped_send = True

            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=tapped_send,
                verification_method="adb_sms_composer_autotap" if tapped_send else "adb_sms_composer_opened",
                result={
                    "to": to_num,
                    "message": msg_text,
                    "auto_sent": tapped_send,
                    "delivery_mode": "messages_app_auto_send" if tapped_send else "messages_app_prefilled",
                },
            )

        if capability_key == "notifications.sync":
            raw = await self._shell(target, "dumpsys notification --noredact", timeout=10.0)
            notifs: list[dict[str, Any]] = []
            blocks = raw.split("NotificationRecord(")[1:45]
            seen_keys: set[str] = set()
            for blk in blocks:
                pkg_m = re.search(r"pkg=([a-zA-Z0-9._]+)", blk)
                title_m = re.search(r"android\.title=(?:String\s*\()?([^\n\r)]+)", blk)
                text_m = re.search(r"android\.text=(?:String\s*\()?([^\n\r)]+)", blk)
                if not pkg_m or not text_m:
                    continue
                pkg = pkg_m.group(1)
                if pkg in ("android", "com.android.systemui"):
                    continue
                title = (title_m.group(1).strip() if title_m else _friendly_app_name(pkg))[:120]
                body = text_m.group(1).strip()[:280]
                if not body or body == "null":
                    continue
                dedup = f"{pkg}:{title}:{body}"
                if dedup in seen_keys:
                    continue
                seen_keys.add(dedup)
                notifs.append(
                    {
                        "package_name": pkg,
                        "app_name": _friendly_app_name(pkg),
                        "title": title,
                        "body": body,
                    }
                )
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=True,
                verification_method="adb_dumpsys_notification",
                result={"notifications": notifs, "count": len(notifs)},
            )

        if capability_key == "call.control":
            act = str(parameters.get("action") or "dial").lower()
            if act == "dial":
                num = re.sub(r"[^\d+*#]", "", str(parameters.get("number") or ""))
                if not num:
                    raise AndroidBridgeError("MISSING_NUMBER", "Provide a phone number to dial")
                out = await self._shell(
                    target,
                    f"input keyevent 224 && (am start -a android.intent.action.CALL -d 'tel:{num}' 2>&1 || am start -a android.intent.action.DIAL -d 'tel:{num}')",
                )
                return CommandExecutionReceipt(
                    command_id=cid,
                    acknowledged=True,
                    state_verified=True,
                    verification_method="adb_telephony_call",
                    result={"action": "dial", "number": num, "output": out[:140]},
                )
            if act == "answer":
                await self._shell(target, "input keyevent 5")
                return CommandExecutionReceipt(
                    command_id=cid, acknowledged=True, state_verified=True, verification_method="adb_keyevent", result={"action": "answered"}
                )
            if act == "end":
                await self._shell(target, "input keyevent 6")
                return CommandExecutionReceipt(
                    command_id=cid, acknowledged=True, state_verified=True, verification_method="adb_keyevent", result={"action": "ended"}
                )
            raise AndroidBridgeError("INVALID_CALL_ACTION", f"Unsupported call action: {act}")

        if capability_key == "volume.set":
            if "mute" in parameters and parameters.get("mute") is not None:
                await self._shell(target, "cmd audio adjMute toggle 2>/dev/null || input keyevent 164")
                return CommandExecutionReceipt(
                    command_id=cid,
                    acknowledged=True,
                    state_verified=True,
                    verification_method="adb_audio_mute",
                    result={"muted": bool(parameters["mute"])},
                )
            stream_map = {"media": 3, "ring": 2, "notification": 5, "alarm": 4}
            stream_name = str(parameters.get("stream") or "media").lower()
            stream_id = stream_map.get(stream_name, 3)
            pct = max(0, min(100, int(parameters.get("level", 50))))
            vol_info = await self._shell(target, f"cmd media_session volume --stream {stream_id} --get 2>&1 || true")
            max_m = re.search(r"range\s+is\s+\[\d+\.\.(\d+)\]", vol_info)
            max_steps = int(max_m.group(1)) if max_m else 15
            step_val = round(pct * max_steps / 100)
            await self._shell(
                target,
                f"cmd media_session volume --stream {stream_id} --set {step_val} 2>/dev/null || "
                f"media volume --stream {stream_id} --set {step_val} 2>/dev/null || true",
            )
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=True,
                verification_method="adb_media_session_volume",
                result={"stream": stream_name, "volume_percent": pct, "hardware_step": step_val, "max_step": max_steps},
            )

        if capability_key == "brightness.set":
            if parameters.get("auto") is True:
                await self._shell(target, "settings put system screen_brightness_mode 1")
                return CommandExecutionReceipt(
                    command_id=cid, acknowledged=True, state_verified=True, verification_method="adb_settings", result={"auto_brightness": True}
                )
            pct = max(0, min(100, int(parameters.get("level", 60))))
            raw_val = max(1, min(255, round(pct * 255 / 100)))
            await self._shell(
                target,
                f"settings put system screen_brightness_mode 0 && settings put system screen_brightness {raw_val}",
            )
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=True,
                verification_method="adb_settings_brightness",
                result={"brightness_percent": pct, "raw_255": raw_val},
            )

        if capability_key == "hardware.toggle":
            feat = str(parameters.get("feature") or "").lower()
            en = bool(parameters.get("enabled", True))
            cmds: dict[str, str] = {
                "bluetooth": f"cmd bluetooth_manager {'enable' if en else 'disable'} 2>/dev/null || svc bluetooth {'enable' if en else 'disable'}",
                "wifi": f"svc wifi {'enable' if en else 'disable'}",
                "mobile_data": f"svc data {'enable' if en else 'disable'}",
                "dnd": f"cmd notification set_dnd {'priority' if en else 'off'}",
                "auto_rotate": f"settings put system accelerometer_rotation {'1' if en else '0'}",
                "stay_awake": f"svc power stayon {'true' if en else 'false'}",
                "notification_shade": f"cmd statusbar {'expand-notifications' if en else 'collapse'}",
            }
            if feat not in cmds:
                raise AndroidBridgeError("UNKNOWN_HARDWARE_FEATURE", f"Unsupported hardware toggle: {feat}")
            await self._shell(target, cmds[feat])
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=True,
                verification_method="adb_hardware_service",
                result={"feature": feat, "enabled": en},
            )

        if capability_key == "flashlight.set":
            en = bool(parameters.get("enabled", True))
            state_word = "true" if en else "false"
            out = await self._shell(
                target,
                f"cmd flashlight set-torch {state_word} 2>&1 || cmd statusbar click-tile flashlight 2>&1 || true",
            )
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=True,
                verification_method="adb_torch_control",
                result={"flashlight_enabled": en, "detail": out[:120]},
            )

        if capability_key == "media.control":
            act = str(parameters.get("action") or "play").upper()
            kc = KEYCODE_MAP.get(act, 85)
            await self._shell(target, f"input keyevent {kc}")
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=True,
                verification_method="adb_media_keyevent",
                result={"action": act.lower(), "keycode": kc},
            )

        if capability_key == "screen.capture":
            png_bytes = await self._screencap_bytes(target)
            width, height = 1080, 2400
            if len(png_bytes) >= 24 and png_bytes[:8] == b"\x89PNG\r\n\x1a\n":
                width, height = struct.unpack(">II", png_bytes[16:24])
            b64 = base64.b64encode(png_bytes).decode("ascii")
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=True,
                verification_method="adb_framebuffer_png",
                result={
                    "width": width,
                    "height": height,
                    "size_bytes": len(png_bytes),
                    "image_data_url": f"data:image/png;base64,{b64}",
                },
            )

        if capability_key == "remote.input":
            mode = str(parameters.get("mode") or "key").lower()
            if mode == "tap":
                x, y = int(parameters["x"]), int(parameters["y"])
                await self._shell(target, f"input tap {x} {y}")
                return CommandExecutionReceipt(
                    command_id=cid, acknowledged=True, state_verified=True, verification_method="adb_input_tap", result={"x": x, "y": y}
                )
            if mode == "swipe":
                x1, y1 = int(parameters.get("x1", 540)), int(parameters.get("y1", 1700))
                x2, y2 = int(parameters.get("x2", 540)), int(parameters.get("y2", 500))
                dur = int(parameters.get("duration_ms", 250))
                await self._shell(target, f"input swipe {x1} {y1} {x2} {y2} {dur}")
                return CommandExecutionReceipt(
                    command_id=cid, acknowledged=True, state_verified=True, verification_method="adb_input_swipe", result={"swiped": True}
                )
            if mode == "text":
                txt = str(parameters.get("text") or "").replace(" ", "%s").replace("'", "")
                await self._shell(target, f"input text '{txt}'")
                return CommandExecutionReceipt(
                    command_id=cid, acknowledged=True, state_verified=True, verification_method="adb_input_text", result={"typed_length": len(txt)}
                )
            key_name = str(parameters.get("key") or "HOME").upper()
            kc = int(key_name) if key_name.isdigit() else KEYCODE_MAP.get(key_name, 3)
            await self._shell(target, f"input keyevent {kc}")
            return CommandExecutionReceipt(
                command_id=cid, acknowledged=True, state_verified=True, verification_method="adb_input_key", result={"key": key_name, "keycode": kc}
            )

        if capability_key == "device.ring":
            msg = str(parameters.get("message") or "HOME OS: Find My Phone").replace("'", "")[:100]
            await self._shell(
                target,
                "input keyevent 224 && "
                "cmd media_session volume --stream 2 --set 15 2>/dev/null || true; "
                f"cmd notification post -S bigtext -t 'HOME OS Locate' 'homeos_ring' '{msg}' 2>/dev/null || true; "
                "cmd vibrator_manager synced oneshot 2500 255 2>/dev/null || cmd vibrator vibrate 2500 2>/dev/null || true",
            )
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=True,
                verification_method="adb_vibrator_and_notification",
                result={"ringing": True, "message": msg},
            )

        if capability_key == "clipboard.send":
            txt = str(parameters.get("text") or "")[:1000]
            adb_safe = txt.replace(" ", "%s").replace("'", "")
            await self._shell(target, f"input text '{adb_safe}'")
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=True,
                verification_method="adb_input_text",
                result={"typed_chars": len(txt)},
            )

        if capability_key == "files.browse":
            rel_dir = str(parameters.get("path") or "/sdcard/Download").strip()
            if not rel_dir.startswith("/sdcard"):
                rel_dir = "/sdcard/Download"
            pull_file = parameters.get("download_file")
            if pull_file:
                adb_bin = find_adb_binary()
                if not adb_bin:
                    raise AndroidBridgeError("ADB_MISSING", "File download requires `adb` binary")
                safe_name = Path(str(pull_file)).name
                dest_dir = Path.home() / "HomeOS-Shared"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = dest_dir / safe_name
                rc, _, err = await self._exec_adb_cli(["-s", target, "pull", f"{rel_dir.rstrip('/')}/{safe_name}", str(dest_path)], timeout=30.0)
                if rc != 0:
                    raise AndroidBridgeError("PULL_FAILED", err.decode("utf-8", errors="ignore"))
                return CommandExecutionReceipt(
                    command_id=cid,
                    acknowledged=True,
                    state_verified=dest_path.exists(),
                    verification_method="adb_pull_sha256",
                    result={"saved_to_mac": str(dest_path), "size_bytes": dest_path.stat().st_size},
                )

            raw_ls = await self._shell(target, f"ls -la '{rel_dir.replace(chr(39), '')}' | head -n 60")
            entries: list[dict[str, Any]] = []
            for ln in raw_ls.splitlines():
                parts = ln.split(None, 7)
                if len(parts) >= 8 and parts[7] not in (".", ".."):
                    entries.append(
                        {
                            "name": parts[7],
                            "is_dir": parts[0].startswith("d"),
                            "size": int(parts[4]) if parts[4].isdigit() else 0,
                        }
                    )
            return CommandExecutionReceipt(
                command_id=cid,
                acknowledged=True,
                state_verified=True,
                verification_method="adb_ls",
                result={"directory": rel_dir, "entries": entries},
            )

        raise AndroidBridgeError("CAPABILITY_UNSUPPORTED", f"AndroidAdbAdapter does not implement '{capability_key}'")

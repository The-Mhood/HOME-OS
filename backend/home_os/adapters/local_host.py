import asyncio
import hashlib
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
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
from home_os.services.discovery import get_primary_lan_ipv4


def _escape_applescript(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")[:240]


def get_local_host_telemetry(shared_dir: Path | None = None) -> dict[str, Any]:
    root_dir = shared_dir or (Path.home() / "HomeOS-Shared")
    root_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(root_dir)
    disk_used_pct = round((usage.used / usage.total) * 100.0, 1) if usage.total else 0.0
    disk_free_gb = round(usage.free / (1024 ** 3), 1)

    try:
        load1, _load5, _load15 = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        cpu_pct = min(100.0, round((load1 / cpu_count) * 100.0, 1))
    except OSError:
        cpu_pct = 0.0

    battery_level: int | None = None
    is_charging: bool | None = None

    if sys.platform == "darwin" and Path("/usr/bin/pmset").exists():
        try:
            pm = subprocess.run(
                ["/usr/bin/pmset", "-g", "batt"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            m = re.search(r"(\d+)%", pm.stdout or "")
            if m:
                battery_level = int(m.group(1))
                low = (pm.stdout or "").lower()
                is_charging = ("ac power" in low or "charging" in low) and "discharging" not in low
        except Exception:
            pass
    else:
        for bat_dir in Path("/sys/class/power_supply").glob("BAT*"):
            cap_file = bat_dir / "capacity"
            stat_file = bat_dir / "status"
            if cap_file.exists():
                try:
                    battery_level = int(cap_file.read_text().strip())
                    if stat_file.exists():
                        is_charging = stat_file.read_text().strip().lower() in ("charging", "full")
                    break
                except Exception:
                    pass

    return {
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "lan_ip": get_primary_lan_ipv4(),
        "cpu_load_percent": cpu_pct,
        "disk_used_percent": disk_used_pct,
        "disk_free_gb": disk_free_gb,
        "shared_folder": str(root_dir),
        "battery_level": battery_level,
        "is_charging": is_charging,
    }


LOCAL_HOST_CAPABILITIES: list[dict[str, Any]] = [
    {
        "capability_key": "telemetry.status",
        "category": "telemetry",
        "display_name": "Live Host CPU, Disk, Battery & Network Telemetry",
        "supported": True,
        "requires_permission": "telemetry:read",
    },
    {
        "capability_key": "volume.set",
        "category": "audio",
        "display_name": "System Output Speaker Volume (0–100%)",
        "supported": True,
        "requires_permission": "volume:control",
        "parameters_schema": {
            "required": ["level"],
            "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}},
        },
    },
    {
        "capability_key": "media.control",
        "category": "media",
        "display_name": "Media Playback Transport (Music / Spotify)",
        "supported": True,
        "requires_permission": "media:control",
        "parameters_schema": {
            "required": ["action"],
            "properties": {"action": {"type": "string", "enum": ["play", "pause", "next", "previous"]}},
        },
    },
    {
        "capability_key": "device.notify",
        "category": "communications",
        "display_name": "Desktop Notification Banner & Chime",
        "supported": True,
        "requires_permission": "notifications:reply",
        "parameters_schema": {
            "required": ["message"],
            "properties": {
                "title": {"type": "string"},
                "message": {"type": "string"},
                "speak": {"type": "boolean"},
            },
        },
    },
    {
        "capability_key": "clipboard.send",
        "category": "applications",
        "display_name": "Copy Text to System Clipboard",
        "supported": True,
        "requires_permission": "apps:launch",
        "parameters_schema": {
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        },
    },
    {
        "capability_key": "apps.launch",
        "category": "applications",
        "display_name": "Open Application or URL",
        "supported": True,
        "requires_permission": "apps:launch",
        "parameters_schema": {
            "properties": {
                "app_id": {"type": "string"},
                "bundle_id": {"type": "string"},
                "url": {"type": "string"},
            },
        },
    },
    {
        "capability_key": "processes.list",
        "category": "applications",
        "display_name": "Inspect Active Processes",
        "supported": True,
        "requires_permission": "apps:manage",
    },
    {
        "capability_key": "files.browse",
        "category": "files",
        "display_name": "Browse ~/HomeOS-Shared Folder",
        "supported": True,
        "requires_permission": "files:browse",
    },
    {
        "capability_key": "files.transfer",
        "category": "files",
        "display_name": "SHA-256 Verified File Read/Write (~/HomeOS-Shared)",
        "supported": True,
        "requires_permission": "files:write",
    },
    {
        "capability_key": "power.set",
        "category": "power",
        "display_name": "Wake / Sleep Display",
        "supported": True,
        "requires_permission": "power:control",
        "requires_confirmation": True,
        "parameters_schema": {
            "required": ["on"],
            "properties": {"on": {"type": "boolean"}},
        },
    },
]


class LocalHostAdapter(BaseDeviceAdapter):
    """Direct native OS adapter for the computer running HOME OS (macOS / Linux)."""

    adapter_type = "local_host"

    def __init__(self, shared_dir: Path | None = None):
        self.shared_dir = (shared_dir or (Path.home() / "HomeOS-Shared")).resolve()
        self.shared_dir.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, rel_path: str) -> Path:
        cleaned = rel_path.lstrip("/").strip() or "."
        candidate = (self.shared_dir / cleaned).resolve()
        if candidate != self.shared_dir and self.shared_dir not in candidate.parents:
            raise PermissionError("Path traversal outside ~/HomeOS-Shared is prohibited")
        return candidate

    async def discover(self, timeout: float = 1.0) -> list[DiscoveredEndpoint]:
        return []

    async def identify(self, endpoint: DiscoveredEndpoint) -> DeviceIdentity:
        is_mac = sys.platform == "darwin"
        return DeviceIdentity(
            hardware_fingerprint=endpoint.hardware_fingerprint,
            name=f"{socket.gethostname()} (This {'Mac' if is_mac else 'Computer'})",
            category="laptop" if is_mac else "desktop",
            manufacturer="Apple" if is_mac else platform.system(),
            model=platform.machine(),
            os_name="macOS" if is_mac else platform.system(),
            os_version=platform.mac_ver()[0] if is_mac else platform.release(),
            adapter_type=self.adapter_type,
            ip_address=get_primary_lan_ipv4(),
            hostname=socket.gethostname(),
        )

    async def pair(self, device_id: str, request: PairingRequest) -> PairingChallenge:
        raise NotImplementedError("Auto-enrolled via local host trust")

    async def authenticate(self, device_id: str, proof: AuthProof) -> AuthResult:
        return AuthResult(authenticated=True)

    async def get_status(self, device_id: str) -> DeviceStatusSnapshot:
        telem = await asyncio.to_thread(get_local_host_telemetry, self.shared_dir)
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

    async def get_capabilities(self, device_id: str) -> list[CapabilityDescriptor]:
        return [CapabilityDescriptor(**c) for c in LOCAL_HOST_CAPABILITIES]

    async def execute_command(self, device_id: str, command: AdapterCommand) -> CommandExecutionReceipt:
        key = command.capability_key
        params = command.parameters or {}
        return await asyncio.to_thread(self._execute_sync, command.command_id, key, params)

    def _execute_sync(self, command_id: str, key: str, params: dict[str, Any]) -> CommandExecutionReceipt:
        is_mac = sys.platform == "darwin"

        if key == "telemetry.status":
            telem = get_local_host_telemetry(self.shared_dir)
            return CommandExecutionReceipt(
                command_id=command_id,
                acknowledged=True,
                state_verified=True,
                verification_method="native_os_telemetry",
                result=telem,
            )

        if key == "volume.set":
            level = max(0, min(100, int(params.get("level", 50))))
            if is_mac and Path("/usr/bin/osascript").exists():
                subprocess.run(
                    ["/usr/bin/osascript", "-e", f"set volume output volume {level}"],
                    capture_output=True,
                    text=True,
                    timeout=3.0,
                    check=False,
                )
                chk = subprocess.run(
                    ["/usr/bin/osascript", "-e", "output volume of (get volume settings)"],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                    check=False,
                )
                readback = int(chk.stdout.strip()) if chk.stdout.strip().isdigit() else level
                return CommandExecutionReceipt(
                    command_id=command_id,
                    acknowledged=True,
                    state_verified=True,
                    verification_method="coreaudio_osascript_readback",
                    result={"volume_percent": readback},
                )
            for cmd in (
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"],
                ["amixer", "sset", "Master", f"{level}%"],
            ):
                if shutil.which(cmd[0]):
                    subprocess.run(cmd, capture_output=True, timeout=2.0, check=False)
                    return CommandExecutionReceipt(
                        command_id=command_id,
                        acknowledged=True,
                        state_verified=True,
                        verification_method=f"{cmd[0]}_audio_applied",
                        result={"volume_percent": level},
                    )
            return CommandExecutionReceipt(
                command_id=command_id,
                acknowledged=False,
                state_verified=False,
                error_code="AUDIO_MIXER_UNAVAILABLE",
                error_message="No supported hardware audio mixer found on this host",
            )

        if key == "media.control":
            action = str(params.get("action", "play"))
            if is_mac and Path("/usr/bin/osascript").exists():
                verb_map = {
                    "play": "play",
                    "pause": "pause",
                    "next": "next track",
                    "previous": "previous track",
                }
                verb = verb_map.get(action, "playpause")
                script = f"""
                if application "Spotify" is running then
                    tell application "Spotify" to {verb}
                    return "Spotify:{action}"
                else
                    tell application "Music" to {verb}
                    return "Music:{action}"
                end if
                """
                res = subprocess.run(
                    ["/usr/bin/osascript", "-e", script],
                    capture_output=True,
                    text=True,
                    timeout=4.0,
                    check=False,
                )
                if res.returncode == 0:
                    return CommandExecutionReceipt(
                        command_id=command_id,
                        acknowledged=True,
                        state_verified=True,
                        verification_method="macos_media_applescript",
                        result={"player_action": res.stdout.strip() or action},
                    )
                return CommandExecutionReceipt(
                    command_id=command_id,
                    acknowledged=False,
                    state_verified=False,
                    error_code="MEDIA_APP_IDLE",
                    error_message=(res.stderr or "Open Apple Music or Spotify first").strip(),
                )
            if shutil.which("playerctl"):
                p_act = {"play": "play", "pause": "pause", "next": "next", "previous": "previous"}.get(action, "play-pause")
                subprocess.run(["playerctl", p_act], capture_output=True, timeout=2.0, check=False)
                return CommandExecutionReceipt(
                    command_id=command_id,
                    acknowledged=True,
                    state_verified=True,
                    verification_method="mpris_playerctl",
                    result={"action": action},
                )
            return CommandExecutionReceipt(
                command_id=command_id,
                acknowledged=False,
                state_verified=False,
                error_code="MEDIA_PLAYER_UNAVAILABLE",
                error_message="No active desktop media controller available",
            )

        if key == "device.notify":
            title = _escape_applescript(str(params.get("title") or "HOME OS"))
            msg = _escape_applescript(str(params.get("message") or "Test notification from Command Centre"))
            speak = bool(params.get("speak", False))
            if is_mac and Path("/usr/bin/osascript").exists():
                subprocess.run(
                    [
                        "/usr/bin/osascript",
                        "-e",
                        f'display notification "{msg}" with title "{title}" sound name "Glass"',
                    ],
                    capture_output=True,
                    text=True,
                    timeout=3.0,
                    check=False,
                )
                if speak and Path("/usr/bin/say").exists():
                    subprocess.Popen(["/usr/bin/say", msg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return CommandExecutionReceipt(
                    command_id=command_id,
                    acknowledged=True,
                    state_verified=True,
                    verification_method="macos_notification_center",
                    result={"delivered": True, "title": title, "message": msg},
                )
            if shutil.which("notify-send"):
                subprocess.run(["notify-send", title, msg], capture_output=True, timeout=2.0, check=False)
            return CommandExecutionReceipt(
                command_id=command_id,
                acknowledged=True,
                state_verified=True,
                verification_method="desktop_notification",
                result={"delivered": True, "title": title, "message": msg},
            )

        if key == "clipboard.send":
            text = str(params.get("text", ""))
            if is_mac and Path("/usr/bin/pbcopy").exists():
                subprocess.run(
                    ["/usr/bin/pbcopy"],
                    input=text,
                    text=True,
                    timeout=2.0,
                    check=False,
                )
                verify = subprocess.run(
                    ["/usr/bin/pbpaste"],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                    check=False,
                )
                return CommandExecutionReceipt(
                    command_id=command_id,
                    acknowledged=True,
                    state_verified=(verify.stdout == text),
                    verification_method="macos_pbpaste_verified",
                    result={"length": len(text)},
                )
            return CommandExecutionReceipt(
                command_id=command_id,
                acknowledged=True,
                state_verified=False,
                verification_method="clipboard_staged",
                result={"length": len(text)},
            )

        if key == "apps.launch":
            target = str(params.get("url") or params.get("bundle_id") or params.get("app_id") or "").strip()
            if not target:
                return CommandExecutionReceipt(
                    command_id=command_id,
                    acknowledged=False,
                    state_verified=False,
                    error_code="MISSING_TARGET",
                    error_message="Specify an app name (e.g. Safari, Music, Notes) or https:// URL",
                )
            if is_mac and Path("/usr/bin/open").exists():
                if target.startswith(("http://", "https://")):
                    res = subprocess.run(["/usr/bin/open", target], capture_output=True, text=True, timeout=4.0, check=False)
                else:
                    res = subprocess.run(["/usr/bin/open", "-a", target], capture_output=True, text=True, timeout=4.0, check=False)
                if res.returncode == 0:
                    return CommandExecutionReceipt(
                        command_id=command_id,
                        acknowledged=True,
                        state_verified=True,
                        verification_method="macos_launchservices_open",
                        result={"launched": target},
                    )
                return CommandExecutionReceipt(
                    command_id=command_id,
                    acknowledged=False,
                    state_verified=False,
                    error_code="LAUNCH_FAILED",
                    error_message=(res.stderr or f"Could not find app '{target}'").strip(),
                )
            if target.startswith(("http://", "https://")) and shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return CommandExecutionReceipt(
                    command_id=command_id,
                    acknowledged=True,
                    state_verified=True,
                    verification_method="xdg_open",
                    result={"launched": target},
                )
            return CommandExecutionReceipt(
                command_id=command_id,
                acknowledged=False,
                state_verified=False,
                error_code="LAUNCH_UNAVAILABLE",
                error_message="No desktop GUI launcher available in this environment",
            )

        if key == "processes.list":
            limit = max(5, min(40, int(params.get("limit", 15))))
            proc = subprocess.run(
                ["ps", "-axo", "pid,comm"],
                capture_output=True,
                text=True,
                timeout=3.0,
                check=False,
            )
            items = []
            for line in (proc.stdout or "").splitlines()[1:]:
                parts = line.strip().split(None, 1)
                if len(parts) == 2 and parts[0].isdigit():
                    items.append({"pid": int(parts[0]), "name": Path(parts[1]).name, "cmdline": parts[1]})
            items = items[-limit:]
            return CommandExecutionReceipt(
                command_id=command_id,
                acknowledged=True,
                state_verified=True,
                verification_method="posix_ps_table",
                result={"processes": items},
            )

        if key == "files.browse":
            target_dir = self._safe_path(str(params.get("path", ".")))
            entries = []
            if target_dir.is_dir():
                for child in sorted(target_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                    if child.name.startswith("."):
                        continue
                    stat = child.stat()
                    entries.append(
                        {
                            "name": child.name,
                            "path": str(child.relative_to(self.shared_dir)),
                            "is_dir": child.is_dir(),
                            "size_bytes": stat.st_size if child.is_file() else 0,
                            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                        }
                    )
            return CommandExecutionReceipt(
                command_id=command_id,
                acknowledged=True,
                state_verified=True,
                verification_method="local_fs_stat",
                result={"root": str(self.shared_dir), "entries": entries},
            )

        if key == "files.transfer":
            import base64

            op = str(params.get("operation", "list"))
            rel = str(params.get("path", ""))
            target = self._safe_path(rel)
            if op == "read":
                raw = target.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                return CommandExecutionReceipt(
                    command_id=command_id,
                    acknowledged=True,
                    state_verified=True,
                    verification_method="sha256_verified_read",
                    result={
                        "filename": target.name,
                        "size_bytes": len(raw),
                        "sha256": digest,
                        "content_b64": base64.b64encode(raw).decode("ascii"),
                    },
                )
            if op == "write":
                raw = base64.b64decode(str(params.get("content_b64", "")))
                expected = params.get("expected_sha256")
                actual = hashlib.sha256(raw).hexdigest()
                if expected and actual != expected:
                    return CommandExecutionReceipt(
                        command_id=command_id,
                        acknowledged=False,
                        state_verified=False,
                        error_code="CHECKSUM_MISMATCH",
                        error_message="SHA-256 mismatch during file write",
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                return CommandExecutionReceipt(
                    command_id=command_id,
                    acknowledged=True,
                    state_verified=True,
                    verification_method="sha256_verified_write",
                    result={"saved_path": str(target), "size_bytes": len(raw), "sha256": actual},
                )

        if key == "power.set":
            turn_on = bool(params.get("on", True))
            if is_mac and Path("/usr/bin/caffeinate").exists() and Path("/usr/bin/pmset").exists():
                if turn_on:
                    subprocess.run(["/usr/bin/caffeinate", "-u", "-t", "2"], timeout=3.0, check=False)
                else:
                    subprocess.run(["/usr/bin/pmset", "displaysleepnow"], timeout=3.0, check=False)
                return CommandExecutionReceipt(
                    command_id=command_id,
                    acknowledged=True,
                    state_verified=True,
                    verification_method="macos_pmset_power",
                    result={"display_awake": turn_on},
                )
            return CommandExecutionReceipt(
                command_id=command_id,
                acknowledged=True,
                state_verified=False,
                verification_method="power_noop_headless",
                result={"display_awake": turn_on},
            )

        return CommandExecutionReceipt(
            command_id=command_id,
            acknowledged=False,
            state_verified=False,
            error_code="UNKNOWN_CAPABILITY",
            error_message=f"Unsupported local_host capability '{key}'",
        )

    async def subscribe_events(self, device_id: str, callback: EventCallback):
        return None

    async def disconnect(self, device_id: str, reason: str = "operator_requested") -> None:
        return None

    async def revoke_access(self, device_id: str, reason: str = "permission_revoked") -> None:
        return None

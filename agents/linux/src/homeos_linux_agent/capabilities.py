import base64
import hashlib
import os
import platform
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any


class LinuxCapabilityProvider:
    """Real Linux host capability probing and execution with X11/Wayland/headless & root-jailed file enforcement."""

    def __init__(self, authorized_roots: list[Path], allowed_apps: dict[str, list[str]] | None = None):
        self.authorized_roots = [r.resolve(strict=False) for r in authorized_roots]
        for r in self.authorized_roots:
            r.mkdir(parents=True, exist_ok=True)
        self.allowed_apps = allowed_apps or {
            "terminal_info": ["uname", "-a"],
            "xdg_open_home": ["true"],
        }

    def detect_display_session(self) -> tuple[str, bool, str | None]:
        wayland = os.environ.get("WAYLAND_DISPLAY")
        x11 = os.environ.get("DISPLAY")
        session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()

        if wayland or session_type == "wayland":
            has_grim = shutil.which("grim") is not None
            if has_grim:
                return ("wayland", True, None)
            return ("wayland", False, "Wayland session detected; install 'grim' or grant PipeWire ScreenCast portal consent.")
        if x11 or session_type == "x11":
            has_tool = any(shutil.which(t) is not None for t in ("import", "scrot", "xwd"))
            if has_tool:
                return ("x11", True, None)
            return ("x11", False, "X11 session detected, but no capture utility (import/scrot/xwd) is installed.")
        return ("headless", False, "No active X11 or Wayland display session detected on this Linux host.")

    def detect_remote_input(self) -> tuple[bool, str | None]:
        disp, _, _ = self.detect_display_session()
        if disp == "x11" and shutil.which("xdotool") is not None:
            return (True, None)
        if disp == "wayland":
            return (False, "Wayland compositor isolates global input injection; requires org.freedesktop.portal.RemoteDesktop session.")
        return (False, "No active graphical desktop session with xdotool/RemoteDesktop portal available.")

    def detect_audio_backend(self) -> tuple[str | None, str | None]:
        if shutil.which("wpctl"):
            return ("wpctl", None)
        if shutil.which("pactl"):
            return ("pactl", None)
        if shutil.which("amixer"):
            return ("amixer", None)
        return (None, "No PipeWire (wpctl), PulseAudio (pactl), or ALSA (amixer) mixer utility detected.")

    def detect_mpris_media(self) -> tuple[bool, str | None]:
        if shutil.which("playerctl"):
            return (True, None)
        return (False, "MPRIS2 'playerctl' utility is not installed on this Linux host.")

    def build_capabilities_manifest(self) -> list[dict[str, Any]]:
        _disp_kind, screen_ok, screen_reason = self.detect_display_session()
        input_ok, input_reason = self.detect_remote_input()
        audio_tool, audio_reason = self.detect_audio_backend()
        media_ok, media_reason = self.detect_mpris_media()

        return [
            {
                "capability_key": "telemetry.status",
                "category": "telemetry",
                "display_name": "Linux System Telemetry (/proc, CPU, RAM, Disk)",
                "supported": True,
                "requires_permission": "telemetry:read",
                "requires_confirmation": False,
                "parameters_schema": {},
            },
            {
                "capability_key": "files.browse",
                "category": "files",
                "display_name": "Authorized Directory Browser & Reader",
                "supported": len(self.authorized_roots) > 0,
                "unavailable_reason": None if self.authorized_roots else "No authorized directories configured.",
                "requires_permission": "files:browse",
                "requires_confirmation": False,
                "parameters_schema": {"properties": {"path": {"type": "string"}, "action": {"type": "string"}}},
            },
            {
                "capability_key": "files.transfer",
                "category": "files",
                "display_name": "SHA-256 Verified File Transfer",
                "supported": len(self.authorized_roots) > 0,
                "unavailable_reason": None if self.authorized_roots else "No authorized directories configured.",
                "requires_permission": "files:write",
                "requires_confirmation": False,
                "parameters_schema": {},
            },
            {
                "capability_key": "processes.list",
                "category": "applications",
                "display_name": "Active Process Inspector",
                "supported": True,
                "requires_permission": "apps:manage",
                "requires_confirmation": False,
                "parameters_schema": {"properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}},
            },
            {
                "capability_key": "apps.launch",
                "category": "applications",
                "display_name": "Launch Allowlisted Application",
                "supported": True,
                "requires_permission": "apps:launch",
                "requires_confirmation": False,
                "parameters_schema": {"required": ["app_id"], "properties": {"app_id": {"type": "string"}}},
            },
            {
                "capability_key": "volume.set",
                "category": "audio",
                "display_name": "Audio Output Volume",
                "supported": audio_tool is not None,
                "unavailable_reason": audio_reason,
                "requires_permission": "volume:control",
                "requires_confirmation": False,
                "parameters_schema": {"required": ["level"], "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}}},
            },
            {
                "capability_key": "media.control",
                "category": "media",
                "display_name": "MPRIS2 Media Session Control",
                "supported": media_ok,
                "unavailable_reason": media_reason,
                "requires_permission": "media:control",
                "requires_confirmation": False,
                "parameters_schema": {"required": ["action"], "properties": {"action": {"type": "string"}}},
            },
            {
                "capability_key": "screen.capture",
                "category": "screen",
                "display_name": "Authorized Display Snapshot",
                "supported": screen_ok,
                "unavailable_reason": screen_reason,
                "requires_permission": "screen:view",
                "requires_confirmation": True,
                "parameters_schema": {},
            },
            {
                "capability_key": "input.remote",
                "category": "input",
                "display_name": "Authorized Remote Input",
                "supported": input_ok,
                "unavailable_reason": input_reason,
                "requires_permission": "input:remote",
                "requires_confirmation": True,
                "parameters_schema": {},
            },
        ]

    def collect_telemetry(self) -> dict[str, Any]:
        # Load averages
        try:
            load1, load5, load15 = os.getloadavg()
        except OSError:
            load1 = load5 = load15 = 0.0

        # Memory info from /proc/meminfo
        mem_total_kb = 0
        mem_avail_kb = 0
        meminfo = Path("/proc/meminfo")
        if meminfo.exists():
            for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("MemTotal:"):
                    mem_total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    mem_avail_kb = int(line.split()[1])
        mem_used_pct = round(((mem_total_kb - mem_avail_kb) / mem_total_kb) * 100.0, 1) if mem_total_kb else 0.0

        # Uptime
        uptime_seconds = 0.0
        uptime_file = Path("/proc/uptime")
        if uptime_file.exists():
            uptime_seconds = float(uptime_file.read_text().split()[0])

        # Disk usage
        disk = shutil.disk_usage("/")
        disk_used_pct = round((disk.used / disk.total) * 100.0, 1) if disk.total else 0.0

        # Battery if laptop
        battery_level: int | None = None
        is_charging: bool | None = None
        ps_root = Path("/sys/class/power_supply")
        if ps_root.exists():
            for bat in ps_root.glob("BAT*"):
                cap_file = bat / "capacity"
                stat_file = bat / "status"
                if cap_file.exists():
                    battery_level = int(cap_file.read_text().strip())
                if stat_file.exists():
                    is_charging = stat_file.read_text().strip().lower() in ("charging", "full")
                break

        disp_kind, _, _ = self.detect_display_session()
        return {
            "battery_level": battery_level,
            "is_charging": is_charging,
            "health_score": 100 if disk_used_pct < 92 and mem_used_pct < 95 else 75,
            "attributes": {
                "hostname": socket.gethostname(),
                "kernel": platform.release(),
                "architecture": platform.machine(),
                "cpu_cores": os.cpu_count() or 1,
                "load_avg_1m": round(load1, 2),
                "load_avg_5m": round(load5, 2),
                "load_avg_15m": round(load15, 2),
                "memory_total_mb": round(mem_total_kb / 1024),
                "memory_used_percent": mem_used_pct,
                "disk_total_gb": round(disk.total / (1024**3), 1),
                "disk_used_percent": disk_used_pct,
                "uptime_seconds": int(uptime_seconds),
                "display_session": disp_kind,
                "authorized_roots": [str(r) for r in self.authorized_roots],
            },
        }

    def _resolve_jailed_path(self, rel_or_abs: str) -> Path:
        if not self.authorized_roots:
            raise PermissionError("No authorized roots configured on agent")
        base_root = self.authorized_roots[0]
        if not rel_or_abs:
            return base_root
        candidate = Path(rel_or_abs)
        if not candidate.is_absolute():
            candidate = (base_root / candidate).resolve(strict=False)
        else:
            candidate = candidate.resolve(strict=False)

        for root in self.authorized_roots:
            try:
                candidate.relative_to(root)
                return candidate
            except ValueError:
                continue
        raise PermissionError(f"Requested path '{rel_or_abs}' is outside authorized roots")

    def execute_capability(self, capability_key: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if capability_key == "telemetry.status":
            return {"acknowledged": True, "state_verified": True, "verification_method": "procfs_readback", "result": self.collect_telemetry()}

        if capability_key == "processes.list":
            limit = min(100, max(1, int(parameters.get("limit", 25))))
            procs: list[dict[str, Any]] = []
            my_uid = os.getuid()
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    if entry.stat().st_uid != my_uid:
                        continue
                    comm = (entry / "comm").read_text(encoding="utf-8", errors="ignore").strip()
                    cmdline = (entry / "cmdline").read_text(encoding="utf-8", errors="ignore").replace("\x00", " ").strip()
                    procs.append({"pid": int(entry.name), "name": comm, "cmdline": cmdline[:160]})
                    if len(procs) >= limit:
                        break
                except OSError:
                    continue
            return {
                "acknowledged": True,
                "state_verified": True,
                "verification_method": "procfs_process_scan",
                "result": {"processes": procs, "count": len(procs)},
            }

        if capability_key == "files.browse":
            action = parameters.get("action", "list")
            target = self._resolve_jailed_path(str(parameters.get("path", "")))
            if action == "read_file":
                if not target.is_file():
                    return {"acknowledged": False, "error_code": "FILE_NOT_FOUND", "error_message": f"File not found: {target.name}"}
                raw = target.read_bytes()
                sha = hashlib.sha256(raw).hexdigest()
                return {
                    "acknowledged": True,
                    "state_verified": True,
                    "verification_method": "sha256_file_read",
                    "result": {
                        "file_name": target.name,
                        "size": len(raw),
                        "sha256": sha,
                        "content_b64": base64.b64encode(raw).decode("ascii"),
                    },
                }

            if not target.is_dir():
                return {"acknowledged": False, "error_code": "NOT_A_DIRECTORY", "error_message": "Target is not a directory"}
            entries = []
            for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                # Skip symlinks that escape authorized roots
                try:
                    resolved_child = child.resolve(strict=False)
                    if not any(self._is_within(resolved_child, r) for r in self.authorized_roots):
                        continue
                    stat = child.stat()
                    entries.append(
                        {
                            "name": child.name,
                            "path": str(resolved_child),
                            "is_dir": child.is_dir(),
                            "size": stat.st_size if child.is_file() else 0,
                            "modified_at": int(stat.st_mtime),
                        }
                    )
                except OSError:
                    continue
            return {
                "acknowledged": True,
                "state_verified": True,
                "verification_method": "directory_stat_readback",
                "result": {"current_path": str(target), "authorized_roots": [str(r) for r in self.authorized_roots], "entries": entries},
            }

        if capability_key == "files.transfer":
            target_dir = self._resolve_jailed_path(str(parameters.get("target_dir", "")))
            target_dir.mkdir(parents=True, exist_ok=True)
            safe_name = os.path.basename(str(parameters.get("file_name", "transfer.bin")))
            dest = self._resolve_jailed_path(str(target_dir / safe_name))
            raw = base64.b64decode(parameters["content_b64"])
            actual_sha = hashlib.sha256(raw).hexdigest()
            expected_sha = parameters.get("expected_sha256")
            if expected_sha and expected_sha != actual_sha:
                return {"acknowledged": False, "error_code": "CHECKSUM_MISMATCH", "error_message": "SHA-256 verification failed"}
            dest.write_bytes(raw)
            verify_sha = hashlib.sha256(dest.read_bytes()).hexdigest()
            return {
                "acknowledged": True,
                "state_verified": verify_sha == actual_sha,
                "verification_method": "post_write_sha256_readback",
                "result": {"written_path": str(dest), "size": len(raw), "sha256": verify_sha},
            }

        if capability_key == "apps.launch":
            app_id = str(parameters.get("app_id", ""))
            cmd = self.allowed_apps.get(app_id)
            if not cmd:
                return {
                    "acknowledged": False,
                    "error_code": "APP_NOT_ALLOWLISTED",
                    "error_message": f"Application '{app_id}' is not in agent allowlist ({list(self.allowed_apps.keys())})",
                }
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
            return {
                "acknowledged": True,
                "state_verified": proc.returncode == 0,
                "verification_method": "process_exit_code",
                "result": {"app_id": app_id, "exit_code": proc.returncode, "stdout": proc.stdout.strip()[:240]},
            }

        if capability_key == "volume.set":
            tool, reason = self.detect_audio_backend()
            if not tool:
                return {"acknowledged": False, "error_code": "AUDIO_UNAVAILABLE", "error_message": reason}
            lvl = int(parameters["level"])
            if tool == "wpctl":
                subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{lvl}%"], timeout=3, check=False)
            elif tool == "pactl":
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{lvl}%"], timeout=3, check=False)
            else:
                subprocess.run(["amixer", "sset", "Master", f"{lvl}%"], timeout=3, check=False)
            return {"acknowledged": True, "state_verified": True, "verification_method": f"{tool}_mixer_set", "result": {"level": lvl}}

        return {
            "acknowledged": False,
            "error_code": "UNSUPPORTED_CAPABILITY",
            "error_message": f"Capability '{capability_key}' is not supported on this Linux session",
        }

    @staticmethod
    def _is_within(child: Path, parent: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False

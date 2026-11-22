import asyncio
import hashlib
import hmac
import json
import platform
import secrets
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import websockets

from homeos_linux_agent.capabilities import LinuxCapabilityProvider
from homeos_linux_agent.keystore import AgentCredentialStore


def sign_agent_request(secret: str, method: str, path: str, timestamp: int, nonce: str, body: bytes = b"") -> str:
    body_sha = hashlib.sha256(body).hexdigest()
    canonical = f"{method.upper()}|{path}|{timestamp}|{nonce}|{body_sha}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def verify_update_bundle_sha256(bundle_path: Path, expected_sha256: str) -> bool:
    digest = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    return hmac.compare_digest(digest, expected_sha256.lower().strip())


class LinuxDeviceAgent:
    """Production Linux companion agent with encrypted keystore, HMAC replay-protected WS, and auto-reconnection."""

    def __init__(
        self,
        *,
        hub_http_url: str,
        state_dir: Path,
        authorized_roots: list[Path],
        telemetry_interval: float = 10.0,
    ):
        self.hub_http_url = hub_http_url.rstrip("/")
        self.store = AgentCredentialStore(state_dir)
        self.capabilities = LinuxCapabilityProvider(authorized_roots=authorized_roots)
        self.telemetry_interval = telemetry_interval
        self._stop_event = asyncio.Event()

    def hardware_fingerprint(self) -> str:
        machine_id = ""
        for p in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
            if p.exists():
                machine_id = p.read_text(encoding="utf-8", errors="ignore").strip()
                break
        seed = machine_id or f"{socket.gethostname()}-{platform.machine()}"
        return "linux-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]

    async def enroll(
        self,
        *,
        pairing_session_id: str,
        pin_or_token: str,
        device_name: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        payload = {
            "pairing_session_id": pairing_session_id,
            "pin_or_token": pin_or_token,
            "hardware_fingerprint": self.hardware_fingerprint(),
            "name": device_name or f"{socket.gethostname()} (Linux Workstation)",
            "category": "desktop",
            "manufacturer": "Linux Native Host",
            "model": platform.machine(),
            "os_name": "Linux",
            "os_version": platform.release(),
            "capabilities": self.capabilities.build_capabilities_manifest(),
            "metadata": {"agent_version": "2.0.0"},
        }
        if http_client is not None:
            resp = await http_client.post(f"{self.hub_http_url}/api/v1/pairing/complete", json=payload)
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{self.hub_http_url}/api/v1/pairing/complete", json=payload)

        resp.raise_for_status()
        data = resp.json()
        creds = data["credentials"]
        creds["hub_http_url"] = self.hub_http_url
        self.store.save(creds)
        return data

    def _ws_url(self, creds: dict[str, Any]) -> str:
        base = self.hub_http_url.replace("https://", "wss://").replace("http://", "ws://")
        ts = int(time.time())
        nonce = secrets.token_urlsafe(16)
        sig = sign_agent_request(
            secret=creds["hmac_secret"],
            method="WS",
            path="/api/v1/agent/ws",
            timestamp=ts,
            nonce=nonce,
            body=b"",
        )
        qs = urlencode(
            {
                "device_id": creds["device_id"],
                "token": creds["device_token"],
                "timestamp": ts,
                "nonce": nonce,
                "signature": sig,
            }
        )
        return f"{base}/api/v1/agent/ws?{qs}"

    async def run_forever(self) -> None:
        backoff = 1.0
        while not self._stop_event.is_set():
            creds = self.store.load()
            if not creds:
                raise RuntimeError("Linux Agent is not enrolled; run 'homeos-linux-agent pair' first")

            ws_uri = self._ws_url(creds)
            try:
                async with websockets.connect(ws_uri, open_timeout=5.0) as ws:
                    backoff = 1.0
                    await self._session_loop(ws)
            except asyncio.CancelledError:
                break
            except Exception:
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                except (TimeoutError, asyncio.TimeoutError):
                    pass
                backoff = min(backoff * 2.0, 15.0)

    async def _session_loop(self, ws) -> None:
        # Immediately publish fresh telemetry + dynamic capabilities on connect
        initial = self.capabilities.collect_telemetry()
        initial["capabilities"] = self.capabilities.build_capabilities_manifest()
        await ws.send(json.dumps({"type": "telemetry", "payload": initial}))

        async def _telemetry_sender():
            while not self._stop_event.is_set():
                await asyncio.sleep(self.telemetry_interval)
                snap = self.capabilities.collect_telemetry()
                await ws.send(json.dumps({"type": "telemetry", "payload": snap}))

        sender_task = asyncio.create_task(_telemetry_sender())
        try:
            while not self._stop_event.is_set():
                raw = await ws.recv()
                msg = json.loads(raw)
                if msg.get("type") == "command.execute":
                    cmd_id = msg["command_id"]
                    cap_key = msg["capability_key"]
                    params = msg.get("parameters") or {}
                    try:
                        outcome = await asyncio.to_thread(self.capabilities.execute_capability, cap_key, params)
                    except Exception as exc:
                        outcome = {
                            "acknowledged": False,
                            "state_verified": False,
                            "error_code": "AGENT_EXECUTION_ERROR",
                            "error_message": str(exc),
                        }
                    await ws.send(json.dumps({"type": "command.receipt", "command_id": cmd_id, **outcome}))
                elif msg.get("type") == "session.terminated":
                    break
        finally:
            sender_task.cancel()
            try:
                await sender_task
            except asyncio.CancelledError:
                pass

    def stop(self) -> None:
        self._stop_event.set()

import asyncio
import hashlib
import socket
from pathlib import Path
import httpx
import pytest
import uvicorn

from home_os.config import Settings
from home_os.main import create_app
from homeos_linux_agent.client import LinuxDeviceAgent


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_live_linux_agents_pairing_telemetry_files_d2d_transfer_and_revocation(tmp_data_dir: Path):
    port = _free_tcp_port()
    base_url = f"http://127.0.0.1:{port}"

    settings = Settings(
        bind_host="127.0.0.1",
        bind_port=port,
        data_dir=tmp_data_dir / "hub_data",
    )
    app = create_app(settings)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    # Wait for server to bind
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        for _ in range(40):
            try:
                r = await client.get("/api/v1/health")
                if r.status_code == 200:
                    break
            except Exception:
                await asyncio.sleep(0.05)

        # Initialize owner
        setup_res = await client.post(
            "/api/v1/auth/setup",
            json={"username": "owner", "display_name": "Linux Lab Owner", "password": "OwnerStrongPassword!99"},
        )
        assert setup_res.status_code == 200
        headers = {"Authorization": f"Bearer {setup_res.json()['access_token']}"}

        # Prepare two independent authorized folders for Agent A and Agent B on this real Linux machine
        share_a = tmp_data_dir / "agent_a_share"
        share_b = tmp_data_dir / "agent_b_share"
        share_a.mkdir(parents=True)
        share_b.mkdir(parents=True)
        sample_audio = share_a / "ambient_chime.wav"
        sample_payload = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 128
        sample_audio.write_bytes(sample_payload)
        expected_sha = hashlib.sha256(sample_payload).hexdigest()

        # Pair Agent A (Primary Linux Workstation)
        pair_a = (
            await client.post(
                "/api/v1/pairing/initiate",
                headers=headers,
                json={
                    "adapter_type": "linux_agent",
                    "requested_permissions": [
                        "telemetry:read",
                        "files:browse",
                        "files:write",
                        "apps:manage",
                        "apps:launch",
                    ],
                },
            )
        ).json()

        agent_a = LinuxDeviceAgent(
            hub_http_url=base_url,
            state_dir=tmp_data_dir / "agent_a_state",
            authorized_roots=[share_a],
            telemetry_interval=2.0,
        )
        enrolled_a = await agent_a.enroll(
            pairing_session_id=pair_a["session_id"],
            pin_or_token=pair_a["pin"],
            device_name="Primary Linux Workstation",
            http_client=client,
        )
        dev_a_id = enrolled_a["device"]["id"]

        # Pair Agent B (Secondary Linux Media Server — customize fingerprint subclass for distinct identity)
        pair_b = (
            await client.post(
                "/api/v1/pairing/initiate",
                headers=headers,
                json={
                    "adapter_type": "linux_agent",
                    "requested_permissions": ["telemetry:read", "files:browse", "files:write"],
                },
            )
        ).json()

        agent_b = LinuxDeviceAgent(
            hub_http_url=base_url,
            state_dir=tmp_data_dir / "agent_b_state",
            authorized_roots=[share_b],
            telemetry_interval=2.0,
        )
        agent_b.hardware_fingerprint = lambda: "linux-secondary-node-b-fingerprint"  # type: ignore[method-assign]
        enrolled_b = await agent_b.enroll(
            pairing_session_id=pair_b["session_id"],
            pin_or_token=pair_b["pin"],
            device_name="Secondary Linux Media Node",
            http_client=client,
        )
        dev_b_id = enrolled_b["device"]["id"]

        # Start both real Linux agents connecting over authenticated WebSockets
        task_a = asyncio.create_task(agent_a.run_forever())
        task_b = asyncio.create_task(agent_b.run_forever())

        try:
            # Wait until both agents report online with live /proc telemetry
            for _ in range(40):
                detail_a = (await client.get(f"/api/v1/devices/{dev_a_id}", headers=headers)).json()["device"]
                detail_b = (await client.get(f"/api/v1/devices/{dev_b_id}", headers=headers)).json()["device"]
                if detail_a["connection_status"] == "online" and "cpu_cores" in detail_a["health_details"] and detail_b["connection_status"] == "online":
                    break
                await asyncio.sleep(0.05)

            assert detail_a["connection_status"] == "online"
            assert detail_a["health_details"]["cpu_cores"] >= 1
            assert detail_a["health_details"]["memory_total_mb"] > 100

            # Verify honest headless screen capability reporting on this Linux container
            caps_a = {c["capability_key"]: c for c in detail_a["capabilities"]}
            assert caps_a["screen.capture"]["supported"] is False
            assert "display session" in (caps_a["screen.capture"]["unavailable_reason"] or "").lower()

            # Execute real process inspection command on Agent A
            proc_cmd = await client.post(
                f"/api/v1/devices/{dev_a_id}/commands",
                headers=headers,
                json={"capability_key": "processes.list", "parameters": {"limit": 10}},
            )
            assert proc_cmd.status_code == 200
            cmd_data = proc_cmd.json()["command"]
            assert cmd_data["status"] == "verified"
            assert cmd_data["result"]["count"] >= 1

            # Browse Agent A's authorized directory
            browse_res = await client.get(f"/api/v1/files/browse/{dev_a_id}", headers=headers)
            assert browse_res.status_code == 200
            names = [e["name"] for e in browse_res.json()["entries"]]
            assert "ambient_chime.wav" in names

            # Index media on Agent A and stream byte-range HTTP 206 audio
            idx_res = await client.post(f"/api/v1/media/index/{dev_a_id}", headers=headers)
            assert idx_res.status_code == 200
            media_items = idx_res.json()["items"]
            assert len(media_items) == 1
            media_id = media_items[0]["id"]

            stream_res = await client.get(
                f"/api/v1/media/stream/{media_id}",
                headers={**headers, "Range": "bytes=0-15"},
            )
            assert stream_res.status_code == 206
            assert len(stream_res.content) == 16

            # Execute real Device-to-Device file transfer from Agent A -> Agent B!
            d2d_res = await client.post(
                "/api/v1/files/transfer",
                headers=headers,
                json={
                    "source_device_id": dev_a_id,
                    "source_path": str(sample_audio),
                    "target_device_id": dev_b_id,
                    "target_dir": "",
                },
            )
            assert d2d_res.status_code == 200
            transfer = d2d_res.json()["transfer"]
            assert transfer["status"] == "completed"
            assert transfer["sha256_checksum"] == expected_sha
            assert (share_b / "ambient_chime.wav").read_bytes() == sample_payload

            # Revoke files:browse on Agent A -> verify immediate enforcement!
            await client.post(
                f"/api/v1/devices/{dev_a_id}/permissions",
                headers=headers,
                json={"permission_scope": "files:browse", "status": "revoked"},
            )
            denied_browse = await client.get(f"/api/v1/files/browse/{dev_a_id}", headers=headers)
            assert denied_browse.status_code == 403

            # Stop Agent B and verify hub detects disconnection accurately
            agent_b.stop()
            task_b.cancel()
            for _ in range(30):
                detail_b_after = (await client.get(f"/api/v1/devices/{dev_b_id}", headers=headers)).json()["device"]
                if detail_b_after["connection_status"] == "offline":
                    break
                await asyncio.sleep(0.05)
            assert detail_b_after["connection_status"] == "offline"

        finally:
            agent_a.stop()
            agent_b.stop()
            task_a.cancel()
            task_b.cancel()
            server.should_exit = True
            await server_task

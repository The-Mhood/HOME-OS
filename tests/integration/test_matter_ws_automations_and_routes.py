import asyncio
import json
import socket
import time
from pathlib import Path
import pytest
import websockets
from fastapi.testclient import TestClient

from home_os.cli import main as cli_main
from home_os.config import Settings
from home_os.main import create_app
from home_os.security.replay import sign_device_request


@pytest.mark.asyncio
async def test_matter_json_rpc_bridge_against_local_ws_server():
    from home_os.adapters.base import AdapterCommand
    from home_os.adapters.matter import MatterAdapter

    received_commands: list[dict] = []

    async def _matter_ws_handler(ws):
        await ws.send(json.dumps({"fabric_id": 1, "sdk_version": "2026.11"}))
        async for raw in ws:
            req = json.loads(raw)
            msg_id = req["message_id"]
            cmd = req["command"]
            if cmd == "get_nodes":
                await ws.send(
                    json.dumps(
                        {
                            "message_id": msg_id,
                            "result": [{"node_id": 7, "available": True, "attributes": {"vendor": "Eve"}}],
                        }
                    )
                )
            elif cmd == "device_command":
                received_commands.append(req["args"])
                await ws.send(json.dumps({"message_id": msg_id, "result": {"status": 0}}))

    server = await websockets.serve(_matter_ws_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    adapter = MatterAdapter()
    adapter.configure_server(f"ws://127.0.0.1:{port}")
    try:
        nodes = await adapter.discover()
        assert len(nodes) == 1
        assert nodes[0].raw_properties["node_id"] == 7

        adapter.bind_node("dev-matter-7", 7)
        status = await adapter.get_status("dev-matter-7")
        assert status.online is True

        receipt = await adapter.execute_command(
            "dev-matter-7",
            AdapterCommand(command_id="m-1", capability_key="power.set", parameters={"on": True}),
        )
        assert receipt.acknowledged is True
        assert receipt.state_verified is True
        assert received_commands[0]["command_name"] == "On"

        lvl_receipt = await adapter.execute_command(
            "dev-matter-7",
            AdapterCommand(command_id="m-2", capability_key="level.set", parameters={"level": 180}),
        )
        assert lvl_receipt.state_verified is True
    finally:
        server.close()
        await server.wait_closed()


def test_dashboard_sessions_automations_integrations_and_ws_routes(tmp_data_dir: Path, monkeypatch):
    settings = Settings(data_dir=tmp_data_dir)
    app = create_app(settings)

    with TestClient(app) as client:
        setup = client.post(
            "/api/v1/auth/setup",
            json={"username": "admin", "display_name": "Admin", "password": "AdminPassword!2026"},
        ).json()
        token = setup["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Login second session & inspect/revoke session
        login2 = client.post("/api/v1/auth/login", json={"username": "admin", "password": "AdminPassword!2026"})
        assert login2.status_code == 200
        me_res = client.get("/api/v1/auth/me", headers=headers)
        assert me_res.status_code == 200

        sessions = client.get("/api/v1/auth/sessions", headers=headers).json()["sessions"]
        assert len(sessions) == 2
        other_sid = next(s["id"] for s in sessions if not s["is_current"])
        assert client.delete(f"/api/v1/auth/sessions/{other_sid}", headers=headers).status_code == 200

        # Operator WebSocket rejects invalid token and accepts valid session token
        with pytest.raises(Exception):
            with client.websocket_connect("/api/v1/ws?token=invalid-token") as _ws_bad:
                _ws_bad.receive_json()

        with client.websocket_connect(f"/api/v1/ws?token={token}") as ws:
            greeting = ws.receive_json()
            assert greeting["event_type"] == "ws.connected"

        # Pair an iOS Companion and verify explicit unsupported sandbox explanations are exposed
        pair_ios = client.post(
            "/api/v1/pairing/initiate",
            headers=headers,
            json={"adapter_type": "ios_companion", "requested_permissions": ["telemetry:read", "media:control"]},
        ).json()
        done_ios = client.post(
            "/api/v1/pairing/complete",
            json={
                "pairing_session_id": pair_ios["session_id"],
                "pin_or_token": pair_ios["pin"],
                "hardware_fingerprint": "ios-iphone16pro-001",
                "name": "iPhone 16 Pro",
                "category": "smartphone",
                "manufacturer": "Apple",
                "model": "iPhone17,1",
                "os_name": "iOS",
                "os_version": "18.2",
            },
        ).json()
        ios_id = done_ios["device"]["id"]
        ios_creds = done_ios["credentials"]

        ios_caps = {c["capability_key"]: c for c in done_ios["device"]["capabilities"]}
        assert ios_caps["sms.send"]["supported"] is False
        assert "Apple iOS" in ios_caps["sms.send"]["unavailable_reason"]

        # Publish signed telemetry + media session from iOS device
        telem_body = json.dumps(
            {
                "battery_level": 18,
                "is_charging": False,
                "health_score": 98,
                "attributes": {"thermal_state": "nominal", "wifi_ssid": "HomeNet-5G"},
                "media_session": {
                    "app_name": "Apple Music",
                    "title": "Midnight City",
                    "artist": "M83",
                    "playback_state": "playing",
                    "volume_percent": 45,
                },
            }
        ).encode("utf-8")
        ts = int(time.time())
        nonce = "ios-telem-nonce-0001"
        sig = sign_device_request(
            secret=ios_creds["hmac_secret"],
            method="POST",
            path="/api/v1/agent/telemetry",
            timestamp=ts,
            nonce=nonce,
            body=telem_body,
        )
        t_res = client.post(
            "/api/v1/agent/telemetry",
            content=telem_body,
            headers={
                "Content-Type": "application/json",
                "X-Device-ID": ios_id,
                "X-Device-Token": ios_creds["device_token"],
                "X-Timestamp": str(ts),
                "X-Nonce": nonce,
                "X-Signature": sig,
            },
        )
        assert t_res.status_code == 200

        # Check Dashboard Summary & System Settings & Media Sessions
        dash = client.get("/api/v1/dashboard/summary", headers=headers).json()
        assert dash["counts"]["registered"] == 1
        assert len(dash["active_media_sessions"]) == 1
        assert dash["active_media_sessions"][0]["title"] == "Midnight City"

        sys_cfg = client.get("/api/v1/system/settings", headers=headers).json()
        assert sys_cfg["bind_host"] == "127.0.0.1"

        # Create Automation Rule requiring confirmation -> trigger -> approve -> verify succeeded
        rule_res = client.post(
            "/api/v1/automations/rules",
            headers=headers,
            json={
                "name": "Midnight Quiet Mode",
                "description": "Send hub alert at midnight",
                "trigger_type": "schedule",
                "trigger_config": {"at": "00:00"},
                "actions": [{"type": "hub_notification", "title": "Midnight Check", "message": "Appliances checked"}],
                "requires_confirmation": True,
            },
        )
        assert rule_res.status_code == 200
        rule_id = rule_res.json()["rule"]["id"]

        trig_res = client.post(f"/api/v1/automations/rules/{rule_id}/trigger", headers=headers).json()["run"]
        assert trig_res["status"] == "awaiting_confirmation"

        app_run = client.post(f"/api/v1/automations/runs/{trig_res['id']}/approve", headers=headers).json()["run"]
        assert app_run["status"] == "succeeded"

        # Configure Matter integration & list/delete
        mat_res = client.post(
            "/api/v1/integrations/matter",
            headers=headers,
            json={"name": "Local Matter Fabric", "ws_url": "ws://127.0.0.1:5580/ws"},
        )
        assert mat_res.status_code == 200
        int_id = mat_res.json()["integration"]["id"]
        assert len(client.get("/api/v1/integrations", headers=headers).json()["integrations"]) == 1
        assert client.delete(f"/api/v1/integrations/{int_id}", headers=headers).status_code == 200

        # Credential rotation, isolate & restore, security matrix, discovery scan
        rot = client.post(f"/api/v1/devices/{ios_id}/rotate-credentials", headers=headers)
        assert rot.status_code == 200
        assert rot.json()["key_version"] == 2

        assert client.post(f"/api/v1/devices/{ios_id}/isolate", headers=headers, json={"reason": "Test"}).status_code == 200
        assert client.post(f"/api/v1/devices/{ios_id}/restore", headers=headers).status_code == 200

        perm_matrix = client.get("/api/v1/security/permissions", headers=headers).json()["matrix"]
        assert len(perm_matrix) == 1

    # Verify CLI verify-audit exits 0 on valid database
    monkeypatch.setenv("HOMEOS_DATA_DIR", str(tmp_data_dir))
    assert cli_main(["verify-audit"]) == 0

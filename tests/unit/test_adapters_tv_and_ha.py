import asyncio
import ssl
from pathlib import Path
import pytest
import httpx

from home_os.adapters.base import AdapterCommand
from home_os.adapters.google_tv import (
    AndroidTvRemoteAdapter,
    decode_remote_frame,
    encode_remote_configure_frame,
    encode_remote_ping_frame,
)
from home_os.adapters.home_assistant import HomeAssistantAdapter
from home_os.security.crypto import ensure_local_tls_certificate


@pytest.mark.asyncio
async def test_google_tv_remote_v2_tls_protocol_execution(tmp_data_dir: Path):
    tls_info = ensure_local_tls_certificate(tmp_data_dir / "tv_tls")
    received_frames: list[dict] = []

    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ssl.load_cert_chain(certfile=str(tls_info.cert_path), keyfile=str(tls_info.key_path))

    async def _handle_tv_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            # Send initial server RemoteConfigure greeting
            writer.write(encode_remote_configure_frame(model="Chromecast Google TV 4K", vendor="Google"))
            await writer.drain()
            while True:
                length_byte = await reader.read(1)
                if not length_byte:
                    break
                length = length_byte[0]
                payload = await reader.readexactly(length)
                decoded = decode_remote_frame(payload)
                received_frames.append(decoded)
                # Send acknowledgement ping/ack frame
                writer.write(encode_remote_ping_frame(val=1))
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(_handle_tv_client, "127.0.0.1", 0, ssl=server_ssl)
    tv_port = server.sockets[0].getsockname()[1]

    adapter = AndroidTvRemoteAdapter(client_cert_info=tls_info)
    adapter.register_endpoint(device_id="tv-living-room", host="127.0.0.1", operational_port=tv_port)

    try:
        # Execute D-Pad Navigation command
        nav_receipt = await adapter.execute_command(
            "tv-living-room",
            AdapterCommand(
                command_id="cmd-tv-1",
                capability_key="tv.navigate",
                parameters={"direction": "DPAD_CENTER"},
            ),
        )
        assert nav_receipt.acknowledged is True
        assert nav_receipt.state_verified is True

        # Execute Deep-Link App Launch command
        app_receipt = await adapter.execute_command(
            "tv-living-room",
            AdapterCommand(
                command_id="cmd-tv-2",
                capability_key="apps.launch",
                parameters={"app_link": "https://www.youtube.com/tv"},
            ),
        )
        assert app_receipt.acknowledged is True
        assert any(f.get("type") == "key_inject" and f.get("keycode") == 23 for f in received_frames)
        assert any(f.get("type") == "app_link" and "youtube.com" in f.get("uri", "") for f in received_frames)
    finally:
        await adapter.disconnect("tv-living-room")
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_home_assistant_adapter_discovery_command_and_post_state_verification():
    entity_states = {
        "light.studio_desk": {
            "entity_id": "light.studio_desk",
            "state": "off",
            "attributes": {"friendly_name": "Studio Desk Light", "brightness": 0},
        },
        "media_player.bedroom_speaker": {
            "entity_id": "media_player.bedroom_speaker",
            "state": "idle",
            "attributes": {"friendly_name": "Bedroom Smart Speaker", "volume_level": 0.5},
        },
        "lock.front_door": {
            "entity_id": "lock.front_door",
            "state": "locked",
            "attributes": {"friendly_name": "Front Door Deadbolt"},
        },
    }

    def _mock_ha_handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        if auth != "Bearer ha-valid-token":
            return httpx.Response(401, json={"message": "Unauthorized"})

        path = request.url.path
        if path == "/api/config":
            return httpx.Response(200, json={"location_name": "Home", "version": "2026.11.1"})
        if path == "/api/states":
            return httpx.Response(200, json=list(entity_states.values()))
        if path.startswith("/api/states/"):
            ent_id = path.removeprefix("/api/states/")
            if ent_id in entity_states:
                return httpx.Response(200, json=entity_states[ent_id])
            return httpx.Response(404)
        if path == "/api/services/light/turn_on":
            import json
            payload = json.loads(request.content.decode("utf-8"))
            ent_id = payload["entity_id"]
            entity_states[ent_id]["state"] = "on"
            entity_states[ent_id]["attributes"]["brightness"] = payload.get("brightness", 255)
            return httpx.Response(200, json=[entity_states[ent_id]])
        if path == "/api/services/media_player/volume_set":
            import json
            payload = json.loads(request.content.decode("utf-8"))
            ent_id = payload["entity_id"]
            entity_states[ent_id]["attributes"]["volume_level"] = payload["volume_level"]
            return httpx.Response(200, json=[entity_states[ent_id]])
        return httpx.Response(404)

    transport = httpx.MockTransport(_mock_ha_handler)
    adapter = HomeAssistantAdapter(http_transport=transport)
    adapter.configure_connection(
        base_url="http://homeassistant.local:8123",
        access_token="ha-valid-token",
    )

    discovered = await adapter.discover()
    assert len(discovered) == 3

    # Verify lock capabilities require sensitive confirmation
    lock_ep = next(ep for ep in discovered if ep.raw_properties["entity_id"] == "lock.front_door")
    adapter.bind_device_entity("dev-lock-1", "lock.front_door")
    lock_caps = await adapter.get_capabilities("dev-lock-1")
    unlock_cap = next(c for c in lock_caps if c.capability_key == "lock.unlock")
    assert unlock_cap.requires_confirmation is True
    assert unlock_cap.requires_permission == "smarthome:sensitive"

    # Execute light turn_on and verify post-execution state readback
    adapter.bind_device_entity("dev-light-1", "light.studio_desk")
    receipt = await adapter.execute_command(
        "dev-light-1",
        AdapterCommand(
            command_id="cmd-ha-1",
            capability_key="power.set",
            parameters={"on": True, "brightness": 200},
        ),
    )
    assert receipt.acknowledged is True
    assert receipt.state_verified is True
    assert receipt.verification_method == "ha_entity_state_readback"
    assert receipt.result["new_state"] == "on"

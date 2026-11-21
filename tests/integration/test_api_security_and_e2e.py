import json
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from home_os.config import Settings
from home_os.main import create_app
from home_os.security.replay import sign_device_request


def test_full_api_security_auth_replay_permissions_and_lockdown(tmp_data_dir: Path):
    settings = Settings(
        data_dir=tmp_data_dir,
        auth_rate_limit_per_minute=5,
        rate_limit_requests_per_minute=200,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        # 1. Unauthenticated requests to protected endpoints MUST fail with 401
        assert client.get("/api/v1/devices").status_code == 401
        assert client.get("/api/v1/dashboard/summary").status_code == 401
        assert client.get("/api/v1/security/audit-logs").status_code == 401

        # 2. First-run status shows setup_required == True
        st = client.get("/api/v1/auth/status").json()
        assert st["setup_required"] is True

        # 3. Initialize owner account
        setup_resp = client.post(
            "/api/v1/auth/setup",
            json={"username": "commander", "display_name": "System Owner", "password": "UltraStrongPassword!2026"},
        )
        assert setup_resp.status_code == 200
        token = setup_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 4. Second setup attempt MUST be forbidden (403)
        dup_setup = client.post(
            "/api/v1/auth/setup",
            json={"username": "attacker", "display_name": "Attacker", "password": "UltraStrongPassword!2026"},
        )
        assert dup_setup.status_code == 403

        # 5. Create room & add manual unpaired device -> verify unpaired device rejects commands
        room_resp = client.post("/api/v1/rooms", headers=headers, json={"name": "Living Room", "floor": "Ground"})
        assert room_resp.status_code == 200
        room_id = room_resp.json()["room"]["id"]

        manual_dev = client.post(
            "/api/v1/devices/manual",
            headers=headers,
            json={"name": "Guest Smart Speaker", "category": "speaker", "ip_address": "192.168.1.44"},
        ).json()["device"]
        assert manual_dev["lifecycle_state"] == "identified"
        assert manual_dev["is_registered"] is False

        unpaired_cmd = client.post(
            f"/api/v1/devices/{manual_dev['id']}/commands",
            headers=headers,
            json={"capability_key": "volume.set", "parameters": {"level": 50}},
        )
        assert unpaired_cmd.status_code == 403
        assert unpaired_cmd.json()["detail"]["code"] == "DEVICE_NOT_AUTHENTICATED"

        # 6. Pair an Android Companion device and test signed communication + replay attack blocking
        pair_init = client.post(
            "/api/v1/pairing/initiate",
            headers=headers,
            json={
                "adapter_type": "android_companion",
                "requested_permissions": ["telemetry:read", "notifications:read", "notifications:reply"],
            },
        ).json()

        pair_done = client.post(
            "/api/v1/pairing/complete",
            json={
                "pairing_session_id": pair_init["session_id"],
                "pin_or_token": pair_init["pin"],
                "hardware_fingerprint": "android-galaxy-s25-001",
                "name": "Galaxy S25 Ultra",
                "category": "smartphone",
                "manufacturer": "Samsung",
                "model": "SM-S938B",
                "os_name": "Android",
                "os_version": "15",
            },
        )
        assert pair_done.status_code == 200
        dev_creds = pair_done.json()["credentials"]
        dev_id = dev_creds["device_id"]

        # Assign device to Living Room
        patch_resp = client.patch(
            f"/api/v1/devices/{dev_id}",
            headers=headers,
            json={"room_id": room_id, "group_tags": ["personal", "mobile"]},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["device"]["room_name"] == "Living Room"

        # Signed WhatsApp Notification publish (legitimate NotificationListenerService mirror)
        comm_body = json.dumps(
            {
                "source_type": "whatsapp_notification",
                "app_name": "WhatsApp",
                "app_package": "com.whatsapp",
                "sender": "Adaeze",
                "title": "Adaeze",
                "body_preview": "Are we still meeting at 7pm?",
                "can_reply": True,
                "reply_action_id": "notif-action-wa-99",
                "external_id": "sbn-wa-99",
            }
        ).encode("utf-8")
        ts = int(time.time())
        nonce = "unique-comm-nonce-0001"
        sig = sign_device_request(
            secret=dev_creds["hmac_secret"],
            method="POST",
            path="/api/v1/agent/communications",
            timestamp=ts,
            nonce=nonce,
            body=comm_body,
        )
        dev_headers = {
            "Content-Type": "application/json",
            "X-Device-ID": dev_id,
            "X-Device-Token": dev_creds["device_token"],
            "X-Timestamp": str(ts),
            "X-Nonce": nonce,
            "X-Signature": sig,
        }
        comm_resp = client.post("/api/v1/agent/communications", content=comm_body, headers=dev_headers)
        assert comm_resp.status_code == 200
        assert comm_resp.json()["access_level"] == "notification_only"

        # Replay of exact same signed request MUST be blocked with 401!
        replay_resp = client.post("/api/v1/agent/communications", content=comm_body, headers=dev_headers)
        assert replay_resp.status_code == 401

        # Verify Communication Centre lists item with notification_only transparency
        inbox = client.get("/api/v1/communications", headers=headers).json()["items"]
        assert len(inbox) == 1
        assert inbox[0]["access_level"] == "notification_only"
        assert inbox[0]["sender"] == "Adaeze"

        # Revoke notifications:read permission -> subsequent signed notification publish MUST be denied (403)
        rev_resp = client.post(
            f"/api/v1/devices/{dev_id}/permissions",
            headers=headers,
            json={"permission_scope": "notifications:read", "status": "revoked", "reason": "Privacy pause"},
        )
        assert rev_resp.status_code == 200

        nonce2 = "unique-comm-nonce-0002"
        sig2 = sign_device_request(
            secret=dev_creds["hmac_secret"],
            method="POST",
            path="/api/v1/agent/communications",
            timestamp=ts,
            nonce=nonce2,
            body=comm_body,
        )
        dev_headers["X-Nonce"] = nonce2
        dev_headers["X-Signature"] = sig2
        denied_comm = client.post("/api/v1/agent/communications", content=comm_body, headers=dev_headers)
        assert denied_comm.status_code == 403

        # 7. Verify Emergency Lockdown isolates all enrolled devices
        lockdown = client.post("/api/v1/security/emergency-lockdown", headers=headers)
        assert lockdown.status_code == 200
        assert lockdown.json()["devices_isolated"] >= 1

        detail = client.get(f"/api/v1/devices/{dev_id}", headers=headers).json()["device"]
        assert detail["lifecycle_state"] == "isolated"

        # 8. Verify Cryptographic Audit Chain Integrity across all actions & attacks
        chain = client.get("/api/v1/security/audit-verify", headers=headers).json()
        assert chain["valid"] is True
        assert chain["checked_entries"] >= 8

        # 9. Verify login brute-force rate limiter triggers 429
        for _ in range(5):
            client.post("/api/v1/auth/login", json={"username": "commander", "password": "wrong"})
        rate_limited = client.post("/api/v1/auth/login", json={"username": "commander", "password": "wrong"})
        assert rate_limited.status_code == 429

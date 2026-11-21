import asyncio
import http.server
import socket
import threading
from pathlib import Path
import pytest

from home_os.db.models import Base, Device, User
from home_os.db.session import create_db_engine_and_session
from home_os.security.crypto import CredentialVault, ensure_local_tls_certificate, hash_password
from home_os.services.discovery import DiscoveryService, parse_ssdp_response, lookup_mac_vendor
from home_os.services.event_bus import EventBus
from home_os.services.pairing import PairingError, PairingService


UPNP_SAMPLE_XML = b"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
    <friendlyName>Living Room Sonos Era 100</friendlyName>
    <manufacturer>Sonos, Inc.</manufacturer>
    <modelName>Sonos Era 100</modelName>
    <UDN>uuid:RINCON_5CAAFD00112201400</UDN>
  </device>
</root>
"""


class _QuietXmlHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(UPNP_SAMPLE_XML)))
        self.end_headers()
        self.wfile.write(UPNP_SAMPLE_XML)

    def log_message(self, format, *args):
        return


@pytest.mark.asyncio
async def test_ssdp_discovery_parses_upnp_xml_and_keeps_device_unpaired(tmp_data_dir: Path):
    # Start local UPnP XML HTTP server
    xml_server = http.server.HTTPServer(("127.0.0.1", 0), _QuietXmlHandler)
    xml_port = xml_server.server_address[1]
    xml_thread = threading.Thread(target=xml_server.serve_forever, daemon=True)
    xml_thread.start()

    # Start local UDP SSDP responder
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.bind(("127.0.0.1", 0))
    ssdp_port = udp_sock.getsockname()[1]

    def _ssdp_responder():
        udp_sock.settimeout(2.0)
        try:
            _data, addr = udp_sock.recvfrom(2048)
            reply = (
                "HTTP/1.1 200 OK\r\n"
                f"LOCATION: http://127.0.0.1:{xml_port}/desc.xml\r\n"
                "ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
                "USN: uuid:RINCON_5CAAFD00112201400::urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
                "SERVER: Linux UPnP/1.0 Sonos/79.1\r\n\r\n"
            ).encode("utf-8")
            udp_sock.sendto(reply, addr)
        except TimeoutError:
            pass
        finally:
            udp_sock.close()

    resp_thread = threading.Thread(target=_ssdp_responder, daemon=True)
    resp_thread.start()

    engine, SessionLocal = create_db_engine_and_session(f"sqlite:///{tmp_data_dir / 'disc.db'}")
    Base.metadata.create_all(bind=engine)
    bus = EventBus()
    discovery = DiscoveryService(event_bus=bus)

    try:
        endpoints = await discovery.scan_ssdp(
            target=("127.0.0.1", ssdp_port),
            timeout=1.0,
        )
        assert len(endpoints) == 1
        ep = endpoints[0]
        assert ep.raw_properties["friendly_name"] == "Living Room Sonos Era 100"
        assert ep.raw_properties["manufacturer"] == "Sonos, Inc."

        with SessionLocal() as db:
            ingested = await discovery.ingest_discovered_endpoints(db, endpoints)
            assert len(ingested) == 1
            dev = ingested[0]
            # CRITICAL REQUIREMENT: Discovering a device MUST NEVER grant private access or mark authenticated!
            assert dev.lifecycle_state in ("discovered", "identified")
            assert dev.connection_status == "unpaired"
            assert dev.credential is None
            assert len(dev.permissions) == 0
    finally:
        xml_server.shutdown()


def test_xxe_attack_in_upnp_xml_is_blocked():
    malicious_xml = b"""<?xml version="1.0"?>
    <!DOCTYPE foo [
      <!ELEMENT foo ANY >
      <!ENTITY xxe SYSTEM "file:///etc/passwd" >]>
    <root><device><friendlyName>&xxe;</friendlyName></device></root>
    """
    from home_os.services.discovery import parse_upnp_description_xml

    parsed = parse_upnp_description_xml(malicious_xml)
    assert parsed == {}


def test_mac_oui_vendor_lookup():
    assert lookup_mac_vendor("3C:22:FB:11:22:33") == "Apple"
    assert lookup_mac_vendor("f0:18:98:1:2:a") == "Apple"
    assert lookup_mac_vendor("DC:A6:32:AA:BB:CC") == "Raspberry Pi"


def test_macos_arp_table_parsing_discovers_wifi_peers_and_skips_loopback():
    from home_os.services.discovery import classify_discovered_endpoint, parse_macos_arp_output

    sample_arp = """
router.local (192.168.1.1) at 4:d9:f5:a:1b:2 on en0 ifscope [ethernet]
? (192.168.1.24) at f0:18:98:3:c:99 on en0 ifscope [ethernet]
? (192.168.1.55) at 24:fd:5b:10:20:30 on en0 ifscope [ethernet]
? (192.168.1.99) at (incomplete) on en0 ifscope [ethernet]
localhost (127.0.0.1) at 3c:22:fb:0:0:1 on lo0 ifscope permanent [ethernet]
? (224.0.0.251) at 1:0:5e:0:0:fb on en0 ifscope permanent [ethernet]
? (192.168.1.255) at ff:ff:ff:ff:ff:ff on en0 ifscope [ethernet]
"""
    eps = parse_macos_arp_output(sample_arp, local_ips={"127.0.0.1", "192.168.1.10"})
    assert len(eps) == 3
    by_ip = {e.ip_address: e for e in eps}
    assert by_ip["192.168.1.1"].mac_address == "04:D9:F5:0A:1B:02"
    assert by_ip["192.168.1.24"].mac_address == "F0:18:98:03:0C:99"
    assert classify_discovered_endpoint(by_ip["192.168.1.24"])["manufacturer"] == "Apple"
    assert classify_discovered_endpoint(by_ip["192.168.1.55"])["manufacturer"] == "Samsung"


def test_mobile_qr_companion_page_serves_and_expires_after_use(tmp_data_dir: Path):
    from fastapi.testclient import TestClient
    from home_os.config import Settings
    from home_os.main import create_app

    app = create_app(Settings(data_dir=tmp_data_dir))
    with TestClient(app) as client:
        setup_res = client.post(
            "/api/v1/auth/setup",
            json={"username": "owner", "display_name": "Owner", "password": "StrongPassword123!"},
        )
        assert setup_res.status_code == 200
        token = setup_res.json()["access_token"]

        init_res = client.post(
            "/api/v1/pairing/initiate",
            headers={"X-HomeOS-Token": token},
            json={"adapter_type": "ios_companion", "requested_permissions": ["telemetry:read"]},
        )
        assert init_res.status_code == 200
        qr_uri = init_res.json()["qr_uri"]
        qr_path = "/" + qr_uri.split("/", 3)[3]

        page_res = client.get(qr_path)
        assert page_res.status_code == 200
        assert "HOME OS Companion Pairing" in page_res.text
        assert "Approve &amp; Connect This Device" in page_res.text


@pytest.mark.asyncio
async def test_secure_qr_and_pin_pairing_enrollment_and_rotation(tmp_data_dir: Path):
    engine, SessionLocal = create_db_engine_and_session(f"sqlite:///{tmp_data_dir / 'pair.db'}")
    Base.metadata.create_all(bind=engine)
    vault = CredentialVault(tmp_data_dir / "master.key")
    tls_info = ensure_local_tls_certificate(tmp_data_dir / "tls")
    bus = EventBus()
    pairing = PairingService(vault=vault, event_bus=bus, tls_fingerprint=tls_info.sha256_fingerprint)

    with SessionLocal() as db:
        owner = User(
            id="u-1",
            username="admin",
            display_name="Admin",
            password_hash=hash_password("SuperSecurePass!1"),
            role="owner",
            is_active=True,
        )
        db.add(owner)
        db.commit()

        initiation = pairing.initiate_pairing(
            db=db,
            user_id="u-1",
            adapter_type="android_companion",
            hub_host="192.168.1.10",
            hub_port=8420,
            requested_permissions=["telemetry:read", "notifications:read", "flashlight:control"],
        )
        assert len(initiation.pin) == 6
        assert initiation.qr_uri.startswith("http://192.168.1.10:8420/p/")
        assert initiation.deep_link_uri.startswith("homeos://pair?")
        assert "<svg" in initiation.qr_svg
        assert 'shape-rendering="crispEdges"' in initiation.qr_svg

        # Wrong PIN must fail
        with pytest.raises(PairingError):
            await pairing.complete_pairing(
                db=db,
                pairing_session_id=initiation.session_id,
                pin_or_token="000000" if initiation.pin != "000000" else "999999",
                hardware_fingerprint="android-hw-pixel9-001",
                name="Pixel 9 Pro",
                category="smartphone",
                manufacturer="Google",
                model="Pixel 9 Pro",
                os_name="Android",
                os_version="15",
            )

        # Valid QR token completes enrollment and issues non-plaintext credentials
        enrolled = await pairing.complete_pairing(
            db=db,
            pairing_session_id=initiation.session_id,
            pin_or_token=initiation.qr_token,
            hardware_fingerprint="android-hw-pixel9-001",
            name="Pixel 9 Pro",
            category="smartphone",
            manufacturer="Google",
            model="Pixel 9 Pro",
            os_name="Android",
            os_version="15",
        )
        assert enrolled.device.lifecycle_state == "authenticated"
        assert len(enrolled.device_token) > 24
        assert len(enrolled.hmac_secret) > 24

        # Verify DB has NO plaintext token or HMAC secret
        cred = enrolled.device.credential
        assert cred.token_hash != enrolled.device_token
        assert enrolled.hmac_secret not in cred.encrypted_hmac_secret
        assert vault.decrypt(cred.encrypted_hmac_secret, aad=f"device:{enrolled.device.id}") == enrolled.hmac_secret

        # Replay of single-use pairing session must fail
        with pytest.raises(PairingError):
            await pairing.complete_pairing(
                db=db,
                pairing_session_id=initiation.session_id,
                pin_or_token=initiation.qr_token,
                hardware_fingerprint="android-hw-clone",
                name="Clone",
                category="smartphone",
            )

        # Credential rotation increments version and invalidates old token
        rotated = pairing.rotate_credentials(db=db, device_id=enrolled.device.id, actor_user_id="u-1")
        assert rotated.key_version == 2
        assert rotated.device_token != enrolled.device_token


def test_virtual_vpn_interfaces_filtered_in_favor_of_physical_wifi(monkeypatch):
    from types import SimpleNamespace
    import home_os.services.discovery as disc

    fake_adapters = [
        SimpleNamespace(
            name="utun4",
            ips=[SimpleNamespace(ip="10.8.0.2", network_prefix=24)],
        ),
        SimpleNamespace(
            name="awdl0",
            ips=[SimpleNamespace(ip="169.254.12.34", network_prefix=16)],
        ),
        SimpleNamespace(
            name="en0",
            ips=[SimpleNamespace(ip="192.168.1.42", network_prefix=24)],
        ),
    ]
    monkeypatch.setattr(disc, "_get_macos_physical_wifi_ips", lambda: [])
    monkeypatch.setattr(disc.ifaddr, "get_adapters", lambda: fake_adapters)

    ifaces = disc.list_lan_interfaces()
    assert len(ifaces) == 1
    assert ifaces[0]["interface"] == "en0"
    assert ifaces[0]["ip"] == "192.168.1.42"
    assert disc.get_primary_lan_ipv4() == "192.168.1.42"


def test_lan_and_cgnat_trust_and_network_diagnostics(tmp_data_dir: Path):
    from fastapi.testclient import TestClient
    from home_os.config import Settings
    from home_os.main import _is_trusted_local_or_lan_client, create_app

    assert _is_trusted_local_or_lan_client("192.168.1.50") is True
    assert _is_trusted_local_or_lan_client("172.20.10.1") is True
    assert _is_trusted_local_or_lan_client("100.64.12.9") is True
    assert _is_trusted_local_or_lan_client("8.8.8.8") is False

    app = create_app(Settings(data_dir=tmp_data_dir))
    with TestClient(app) as client:
        setup_res = client.post(
            "/api/v1/auth/setup",
            json={"username": "owner", "display_name": "Owner", "password": "StrongPassword123!"},
        )
        token = setup_res.json()["access_token"]

        diag_res = client.get("/api/v1/system/network-diagnostics", headers={"X-HomeOS-Token": token})
        assert diag_res.status_code == 200
        diag = diag_res.json()
        assert "primary_lan_ip" in diag
        assert "lan_socket_reachable" in diag
        assert "lan_interfaces" in diag

        # Explicit LAN IP override works while public IP override is rejected
        override_res = client.post(
            "/api/v1/pairing/initiate",
            headers={"X-HomeOS-Token": token},
            json={"adapter_type": "ios_companion", "hub_host": "192.168.8.105"},
        )
        assert override_res.status_code == 200
        assert override_res.json()["lan_host"] == "192.168.8.105"
        assert "http://192.168.8.105:" in override_res.json()["qr_uri"]

        unsafe_res = client.post(
            "/api/v1/pairing/initiate",
            headers={"X-HomeOS-Token": token},
            json={"adapter_type": "ios_companion", "hub_host": "8.8.8.8"},
        )
        assert unsafe_res.status_code == 200
        assert unsafe_res.json()["lan_host"] != "8.8.8.8"


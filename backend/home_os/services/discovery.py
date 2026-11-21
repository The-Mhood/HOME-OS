import asyncio
import hashlib
import ipaddress
import json
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import defusedxml.ElementTree as SafeET
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from home_os.adapters.base import DiscoveredEndpoint
from home_os.db.models import Device, DeviceCapability
from home_os.security.audit import append_audit_log
from home_os.services.event_bus import EventBus

MDNS_SERVICE_TYPES = [
    "_homeos._tcp.local.",
    "_androidtvremote2._tcp.local.",
    "_googlecast._tcp.local.",
    "_home-assistant._tcp.local.",
    "_matter._tcp.local.",
    "_matterc._udp.local.",
    "_hap._tcp.local.",
    "_airplay._tcp.local.",
    "_companion-link._tcp.local.",
]

OUI_VENDOR_PREFIXES: dict[str, str] = {
    "3C:22:FB": "Apple",
    "F0:18:98": "Apple",
    "BC:D0:74": "Apple",
    "DC:A6:32": "Raspberry Pi",
    "B8:27:EB": "Raspberry Pi",
    "E4:5F:01": "Raspberry Pi",
    "5C:AA:FD": "Sonos",
    "78:28:CA": "Sonos",
    "3C:5A:B4": "Google",
    "F4:F5:D8": "Google",
    "A4:77:33": "Google",
    "24:FD:5B": "Samsung",
    "8C:79:F5": "Samsung",
    "00:17:88": "Philips Hue (Signify)",
    "EC:B5:FA": "Philips Hue (Signify)",
    "24:0A:C4": "Espressif",
    "30:AE:A4": "Espressif",
    "FC:65:DE": "Amazon",
    "44:65:0D": "Amazon",
    "00:04:4B": "NVIDIA",
    "AC:9B:0A": "Sony",
    "38:8C:50": "LG Electronics",
}


def lookup_mac_vendor(mac: str | None) -> str | None:
    if not mac:
        return None
    normalized = mac.strip().upper().replace("-", ":")
    parts = normalized.split(":")
    if len(parts) >= 3:
        prefix = ":".join(parts[:3])
        return OUI_VENDOR_PREFIXES.get(prefix)
    return None


def _is_safe_lan_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def parse_ssdp_response(raw: bytes) -> dict[str, str]:
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().upper()] = v.strip()
    return headers


def parse_upnp_description_xml(xml_bytes: bytes) -> dict[str, str]:
    """Safely parses UPnP device description XML using defusedxml (blocks XXE & entity expansion)."""
    try:
        root = SafeET.fromstring(xml_bytes[:65536])
    except Exception:
        return {}

    result: dict[str, str] = {}
    tag_map = {
        "friendlyName": "friendly_name",
        "manufacturer": "manufacturer",
        "modelName": "model_name",
        "deviceType": "device_type",
        "UDN": "udn",
    }
    for elem in root.iter():
        local_name = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
        if local_name in tag_map and elem.text and local_name not in result:
            result[tag_map[local_name]] = elem.text.strip()[:160]
    return result


def classify_discovered_endpoint(ep: DiscoveredEndpoint) -> dict[str, Any]:
    props = ep.raw_properties or {}
    svc = (ep.service_type or "").lower()
    st = str(props.get("st", "")).lower()
    friendly = props.get("friendly_name") or props.get("name") or ep.hostname or ep.ip_address or "Unknown Network Device"
    vendor = props.get("manufacturer") or lookup_mac_vendor(ep.mac_address)
    model = props.get("model_name") or props.get("md")

    category = "network"
    adapter_type = "generic_network"
    os_name = None

    if "_homeos._tcp" in svc:
        platform = str(props.get("platform", "linux")).lower()
        if platform == "android":
            category, adapter_type, os_name = "smartphone", "android_companion", "Android"
        elif platform == "ios":
            category, adapter_type, os_name = "smartphone", "ios_companion", "iOS"
        elif platform == "windows":
            category, adapter_type, os_name = "desktop", "windows_agent", "Windows"
        elif platform == "macos":
            category, adapter_type, os_name = "laptop", "macos_agent", "macOS"
        else:
            category, adapter_type, os_name = "desktop", "linux_agent", "Linux"
    elif "_androidtvremote2._tcp" in svc or "androidtv" in st:
        category, adapter_type, os_name = "tv", "google_tv", "Android TV"
        vendor = vendor or "Google"
    elif "_googlecast._tcp" in svc:
        category, adapter_type = ("tv" if "tv" in str(model or friendly).lower() else "speaker"), "google_tv"
        vendor = vendor or "Google"
    elif "_home-assistant._tcp" in svc:
        category, adapter_type = "smarthome", "home_assistant"
        vendor = vendor or "Home Assistant"
    elif "_matter._tcp" in svc or "_matterc._udp" in svc:
        category, adapter_type = "smarthome", "matter"
    elif "_hap._tcp" in svc or "_airplay._tcp" in svc or "_companion-link._tcp" in svc:
        category = "smarthome" if "_hap._tcp" in svc else "smartphone"
        adapter_type = "generic_network"
        vendor = vendor or "Apple"
    elif "mediarenderer" in st or "sonos" in str(vendor or "").lower():
        category = "speaker"
        adapter_type = "generic_network"

    return {
        "name": friendly,
        "category": category,
        "manufacturer": vendor,
        "model": model,
        "os_name": os_name,
        "adapter_type": adapter_type,
        "identified": adapter_type != "generic_network" or bool(vendor or model),
    }


class DiscoveryService:
    """Multi-protocol LAN discovery (mDNS/DNS-SD, SSDP/UPnP, ARP/Neighbour, and manual probes)."""

    def __init__(self, event_bus: EventBus, http_client: httpx.AsyncClient | None = None):
        self.event_bus = event_bus
        self._http_client = http_client

    async def scan_all(
        self,
        *,
        db: Session,
        mdns_timeout: float = 1.2,
        ssdp_timeout: float = 1.0,
        actor_user_id: str | None = None,
    ) -> list[Device]:
        mdns_task = asyncio.create_task(self.scan_mdns(timeout=mdns_timeout))
        ssdp_task = asyncio.create_task(self.scan_ssdp(timeout=ssdp_timeout))
        arp_task = asyncio.to_thread(self.scan_arp_and_interfaces)

        results = await asyncio.gather(mdns_task, ssdp_task, arp_task, return_exceptions=True)
        endpoints: list[DiscoveredEndpoint] = []
        for batch in results:
            if isinstance(batch, list):
                endpoints.extend(batch)

        devices = await self.ingest_discovered_endpoints(db, endpoints, actor_user_id=actor_user_id)
        return devices

    async def scan_ssdp(
        self,
        *,
        target: tuple[str, int] = ("239.255.255.250", 1900),
        timeout: float = 1.2,
    ) -> list[DiscoveredEndpoint]:
        m_search = (
            "M-SEARCH * HTTP/1.1\r\n"
            f"HOST: {target[0]}:{target[1]}\r\n"
            'MAN: "ssdp:discover"\r\n'
            "MX: 1\r\n"
            "ST: ssdp:all\r\n\r\n"
        ).encode("ascii")

        raw_packets: list[tuple[bytes, tuple[str, int]]] = []

        def _udp_exchange():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(0.25)
            try:
                sock.sendto(m_search, target)
                deadline = asyncio.get_event_loop  # unused in thread
                import time as _t
                end_time = _t.monotonic() + timeout
                while _t.monotonic() < end_time:
                    remaining = end_time - _t.monotonic()
                    if remaining <= 0:
                        break
                    sock.settimeout(min(0.25, remaining))
                    try:
                        data, addr = sock.recvfrom(4096)
                        raw_packets.append((data, addr))
                    except TimeoutError:
                        continue
            except OSError:
                return
            finally:
                sock.close()

        await asyncio.to_thread(_udp_exchange)

        seen_fps: set[str] = set()
        endpoints: list[DiscoveredEndpoint] = []

        async with httpx.AsyncClient(timeout=1.5, follow_redirects=False) as default_client:
            client = self._http_client or default_client
            for packet, (ip_addr, port) in raw_packets:
                headers = parse_ssdp_response(packet)
                usn = headers.get("USN") or f"ssdp:{ip_addr}:{port}"
                fp = "ssdp-" + hashlib.sha256(usn.encode("utf-8")).hexdigest()[:24]
                if fp in seen_fps:
                    continue
                seen_fps.add(fp)

                props: dict[str, Any] = {
                    "usn": usn,
                    "st": headers.get("ST", ""),
                    "server": headers.get("SERVER", ""),
                    "location": headers.get("LOCATION", ""),
                }

                loc = headers.get("LOCATION", "")
                if loc.startswith(("http://", "https://")):
                    parsed = urlparse(loc)
                    if parsed.hostname and _is_safe_lan_ip(parsed.hostname):
                        try:
                            resp = await client.get(loc)
                            if resp.status_code == 200 and resp.content:
                                props.update(parse_upnp_description_xml(resp.content))
                        except Exception:
                            pass

                endpoints.append(
                    DiscoveredEndpoint(
                        hardware_fingerprint=fp,
                        discovery_source="ssdp",
                        ip_address=ip_addr,
                        port=port,
                        service_type=headers.get("ST"),
                        raw_properties=props,
                    )
                )
        return endpoints

    async def scan_mdns(self, timeout: float = 1.2) -> list[DiscoveredEndpoint]:
        discovered: list[DiscoveredEndpoint] = []
        found_services: list[tuple[str, str]] = []

        def _on_service_state_change(zeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
            if state_change is ServiceStateChange.Added:
                found_services.append((service_type, name))

        try:
            azc = AsyncZeroconf()
        except Exception:
            return []

        try:
            browser = AsyncServiceBrowser(azc.zeroconf, MDNS_SERVICE_TYPES, handlers=[_on_service_state_change])
            await asyncio.sleep(timeout)
            await browser.async_cancel()

            for svc_type, svc_name in found_services:
                info = AsyncServiceInfo(svc_type, svc_name)
                await info.async_request(azc.zeroconf, 800)
                addrs = info.parsed_scoped_addresses()
                ip_addr = addrs[0] if addrs else None
                txt_props: dict[str, Any] = {"name": svc_name.removesuffix(f".{svc_type}")}
                for k, v in (info.properties or {}).items():
                    key_str = k.decode("utf-8", errors="ignore") if isinstance(k, bytes) else str(k)
                    val_str = v.decode("utf-8", errors="ignore") if isinstance(v, bytes) else str(v)
                    txt_props[key_str] = val_str

                fp_seed = txt_props.get("id") or txt_props.get("device_id") or f"{svc_type}:{svc_name}"
                fp = "mdns-" + hashlib.sha256(str(fp_seed).encode("utf-8")).hexdigest()[:24]
                discovered.append(
                    DiscoveredEndpoint(
                        hardware_fingerprint=fp,
                        discovery_source="mdns",
                        ip_address=ip_addr,
                        port=info.port,
                        hostname=info.server.rstrip(".") if info.server else None,
                        service_type=svc_type,
                        raw_properties=txt_props,
                    )
                )
        except Exception:
            pass
        finally:
            try:
                await azc.async_close()
            except Exception:
                pass
        return discovered

    def scan_arp_and_interfaces(self) -> list[DiscoveredEndpoint]:
        endpoints: list[DiscoveredEndpoint] = []
        seen: set[str] = set()

        # 1. Read /proc/net/arp if on Linux
        arp_file = Path("/proc/net/arp")
        if arp_file.exists():
            try:
                lines = arp_file.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]
                for line in lines:
                    cols = line.split()
                    if len(cols) >= 6:
                        ip_addr, _hw_type, flags, mac, _mask, iface = cols[:6]
                        if mac == "00:00:00:00:00:00" or flags == "0x0":
                            continue
                        fp = f"arp-{mac.lower()}"
                        if fp in seen:
                            continue
                        seen.add(fp)
                        endpoints.append(
                            DiscoveredEndpoint(
                                hardware_fingerprint=fp,
                                discovery_source="arp",
                                ip_address=ip_addr,
                                mac_address=mac.upper(),
                                raw_properties={"interface": iface},
                            )
                        )
            except OSError:
                pass

        # 2. Inspect `ip -j neigh` for IPv4/IPv6 neighbors
        try:
            proc = subprocess.run(
                ["ip", "-j", "neigh"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                entries = json.loads(proc.stdout)
                for item in entries:
                    mac = item.get("lladdr")
                    dst = item.get("dst")
                    if not dst or not mac or mac == "00:00:00:00:00:00":
                        continue
                    fp = f"arp-{mac.lower()}"
                    if fp in seen:
                        continue
                    seen.add(fp)
                    endpoints.append(
                        DiscoveredEndpoint(
                            hardware_fingerprint=fp,
                            discovery_source="arp",
                            ip_address=dst,
                            mac_address=mac.upper(),
                            raw_properties={"interface": item.get("dev", ""), "nud_state": item.get("state", [])},
                        )
                    )
        except Exception:
            pass

        return endpoints

    async def ingest_discovered_endpoints(
        self,
        db: Session,
        endpoints: list[DiscoveredEndpoint],
        *,
        actor_user_id: str | None = None,
    ) -> list[Device]:
        touched: list[Device] = []
        now = datetime.now(timezone.utc)

        for ep in endpoints:
            classified = classify_discovered_endpoint(ep)
            existing = db.execute(
                select(Device).where(Device.hardware_fingerprint == ep.hardware_fingerprint)
            ).scalar_one_or_none()

            if existing is None and ep.ip_address:
                # Match existing enrolled device on same IP & adapter if fingerprint matches metadata
                pass

            if existing is None:
                lifecycle = "identified" if classified["identified"] else "discovered"
                device = Device(
                    hardware_fingerprint=ep.hardware_fingerprint,
                    name=classified["name"],
                    category=classified["category"],
                    manufacturer=classified["manufacturer"],
                    model=classified["model"],
                    os_name=classified["os_name"],
                    ip_address=ep.ip_address,
                    mac_address=ep.mac_address,
                    port=ep.port,
                    hostname=ep.hostname,
                    lifecycle_state=lifecycle,
                    discovery_source=ep.discovery_source,
                    adapter_type=classified["adapter_type"],
                    connection_status="unpaired",
                    integration_health="unverified_until_paired",
                    metadata_json={
                        "service_type": ep.service_type,
                        "discovery_properties": ep.raw_properties,
                    },
                    first_seen_at=now,
                    last_seen_at=now,
                )
                db.add(device)
                db.flush()

                # Add honest placeholder capability explaining unpaired status
                db.add(
                    DeviceCapability(
                        device_id=device.id,
                        capability_key="control.unpaired",
                        category="system",
                        display_name="Remote Control Unavailable Until Paired",
                        description="Discovered on local network; enroll and grant permissions to inspect capabilities.",
                        supported=False,
                        unavailable_reason="Device is discovered but not enrolled or authenticated with HOME OS.",
                        requires_permission="telemetry:read",
                    )
                )
                touched.append(device)
            else:
                existing.last_seen_at = now
                if ep.ip_address:
                    existing.ip_address = ep.ip_address
                if ep.port:
                    existing.port = ep.port
                if ep.mac_address and not existing.mac_address:
                    existing.mac_address = ep.mac_address
                # Never downgrade an enrolled/authenticated/isolated device during passive discovery!
                if existing.lifecycle_state == "discovered" and classified["identified"]:
                    existing.lifecycle_state = "identified"
                touched.append(existing)

        append_audit_log(
            db,
            severity="info",
            category="discovery",
            event_type="discovery.scan_completed",
            actor_type="user" if actor_user_id else "system",
            actor_id=actor_user_id,
            outcome="success",
            details={"discovered_count": len(endpoints), "inventory_updated": len(touched)},
        )
        db.commit()

        await self.event_bus.publish(
            "discovery.completed",
            {"count": len(touched), "timestamp": now.isoformat()},
        )
        return touched

import asyncio
import hashlib
import ipaddress
import json
import re
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import defusedxml.ElementTree as SafeET
import httpx
import ifaddr
from sqlalchemy import select
from sqlalchemy.orm import Session
from zeroconf import InterfaceChoice, ServiceStateChange
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
    "_raop._tcp.local.",
    "_companion-link._tcp.local.",
    "_spotify-connect._tcp.local.",
    "_sonos._tcp.local.",
    "_hue._tcp.local.",
    "_esphomelib._tcp.local.",
    "_ipp._tcp.local.",
    "_smb._tcp.local.",
]

DISCOVERY_TCP_PORTS = (62078, 6466, 8009, 7000, 8123, 1400, 8001, 80)

OUI_VENDOR_PREFIXES: dict[str, str] = {
    # Apple
    "3C:22:FB": "Apple", "F0:18:98": "Apple", "BC:D0:74": "Apple", "A4:83:E7": "Apple",
    "DC:A9:04": "Apple", "AC:BC:32": "Apple", "F8:FF:C2": "Apple", "D0:81:7A": "Apple",
    "60:F8:1D": "Apple", "14:7D:DA": "Apple", "28:CF:E9": "Apple", "7C:D1:C3": "Apple",
    # Samsung
    "24:FD:5B": "Samsung", "8C:79:F5": "Samsung", "50:01:BB": "Samsung", "BC:14:85": "Samsung",
    "CC:07:AB": "Samsung", "E4:7C:F9": "Samsung", "78:47:1D": "Samsung", "D0:22:BE": "Samsung",
    # Google / Nest
    "3C:5A:B4": "Google", "F4:F5:D8": "Google", "A4:77:33": "Google", "54:60:09": "Google",
    "D8:6C:63": "Google", "E4:F0:42": "Google",
    # Sony / LG / Roku / NVIDIA
    "AC:9B:0A": "Sony", "04:5D:4B": "Sony", "38:8C:50": "LG Electronics", "CC:2D:83": "LG Electronics",
    "B8:3E:59": "Roku", "D8:31:34": "Roku", "00:04:4B": "NVIDIA",
    # Sonos / Amazon / Philips Hue / Espressif / Raspberry Pi
    "5C:AA:FD": "Sonos", "78:28:CA": "Sonos", "94:9F:3E": "Sonos",
    "FC:65:DE": "Amazon", "44:65:0D": "Amazon", "68:54:FD": "Amazon",
    "00:17:88": "Philips Hue (Signify)", "EC:B5:FA": "Philips Hue (Signify)",
    "24:0A:C4": "Espressif", "30:AE:A4": "Espressif", "84:CC:A8": "Espressif",
    "DC:A6:32": "Raspberry Pi", "B8:27:EB": "Raspberry Pi", "E4:5F:01": "Raspberry Pi",
    # Xiaomi / TP-Link / Ubiquiti / Netgear / ASUS / Intel
    "64:CE:01": "Xiaomi", "28:6C:07": "Xiaomi", "50:C7:BF": "TP-Link", "B0:BE:76": "TP-Link",
    "78:8A:20": "Ubiquiti", "F0:9F:C2": "Ubiquiti", "9C:3D:CF": "Netgear",
    "04:D9:F5": "ASUS", "2C:FD:A1": "ASUS",
}

MACOS_ARP_LINE_RE = re.compile(
    r"^(?P<host>\S+)\s+\((?P<ip>\d+\.\d+\.\d+\.\d+)\)\s+at\s+(?P<mac>[0-9a-fA-F:]{11,17})\s+on\s+(?P<iface>\S+)"
)


def normalize_mac(mac: str | None) -> str | None:
    """Normalizes MAC addresses (including macOS single-digit hex octets like '0:1a:2b:3:4:5')."""
    if not mac:
        return None
    cleaned = mac.strip().lower().replace("-", ":")
    if cleaned in ("(incomplete)", "incomplete"):
        return None
    parts = cleaned.split(":")
    if len(parts) != 6:
        return None
    try:
        octets = [f"{int(p, 16):02X}" for p in parts]
    except ValueError:
        return None
    normalized = ":".join(octets)
    if normalized in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF"):
        return None
    # Skip IPv4/IPv6 multicast MACs (01:00:5E:xx and 33:33:xx)
    if normalized.startswith("01:00:5E:") or normalized.startswith("33:33:"):
        return None
    return normalized


def lookup_mac_vendor(mac: str | None) -> str | None:
    norm = normalize_mac(mac)
    if not norm:
        return None
    return OUI_VENDOR_PREFIXES.get(norm[:8])


def get_local_lan_ips() -> set[str]:
    ips: set[str] = {"127.0.0.1", "::1"}
    try:
        for adapter in ifaddr.get_adapters():
            for ip_info in adapter.ips:
                if isinstance(ip_info.ip, str):
                    ips.add(ip_info.ip)
                elif isinstance(ip_info.ip, tuple) and ip_info.ip:
                    ips.add(str(ip_info.ip[0]))
    except Exception:
        pass
    return ips


def get_primary_lan_ipv4() -> str:
    """Detects the primary active Wi-Fi / LAN IPv4 address of this host (e.g. 192.168.1.x on macOS en0)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("10.255.255.255", 1))
            candidate = sock.getsockname()[0]
            ip_obj = ipaddress.ip_address(candidate)
            if ip_obj.is_private and not ip_obj.is_loopback and not ip_obj.is_link_local:
                return candidate
        finally:
            sock.close()
    except OSError:
        pass

    try:
        for adapter in ifaddr.get_adapters():
            for ip_info in adapter.ips:
                if isinstance(ip_info.ip, str):
                    ip_obj = ipaddress.ip_address(ip_info.ip)
                    if isinstance(ip_obj, ipaddress.IPv4Address) and ip_obj.is_private and not ip_obj.is_loopback and not ip_obj.is_link_local:
                        return str(ip_obj)
    except Exception:
        pass

    return "127.0.0.1"


def get_active_lan_subnets() -> list[ipaddress.IPv4Network]:
    subnets: list[ipaddress.IPv4Network] = []
    seen: set[str] = set()
    primary_ip = get_primary_lan_ipv4()

    try:
        for adapter in ifaddr.get_adapters():
            for ip_info in adapter.ips:
                if not isinstance(ip_info.ip, str):
                    continue
                ip_obj = ipaddress.ip_address(ip_info.ip)
                if not isinstance(ip_obj, ipaddress.IPv4Address):
                    continue
                if ip_obj.is_loopback or ip_obj.is_link_local or not ip_obj.is_private:
                    continue
                # Cap large /16 or /8 private networks to the local /24 neighborhood for fast scanning
                prefix = max(24, int(ip_info.network_prefix or 24))
                net = ipaddress.IPv4Network(f"{ip_info.ip}/{prefix}", strict=False)
                if str(net) not in seen:
                    seen.add(str(net))
                    if ip_info.ip == primary_ip:
                        subnets.insert(0, net)
                    else:
                        subnets.append(net)
    except Exception:
        if primary_ip != "127.0.0.1":
            subnets.append(ipaddress.IPv4Network(f"{primary_ip}/24", strict=False))

    return subnets[:2]


def _is_usable_peer_ip(host: str | None, local_ips: set[str] | None = None) -> bool:
    if not host:
        return False
    clean_host = host.split("%", 1)[0].strip()
    if local_ips and clean_host in local_ips:
        return False
    try:
        ip = ipaddress.ip_address(clean_host)
        if ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_link_local:
            return False
        if isinstance(ip, ipaddress.IPv4Address) and clean_host.endswith(".255"):
            return False
        return ip.is_private
    except ValueError:
        return False


def _is_safe_lan_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def parse_macos_arp_output(output: str, local_ips: set[str] | None = None) -> list[DiscoveredEndpoint]:
    """Parses BSD/macOS `/usr/sbin/arp -an` neighbor entries into DiscoveredEndpoints."""
    endpoints: list[DiscoveredEndpoint] = []
    seen: set[str] = set()
    for line in output.splitlines():
        m = MACOS_ARP_LINE_RE.match(line.strip())
        if not m:
            continue
        ip_addr = m.group("ip")
        if not _is_usable_peer_ip(ip_addr, local_ips):
            continue
        norm_mac = normalize_mac(m.group("mac"))
        if not norm_mac:
            continue
        host_raw = m.group("host")
        hostname = None if host_raw == "?" else host_raw.rstrip(".")
        iface = m.group("iface")
        fp = f"arp-{norm_mac.lower()}"
        if fp in seen:
            continue
        seen.add(fp)
        endpoints.append(
            DiscoveredEndpoint(
                hardware_fingerprint=fp,
                discovery_source="arp",
                ip_address=ip_addr,
                mac_address=norm_mac,
                hostname=hostname,
                raw_properties={"interface": iface},
            )
        )
    return endpoints


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
    open_ports: list[int] = list(props.get("open_ports") or [])
    norm_mac = normalize_mac(ep.mac_address)
    vendor = props.get("manufacturer") or lookup_mac_vendor(norm_mac)
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
    elif "_androidtvremote2._tcp" in svc or "androidtv" in st or 6466 in open_ports:
        category, adapter_type, os_name = "tv", "google_tv", "Android TV"
        vendor = vendor or "Google"
    elif "_googlecast._tcp" in svc or 8009 in open_ports:
        category, adapter_type = ("tv" if "tv" in str(model or "").lower() else "speaker"), "google_tv"
        vendor = vendor or "Google"
    elif "_home-assistant._tcp" in svc or 8123 in open_ports:
        category, adapter_type = "smarthome", "home_assistant"
        vendor = vendor or "Home Assistant"
    elif "_matter._tcp" in svc or "_matterc._udp" in svc:
        category, adapter_type = "smarthome", "matter"
    elif 62078 in open_ports or "_companion-link._tcp" in svc:
        category = "smartphone"
        adapter_type = "ios_companion"
        vendor = vendor or "Apple"
        os_name = "iOS"
    elif "_hap._tcp" in svc or "_hue._tcp" in svc or "_esphomelib._tcp" in svc:
        category = "smarthome"
        adapter_type = "generic_network"
    elif "_airplay._tcp" in svc or "_raop._tcp" in svc or 7000 in open_ports:
        category = "tv" if "tv" in str(model or ep.hostname or "").lower() else "speaker"
        vendor = vendor or "Apple"
    elif "_sonos._tcp" in svc or "_spotify-connect._tcp" in svc or 1400 in open_ports or "mediarenderer" in st or "sonos" in str(vendor or "").lower():
        category = "speaker"
    elif 8001 in open_ports or vendor in ("Sony", "LG Electronics", "Roku"):
        category = "tv"
    elif "_smb._tcp" in svc:
        category = "desktop"
    elif vendor in ("Apple", "Samsung", "Xiaomi"):
        category = "smartphone"

    # Build a meaningful display name when hostname isn't advertised
    raw_name = props.get("friendly_name") or props.get("name") or ep.hostname
    if raw_name:
        friendly = str(raw_name)
    elif ep.ip_address and ep.ip_address.endswith((".1", ".254")) and category == "network":
        friendly = f"{vendor + ' ' if vendor else ''}Wi-Fi Router ({ep.ip_address})"
    elif vendor:
        label_map = {
            "smartphone": "Mobile Device",
            "tv": "Smart TV",
            "speaker": "Wireless Speaker",
            "smarthome": "Smart Home Device",
            "desktop": "Computer",
            "laptop": "Laptop",
            "network": "Wi-Fi Device",
        }
        friendly = f"{vendor} {label_map.get(category, 'Device')} ({ep.ip_address or norm_mac})"
    else:
        friendly = f"LAN Device ({ep.ip_address or norm_mac or 'Unknown'})"

    return {
        "name": friendly,
        "category": category,
        "manufacturer": vendor,
        "model": model,
        "os_name": os_name,
        "adapter_type": adapter_type,
        "identified": adapter_type != "generic_network" or bool(vendor or model or raw_name),
    }


class DiscoveryService:
    """Multi-protocol Wi-Fi / LAN discovery (Active Subnet ARP Warm-up, macOS/Linux ARP, mDNS, SSDP/UPnP)."""

    def __init__(self, event_bus: EventBus, http_client: httpx.AsyncClient | None = None):
        self.event_bus = event_bus
        self._http_client = http_client

    async def scan_all(
        self,
        *,
        db: Session,
        mdns_timeout: float = 2.0,
        ssdp_timeout: float = 1.4,
        actor_user_id: str | None = None,
    ) -> list[Device]:
        local_ips = get_local_lan_ips()

        # Run mDNS, SSDP, and active Wi-Fi subnet ARP warm-up + port fingerprint concurrently
        mdns_task = asyncio.create_task(self.scan_mdns(timeout=mdns_timeout, local_ips=local_ips))
        ssdp_task = asyncio.create_task(self.scan_ssdp(timeout=ssdp_timeout, local_ips=local_ips))
        probe_task = asyncio.create_task(self.probe_active_subnets(local_ips=local_ips))

        mdns_res, ssdp_res, port_map = await asyncio.gather(
            mdns_task, ssdp_task, probe_task, return_exceptions=True
        )

        # Read ARP cache AFTER subnet warm-up so every awake Wi-Fi peer is present in macOS/Linux ARP table
        arp_endpoints = await asyncio.to_thread(self.scan_arp_and_interfaces, local_ips)

        # Enrich ARP endpoints with any open TCP ports discovered during the subnet sweep
        open_ports_by_ip: dict[str, list[int]] = port_map if isinstance(port_map, dict) else {}
        arp_ips: set[str] = set()
        for ep in arp_endpoints:
            if ep.ip_address:
                arp_ips.add(ep.ip_address)
                if ep.ip_address in open_ports_by_ip:
                    ep.raw_properties["open_ports"] = open_ports_by_ip[ep.ip_address]

        # Also add any TCP-responsive hosts whose MAC wasn't visible (e.g., bridged VLAN/VPN subnet)
        for ip_addr, ports in open_ports_by_ip.items():
            if ip_addr not in arp_ips and ports:
                fp = "tcp-" + hashlib.sha256(ip_addr.encode("utf-8")).hexdigest()[:24]
                arp_endpoints.append(
                    DiscoveredEndpoint(
                        hardware_fingerprint=fp,
                        discovery_source="subnet_probe",
                        ip_address=ip_addr,
                        port=ports[0],
                        raw_properties={"open_ports": ports},
                    )
                )

        endpoints: list[DiscoveredEndpoint] = []
        if isinstance(mdns_res, list):
            endpoints.extend(mdns_res)
        if isinstance(ssdp_res, list):
            endpoints.extend(ssdp_res)
        endpoints.extend(arp_endpoints)

        devices = await self.ingest_discovered_endpoints(db, endpoints, actor_user_id=actor_user_id)
        return devices

    async def probe_active_subnets(self, *, local_ips: set[str]) -> dict[str, list[int]]:
        """Warms the OS Wi-Fi ARP table across the local /24 subnet and fingerprints common device ports."""
        subnets = get_active_lan_subnets()
        if not subnets:
            return {}

        peer_ips: list[str] = []
        for net in subnets:
            for host_ip in net.hosts():
                ip_str = str(host_ip)
                if _is_usable_peer_ip(ip_str, local_ips):
                    peer_ips.append(ip_str)
                if len(peer_ips) >= 254:
                    break

        if not peer_ips:
            return {}

        # Step 1: Fast UDP datagram burst triggers OS Layer-2 ARP resolution for all phones/TVs on Wi-Fi
        def _arp_udp_warmup(targets: list[str]) -> None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            try:
                for ip_str in targets:
                    try:
                        sock.sendto(b"\x00", (ip_str, 5353))
                    except OSError:
                        continue
            finally:
                sock.close()

        await asyncio.to_thread(_arp_udp_warmup, peer_ips)

        # Step 2: Fast bounded TCP service check on responsive LAN peers
        sem = asyncio.Semaphore(96)
        open_ports: dict[str, list[int]] = {}

        async def _check_port(ip_str: str, port: int) -> None:
            async with sem:
                try:
                    _reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(ip_str, port),
                        timeout=0.22,
                    )
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
                    open_ports.setdefault(ip_str, []).append(port)
                except Exception:
                    return

        # Probe high-signal ports across subnet
        tasks = [
            _check_port(ip_str, port)
            for ip_str in peer_ips
            for port in (62078, 8009, 6466, 7000, 8123, 80)
        ]
        try:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=2.2)
        except TimeoutError:
            pass

        return open_ports

    async def scan_ssdp(
        self,
        *,
        target: tuple[str, int] = ("239.255.255.250", 1900),
        timeout: float = 1.4,
        local_ips: set[str] | None = None,
    ) -> list[DiscoveredEndpoint]:
        m_search = (
            "M-SEARCH * HTTP/1.1\r\n"
            f"HOST: {target[0]}:{target[1]}\r\n"
            'MAN: "ssdp:discover"\r\n'
            "MX: 1\r\n"
            "ST: ssdp:all\r\n\r\n"
        ).encode("ascii")

        raw_packets: list[tuple[bytes, tuple[str, int]]] = []
        primary_ip = get_primary_lan_ipv4()

        def _udp_exchange():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if primary_ip != "127.0.0.1":
                try:
                    sock.setsockopt(
                        socket.IPPROTO_IP,
                        socket.IP_MULTICAST_IF,
                        socket.inet_aton(primary_ip),
                    )
                except OSError:
                    pass
            sock.settimeout(0.25)
            try:
                sock.sendto(m_search, target)
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
                if local_ips is not None and not _is_usable_peer_ip(ip_addr, local_ips):
                    continue
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

    async def scan_mdns(self, timeout: float = 2.0, local_ips: set[str] | None = None) -> list[DiscoveredEndpoint]:
        discovered: list[DiscoveredEndpoint] = []
        found_services: list[tuple[str, str]] = []
        skip_ips = local_ips or {"127.0.0.1", "::1"}

        def _on_service_state_change(zeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
            if state_change is ServiceStateChange.Added:
                found_services.append((service_type, name))

        try:
            azc = AsyncZeroconf(interfaces=InterfaceChoice.All)
        except Exception:
            return []

        try:
            browser = AsyncServiceBrowser(azc.zeroconf, MDNS_SERVICE_TYPES, handlers=[_on_service_state_change])
            await asyncio.sleep(timeout)
            await browser.async_cancel()

            for svc_type, svc_name in found_services:
                info = AsyncServiceInfo(svc_type, svc_name)
                await info.async_request(azc.zeroconf, 900)
                addrs = info.parsed_scoped_addresses()
                peer_addrs = [a for a in addrs if _is_usable_peer_ip(a, skip_ips)]
                if not peer_addrs:
                    continue
                ip_addr = peer_addrs[0]
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

    def scan_arp_and_interfaces(self, local_ips: set[str] | None = None) -> list[DiscoveredEndpoint]:
        endpoints: list[DiscoveredEndpoint] = []
        seen: set[str] = set()
        skip_ips = local_ips if local_ips is not None else get_local_lan_ips()

        # 1. macOS / BSD `/usr/sbin/arp -an`
        for arp_bin in ("/usr/sbin/arp", "arp"):
            try:
                proc = subprocess.run(
                    [arp_bin, "-an"],
                    capture_output=True,
                    text=True,
                    timeout=2.5,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    for ep in parse_macos_arp_output(proc.stdout, skip_ips):
                        if ep.hardware_fingerprint not in seen:
                            seen.add(ep.hardware_fingerprint)
                            endpoints.append(ep)
                    if endpoints:
                        break
            except Exception:
                continue

        # 2. Linux `/proc/net/arp`
        arp_file = Path("/proc/net/arp")
        if arp_file.exists():
            try:
                lines = arp_file.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]
                for line in lines:
                    cols = line.split()
                    if len(cols) >= 6:
                        ip_addr, _hw_type, flags, mac, _mask, iface = cols[:6]
                        if flags == "0x0" or not _is_usable_peer_ip(ip_addr, skip_ips):
                            continue
                        norm_mac = normalize_mac(mac)
                        if not norm_mac:
                            continue
                        fp = f"arp-{norm_mac.lower()}"
                        if fp in seen:
                            continue
                        seen.add(fp)
                        endpoints.append(
                            DiscoveredEndpoint(
                                hardware_fingerprint=fp,
                                discovery_source="arp",
                                ip_address=ip_addr,
                                mac_address=norm_mac,
                                raw_properties={"interface": iface},
                            )
                        )
            except OSError:
                pass

        # 3. Linux `ip -j neigh`
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
                    dst = item.get("dst")
                    norm_mac = normalize_mac(item.get("lladdr"))
                    if not dst or not norm_mac or not _is_usable_peer_ip(dst, skip_ips):
                        continue
                    fp = f"arp-{norm_mac.lower()}"
                    if fp in seen:
                        continue
                    seen.add(fp)
                    endpoints.append(
                        DiscoveredEndpoint(
                            hardware_fingerprint=fp,
                            discovery_source="arp",
                            ip_address=dst,
                            mac_address=norm_mac,
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

        # Clean up any stale unpaired loopback (127.0.0.1 / ::1) self-discovery artifacts
        stale_loopbacks = (
            db.execute(
                select(Device).where(
                    Device.ip_address.in_(["127.0.0.1", "::1"]),
                    Device.lifecycle_state.in_(["discovered", "identified"]),
                )
            )
            .scalars()
            .all()
        )
        for stale in stale_loopbacks:
            db.delete(stale)
        if stale_loopbacks:
            db.flush()

        for ep in endpoints:
            classified = classify_discovered_endpoint(ep)
            existing = db.execute(
                select(Device).where(Device.hardware_fingerprint == ep.hardware_fingerprint)
            ).scalar_one_or_none()

            # Deduplicate if an mDNS/SSDP entry and ARP entry share the exact same LAN IP
            if existing is None and ep.ip_address:
                existing_by_ip = (
                    db.execute(
                        select(Device).where(
                            Device.ip_address == ep.ip_address,
                            Device.lifecycle_state.in_(["discovered", "identified", "authenticated", "enrolled"]),
                        )
                    )
                    .scalars()
                    .first()
                )
                if existing_by_ip is not None:
                    existing = existing_by_ip

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
                    mac_address=normalize_mac(ep.mac_address),
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
                if ep.port and not existing.port:
                    existing.port = ep.port
                norm_mac = normalize_mac(ep.mac_address)
                if norm_mac and not existing.mac_address:
                    existing.mac_address = norm_mac
                if not existing.manufacturer and classified["manufacturer"]:
                    existing.manufacturer = classified["manufacturer"]
                if existing.lifecycle_state in ("discovered", "identified"):
                    if classified["identified"] and existing.name.startswith("LAN Device"):
                        existing.name = classified["name"]
                    if existing.category == "network" and classified["category"] != "network":
                        existing.category = classified["category"]
                    if existing.adapter_type == "generic_network" and classified["adapter_type"] != "generic_network":
                        existing.adapter_type = classified["adapter_type"]
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

import asyncio
import hashlib
import ipaddress
import json
import re
import socket
import subprocess
import sys
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
    "_adb-tls-connect._tcp.local.",
    "_adb-tls-pairing._tcp.local.",
    "_apple-mobdev2._tcp.local.",
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

VIRTUAL_IFACE_PREFIXES = (
    "lo", "utun", "tun", "tap", "wg", "tailscale", "docker", "br-", "veth",
    "vmnet", "vboxnet", "awdl", "llw", "anpi", "gif", "stf", "ap", "bridge0",
)

OUI_VENDOR_PREFIXES: dict[str, str] = {
    # Apple
    "3C:22:FB": "Apple", "F0:18:98": "Apple", "BC:D0:74": "Apple", "A4:83:E7": "Apple",
    "DC:A9:04": "Apple", "AC:BC:32": "Apple", "F8:FF:C2": "Apple", "D0:81:7A": "Apple",
    "60:F8:1D": "Apple", "14:7D:DA": "Apple", "28:CF:E9": "Apple", "7C:D1:C3": "Apple",
    "98:01:A7": "Apple", "88:66:5A": "Apple", "C8:69:CD": "Apple", "50:ED:3C": "Apple",
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
    # Xiaomi / Huawei / OnePlus / TP-Link / Ubiquiti / Netgear / ASUS / Tecno / Infinix
    "64:CE:01": "Xiaomi", "28:6C:07": "Xiaomi", "50:C7:BF": "TP-Link", "B0:BE:76": "TP-Link",
    "78:8A:20": "Ubiquiti", "F0:9F:C2": "Ubiquiti", "9C:3D:CF": "Netgear",
    "04:D9:F5": "ASUS", "2C:FD:A1": "ASUS",
}

MACOS_ARP_LINE_RE = re.compile(
    r"^(?P<host>\S+)\s+\((?P<ip>\d+\.\d+\.\d+\.\d+)\)\s+at\s+(?P<mac>\S+)\s+on\s+(?P<iface>\S+)"
)

CGNAT_IPV4_NET = ipaddress.ip_network("100.64.0.0/10")


def _as_naive_utc(dt):
    """Normalize a stored datetime to naive UTC for comparison.

    SQLite returns naive datetimes even when tz-aware values were stored, and
    SQLAlchemy with timezone=True may return aware ones — handle both.
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _is_stale_device(device: "Device", now_naive, stale_after_seconds: float) -> bool:
    """An unpaired/discovered device is stale when nothing has been heard from it."""
    if device.last_seen_at is None:
        return False
    last = _as_naive_utc(device.last_seen_at)
    return (now_naive - last).total_seconds() > stale_after_seconds


def is_lan_or_cgnat_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for RFC1918/ULA private IPs as well as RFC6598 100.64.0.0/10 Wi-Fi/CPE subnets."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_private:
        return True
    if isinstance(ip, ipaddress.IPv4Address) and ip in CGNAT_IPV4_NET:
        return True
    return False


def _is_virtual_iface(name: str) -> bool:
    low = name.lower()
    return any(low.startswith(prefix) for prefix in VIRTUAL_IFACE_PREFIXES)


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
    if normalized.startswith("01:00:5E:") or normalized.startswith("33:33:"):
        return None
    return normalized


def lookup_mac_vendor(mac: str | None) -> str | None:
    norm = normalize_mac(mac)
    if not norm:
        return None
    return OUI_VENDOR_PREFIXES.get(norm[:8])


def _get_macos_physical_wifi_ips() -> list[dict[str, Any]]:
    """Queries macOS `networksetup` + `ipconfig` to find the real Wi-Fi/Ethernet IP (bypassing VPN utun tunnels)."""
    if sys.platform != "darwin":
        return []
    results: list[dict[str, Any]] = []
    seen_ifaces: list[tuple[str, str]] = []

    try:
        proc = subprocess.run(
            ["/usr/sbin/networksetup", "-listallhardwareports"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if proc.returncode == 0:
            current_label = ""
            for line in proc.stdout.splitlines():
                line = line.strip()
                if line.startswith("Hardware Port:"):
                    current_label = line.split(":", 1)[1].strip()
                elif line.startswith("Device:"):
                    dev = line.split(":", 1)[1].strip()
                    if dev and not _is_virtual_iface(dev):
                        # Put Wi-Fi first
                        if "wi-fi" in current_label.lower() or "airport" in current_label.lower():
                            seen_ifaces.insert(0, (dev, current_label))
                        else:
                            seen_ifaces.append((dev, current_label))
    except Exception:
        pass

    for fallback_dev in ("en0", "en1", "en2"):
        if not any(d == fallback_dev for d, _ in seen_ifaces):
            seen_ifaces.append((fallback_dev, fallback_dev))

    for dev, label in seen_ifaces:
        try:
            ip_proc = subprocess.run(
                ["/usr/sbin/ipconfig", "getifaddr", dev],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            ip_str = ip_proc.stdout.strip()
            if ip_proc.returncode != 0 or not ip_str:
                continue
            ip_obj = ipaddress.ip_address(ip_str)
            if not isinstance(ip_obj, ipaddress.IPv4Address) or ip_obj.is_loopback or ip_obj.is_link_local:
                continue

            prefix_len = 24
            mask_proc = subprocess.run(
                ["/usr/sbin/ipconfig", "getoption", dev, "subnet_mask"],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
            )
            mask_str = mask_proc.stdout.strip()
            if mask_proc.returncode == 0 and mask_str:
                try:
                    prefix_len = ipaddress.IPv4Network(f"0.0.0.0/{mask_str}").prefixlen
                except ValueError:
                    prefix_len = 24

            results.append(
                {
                    "interface": dev,
                    "label": label,
                    "ip": ip_str,
                    "prefix": prefix_len,
                }
            )
        except Exception:
            continue

    return results


def list_lan_interfaces() -> list[dict[str, Any]]:
    """Returns physical Wi-Fi/LAN IPv4 interfaces first, filtering out VPN/utun/virtual interfaces."""
    items: list[dict[str, Any]] = []
    seen_ips: set[str] = set()

    for mac_item in _get_macos_physical_wifi_ips():
        seen_ips.add(mac_item["ip"])
        items.append(mac_item)

    try:
        physical: list[dict[str, Any]] = []
        other: list[dict[str, Any]] = []
        for adapter in ifaddr.get_adapters():
            iface_name = str(adapter.name)
            for ip_info in adapter.ips:
                if not isinstance(ip_info.ip, str) or ip_info.ip in seen_ips:
                    continue
                try:
                    ip_obj = ipaddress.ip_address(ip_info.ip)
                except ValueError:
                    continue
                if not isinstance(ip_obj, ipaddress.IPv4Address):
                    continue
                if ip_obj.is_loopback or ip_obj.is_link_local or not is_lan_or_cgnat_ip(ip_obj):
                    continue
                seen_ips.add(ip_info.ip)
                entry = {
                    "interface": iface_name,
                    "label": iface_name,
                    "ip": ip_info.ip,
                    "prefix": int(ip_info.network_prefix or 24),
                }
                if _is_virtual_iface(iface_name):
                    other.append(entry)
                else:
                    physical.append(entry)
        items.extend(physical)
        if not items:
            items.extend(other)
    except Exception:
        pass

    return items


def get_local_lan_ips() -> set[str]:
    ips: set[str] = {"127.0.0.1", "::1"}
    for entry in list_lan_interfaces():
        ips.add(entry["ip"])
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
    """Detects the real physical Wi-Fi / LAN IPv4 address (prefers macOS en0 Wi-Fi over VPN utun interfaces)."""
    ifaces = list_lan_interfaces()
    if ifaces:
        return str(ifaces[0]["ip"])

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("192.168.1.1", 80))
            candidate = sock.getsockname()[0]
            ip_obj = ipaddress.ip_address(candidate)
            if ip_obj.is_private and not ip_obj.is_loopback and not ip_obj.is_link_local:
                return candidate
        finally:
            sock.close()
    except OSError:
        pass

    return "127.0.0.1"


def _get_default_gateway_ipv4() -> str | None:
    if sys.platform == "darwin" and Path("/sbin/route").exists():
        try:
            proc = subprocess.run(
                ["/sbin/route", "-n", "get", "default"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            m = re.search(r"gateway:\s*(\d+\.\d+\.\d+\.\d+)", proc.stdout or "")
            if m:
                return m.group(1)
        except Exception:
            pass
    else:
        try:
            proc = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=1.5, check=False)
            m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", proc.stdout or "")
            if m:
                return m.group(1)
        except Exception:
            pass
    return None


def get_active_lan_subnets() -> list[ipaddress.IPv4Network]:
    subnets: list[ipaddress.IPv4Network] = []
    seen: set[str] = set()
    for entry in list_lan_interfaces():
        ip_str = entry["ip"]
        prefix = max(24, int(entry.get("prefix") or 24))
        net = ipaddress.IPv4Network(f"{ip_str}/{prefix}", strict=False)
        if str(net) not in seen:
            seen.add(str(net))
            subnets.append(net)

    gw = _get_default_gateway_ipv4()
    if gw and _is_safe_lan_ip(gw):
        gw_net = ipaddress.IPv4Network(f"{gw}/24", strict=False)
        if str(gw_net) not in seen:
            seen.add(str(gw_net))
            subnets.append(gw_net)

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
        if isinstance(ip, ipaddress.IPv4Address) and clean_host.endswith((".0", ".255")):
            return False
        return is_lan_or_cgnat_ip(ip)
    except ValueError:
        return False


def _is_safe_lan_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
        return is_lan_or_cgnat_ip(ip) or ip.is_loopback or ip.is_link_local
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
        iface = m.group("iface")
        if _is_virtual_iface(iface):
            continue
        ip_addr = m.group("ip")
        if not _is_usable_peer_ip(ip_addr, local_ips):
            continue
        raw_mac = m.group("mac").strip().lower()
        if raw_mac in ("(incomplete)", "incomplete", "ff:ff:ff:ff:ff:ff") or raw_mac.startswith(("1:0:5e:", "01:00:5e:", "33:33:")):
            continue
        norm_mac = normalize_mac(raw_mac)
        # On macOS Sonoma/Sequoia, non-root processes may see privacy-redacted MACs (`0:0:0:0:0:0`)
        # for live Wi-Fi neighbors on en0 — preserve the live host by IP fingerprint instead of dropping it!
        if norm_mac:
            fp = f"arp-{norm_mac.lower()}"
        elif raw_mac in ("0:0:0:0:0:0", "00:00:00:00:00:00", "(redacted)"):
            fp = "arp-ip-" + hashlib.sha256(ip_addr.encode("utf-8")).hexdigest()[:24]
        else:
            continue
        if fp in seen:
            continue
        seen.add(fp)
        host_raw = m.group("host")
        hostname = None if host_raw == "?" else host_raw.rstrip(".")
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
    elif "_adb-tls-connect._tcp" in svc or "_adb-tls-pairing._tcp" in svc or 5555 in open_ports:
        category = "smartphone"
        adapter_type = "android_adb"
        os_name = "Android"
    elif 62078 in open_ports or "_apple-mobdev2._tcp" in svc or "_companion-link._tcp" in svc:
        category = "smartphone"
        adapter_type = "ios_native"
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
    elif "_smb._tcp" in svc or 445 in open_ports:
        category = "desktop"
    elif vendor in ("Apple", "Samsung", "Xiaomi"):
        category = "smartphone"

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
        friendly = f"Wi-Fi Device ({ep.ip_address or norm_mac or 'Unknown'})"

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
    """Multi-protocol Wi-Fi / LAN discovery (macOS en0 Subnet Sweep, mDNSResponder/dns-sd, ARP, Zeroconf, SSDP)."""

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

        mdns_task = asyncio.create_task(self.scan_mdns(timeout=mdns_timeout, local_ips=local_ips))
        ssdp_task = asyncio.create_task(self.scan_ssdp(timeout=ssdp_timeout, local_ips=local_ips))
        probe_task = asyncio.create_task(self.probe_active_subnets(local_ips=local_ips))
        dnssd_task = asyncio.create_task(self.scan_macos_dnssd(local_ips=local_ips))

        mdns_res, ssdp_res, probe_res, dnssd_res = await asyncio.gather(
            mdns_task, ssdp_task, probe_task, dnssd_task, return_exceptions=True
        )

        # Brief settle so macOS kernel finishes recording Layer-2 ARP replies from the subnet sweep
        await asyncio.sleep(0.18)
        arp_endpoints = await asyncio.to_thread(self.scan_arp_and_interfaces, local_ips)

        open_ports_by_ip: dict[str, list[int]] = {}
        alive_ips: set[str] = set()
        if isinstance(probe_res, tuple) and len(probe_res) == 2:
            open_ports_by_ip, alive_ips = probe_res

        arp_ips: set[str] = set()
        for ep in arp_endpoints:
            if ep.ip_address:
                arp_ips.add(ep.ip_address)
                if ep.ip_address in open_ports_by_ip:
                    ep.raw_properties["open_ports"] = open_ports_by_ip[ep.ip_address]
                if not ep.hostname:
                    ep.hostname = await self._try_reverse_hostname(ep.ip_address)

        # Include responsive Wi-Fi peers (TCP SYN-ACK or TCP RST) even if ARP entry wasn't exposed
        for ip_addr in sorted(alive_ips | set(open_ports_by_ip.keys())):
            if ip_addr not in arp_ips and _is_usable_peer_ip(ip_addr, local_ips):
                ports = open_ports_by_ip.get(ip_addr, [])
                hostname = await self._try_reverse_hostname(ip_addr)
                fp = "lan-" + hashlib.sha256(ip_addr.encode("utf-8")).hexdigest()[:24]
                arp_endpoints.append(
                    DiscoveredEndpoint(
                        hardware_fingerprint=fp,
                        discovery_source="subnet_probe",
                        ip_address=ip_addr,
                        port=ports[0] if ports else None,
                        hostname=hostname,
                        raw_properties={"open_ports": ports},
                    )
                )

        endpoints: list[DiscoveredEndpoint] = []
        if isinstance(dnssd_res, list):
            endpoints.extend(dnssd_res)
        if isinstance(mdns_res, list):
            endpoints.extend(mdns_res)
        if isinstance(ssdp_res, list):
            endpoints.extend(ssdp_res)
        endpoints.extend(arp_endpoints)

        devices = await self.ingest_discovered_endpoints(db, endpoints, actor_user_id=actor_user_id)
        await self.mark_stale_unpaired_devices(db)
        return devices

    async def _try_reverse_hostname(self, ip_addr: str) -> str | None:
        def _lookup() -> str | None:
            try:
                host, _, _ = socket.gethostbyaddr(ip_addr)
                if host and host != ip_addr:
                    return host.rstrip(".")
            except Exception:
                return None
            return None

        try:
            return await asyncio.wait_for(asyncio.to_thread(_lookup), timeout=0.25)
        except Exception:
            return None

    async def scan_macos_dnssd(self, *, local_ips: set[str]) -> list[DiscoveredEndpoint]:
        """Queries macOS native `mDNSResponder` daemon via `/usr/bin/dns-sd` IPC."""
        if sys.platform != "darwin" or not Path("/usr/bin/dns-sd").exists():
            return []

        key_services = [
            "_companion-link._tcp",
            "_airplay._tcp",
            "_googlecast._tcp",
            "_androidtvremote2._tcp",
            "_hap._tcp",
            "_home-assistant._tcp",
            "_sonos._tcp",
            "_smb._tcp",
        ]

        def _browse_one(svc: str) -> list[tuple[str, str]]:
            found: list[tuple[str, str]] = []
            try:
                proc = subprocess.Popen(
                    ["/usr/bin/dns-sd", "-B", svc, "local."],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                try:
                    out, _ = proc.communicate(timeout=0.9)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    out, _ = proc.communicate()
                for line in (out or "").splitlines():
                    if "Add" in line and svc in line:
                        parts = line.split(svc)
                        if len(parts) >= 2:
                            inst_name = parts[-1].strip()
                            if inst_name and inst_name != "Instance Name":
                                found.append((f"{svc}.local.", inst_name))
            except Exception:
                pass
            return found

        def _resolve_instance(svc_full: str, inst_name: str) -> DiscoveredEndpoint | None:
            svc_short = svc_full.removesuffix(".local.")
            try:
                proc = subprocess.Popen(
                    ["/usr/bin/dns-sd", "-L", inst_name, svc_short, "local."],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                try:
                    out, _ = proc.communicate(timeout=0.7)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    out, _ = proc.communicate()

                m = re.search(r"can be reached at\s+([^\s:]+):(\d+)", out or "")
                if not m:
                    return None
                target_host = m.group(1).rstrip(".")
                port = int(m.group(2))
                ip_addr = socket.gethostbyname(target_host)
                if not _is_usable_peer_ip(ip_addr, local_ips):
                    return None
                fp = "dnssd-" + hashlib.sha256(f"{svc_full}:{inst_name}".encode("utf-8")).hexdigest()[:24]
                return DiscoveredEndpoint(
                    hardware_fingerprint=fp,
                    discovery_source="mdns",
                    ip_address=ip_addr,
                    port=port,
                    hostname=target_host,
                    service_type=svc_full,
                    raw_properties={"name": inst_name},
                )
            except Exception:
                return None

        raw_instances: list[tuple[str, str]] = []
        batches = await asyncio.gather(*[asyncio.to_thread(_browse_one, s) for s in key_services], return_exceptions=True)
        seen_inst: set[tuple[str, str]] = set()
        for b in batches:
            if isinstance(b, list):
                for item in b:
                    if item not in seen_inst:
                        seen_inst.add(item)
                        raw_instances.append(item)

        if not raw_instances:
            return []

        resolved = await asyncio.gather(
            *[asyncio.to_thread(_resolve_instance, svc, inst) for svc, inst in raw_instances[:24]],
            return_exceptions=True,
        )
        return [ep for ep in resolved if isinstance(ep, DiscoveredEndpoint)]

    async def probe_active_subnets(self, *, local_ips: set[str]) -> tuple[dict[str, list[int]], set[str]]:
        """Low-FD multi-stage Wi-Fi /24 sweep: 1-socket UDP ARP trigger + fast TCP RST/SYN liveness + service fingerprinting."""
        subnets = get_active_lan_subnets()
        if not subnets:
            return {}, set()

        peer_ips: list[str] = []
        for net in subnets:
            for host_ip in net.hosts():
                ip_str = str(host_ip)
                if _is_usable_peer_ip(ip_str, local_ips):
                    peer_ips.append(ip_str)
                if len(peer_ips) >= 254:
                    break

        if not peer_ips:
            return {}, set()

        # Stage 1: Single-socket UDP datagram sweep (uses 1 FD, forces kernel Layer-2 ARP Who-has across entire /24 in <10ms)
        def _udp_arp_sweep() -> None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setblocking(False)
                for ip_str in peer_ips:
                    try:
                        sock.sendto(b"\x00", (ip_str, 9))
                    except OSError:
                        pass
            finally:
                sock.close()

        await asyncio.to_thread(_udp_arp_sweep)

        open_ports: dict[str, list[int]] = {}
        alive_ips: set[str] = set()
        gw_ip = _get_default_gateway_ipv4()
        if gw_ip and _is_usable_peer_ip(gw_ip, local_ips):
            alive_ips.add(gw_ip)

        # Stage 2: Fast 2-port TCP SYN/RST liveness sweep across all 254 IPs (low concurrency = 36 FDs max)
        liveness_sem = asyncio.Semaphore(36)

        async def _fast_liveness(ip_str: str) -> None:
            async with liveness_sem:
                for p in (62078, 80):
                    try:
                        _r, w = await asyncio.wait_for(asyncio.open_connection(ip_str, p), timeout=0.14)
                        w.close()
                        try:
                            await w.wait_closed()
                        except Exception:
                            pass
                        alive_ips.add(ip_str)
                        open_ports.setdefault(ip_str, []).append(p)
                        return
                    except ConnectionRefusedError:
                        # Immediate TCP RST proves phone/computer is awake on Wi-Fi
                        alive_ips.add(ip_str)
                        return
                    except (TimeoutError, asyncio.TimeoutError, OSError):
                        continue

        try:
            await asyncio.wait_for(
                asyncio.gather(*[_fast_liveness(ip_str) for ip_str in peer_ips], return_exceptions=True),
                timeout=2.6,
            )
        except (TimeoutError, asyncio.TimeoutError):
            pass

        # Collect any additional ARP-confirmed neighbors before Stage 3 service classification
        arp_now = await asyncio.to_thread(self.scan_arp_and_interfaces, local_ips)
        for ep in arp_now:
            if ep.ip_address:
                alive_ips.add(ep.ip_address)

        # Stage 3: Multi-port protocol classification ONLY on confirmed live Wi-Fi hosts
        svc_sem = asyncio.Semaphore(20)

        async def _fingerprint_live_host(ip_str: str) -> None:
            async with svc_sem:
                for port in (62078, 5555, 8009, 6466, 7000, 8123, 1400, 445, 80, 443):
                    if port in open_ports.get(ip_str, []):
                        continue
                    try:
                        _r, w = await asyncio.wait_for(asyncio.open_connection(ip_str, port), timeout=0.22)
                        w.close()
                        try:
                            await w.wait_closed()
                        except Exception:
                            pass
                        open_ports.setdefault(ip_str, []).append(port)
                    except (ConnectionRefusedError, TimeoutError, asyncio.TimeoutError, OSError):
                        continue

        if alive_ips:
            await asyncio.gather(*[_fingerprint_live_host(ip_str) for ip_str in sorted(alive_ips)], return_exceptions=True)

        return open_ports, alive_ips

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
        primary_ip = get_primary_lan_ipv4()

        def _on_service_state_change(zeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
            if state_change is ServiceStateChange.Added:
                found_services.append((service_type, name))

        try:
            ifaces: Any = [primary_ip] if primary_ip != "127.0.0.1" else InterfaceChoice.All
            azc = AsyncZeroconf(interfaces=ifaces)
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
                        if flags == "0x0" or _is_virtual_iface(iface) or not _is_usable_peer_ip(ip_addr, skip_ips):
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
                    dev = str(item.get("dev", ""))
                    dst = item.get("dst")
                    norm_mac = normalize_mac(item.get("lladdr"))
                    if not dst or not norm_mac or _is_virtual_iface(dev) or not _is_usable_peer_ip(dst, skip_ips):
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
                            raw_properties={"interface": dev, "nud_state": item.get("state", [])},
                        )
                    )
        except Exception:
            pass

        return endpoints

    async def mark_stale_unpaired_devices(
        self,
        db: Session,
        *,
        stale_after_seconds: float = 3600.0,
    ) -> list[Device]:
        """Flag discovered/identified devices that went silent as stale.

        Sleeping, asleep-in-a-box and unplugged unpaired devices must not keep
        the bright ``unpaired`` badge forever: after ``stale_after_seconds``
        without a sighting they are marked ``connection_status="stale"``.
        Enrolled/authenticated devices are never auto-marked here — their
        connected-state machine (agent WebSocket sessions) owns their lifecycle.
        """
        now = datetime.now(timezone.utc)
        now_naive = _as_naive_utc(now)
        candidates = (
            db.execute(
                select(Device).where(
                    Device.lifecycle_state.in_(["discovered", "identified"]),
                    Device.connection_status != "stale",
                )
            )
            .scalars()
            .all()
        )
        newly_stale: list[Device] = []
        for device in candidates:
            if _is_stale_device(device, now_naive, stale_after_seconds):
                device.connection_status = "stale"
                newly_stale.append(device)
        if not newly_stale:
            return []
        append_audit_log(
            db,
            severity="info",
            category="discovery",
            event_type="discovery.device_stale",
            actor_type="system",
            actor_id=None,
            outcome="success",
            details={
                "stale_count": len(newly_stale),
                "device_ids": [d.id for d in newly_stale],
                "stale_after_seconds": stale_after_seconds,
            },
        )
        db.commit()
        await self.event_bus.publish(
            "discovery.stale_marked",
            {"stale_count": len(newly_stale), "timestamp": now.isoformat()},
        )
        return newly_stale

    async def ingest_discovered_endpoints(
        self,
        db: Session,
        endpoints: list[DiscoveredEndpoint],
        *,
        actor_user_id: str | None = None,
    ) -> list[Device]:
        touched: list[Device] = []
        now = datetime.now(timezone.utc)
        local_ips = get_local_lan_ips()

        # Purge any stale unpaired self/loopback artifacts (127.0.0.1, ::1, or this Mac's own IP)
        stale_self = (
            db.execute(
                select(Device).where(
                    Device.ip_address.in_(list(local_ips)),
                    Device.lifecycle_state.in_(["discovered", "identified"]),
                )
            )
            .scalars()
            .all()
        )
        for stale in stale_self:
            db.delete(stale)
        if stale_self:
            db.flush()

        for ep in endpoints:
            classified = classify_discovered_endpoint(ep)
            existing = db.execute(
                select(Device).where(Device.hardware_fingerprint == ep.hardware_fingerprint)
            ).scalar_one_or_none()

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
                was_stale = existing.connection_status == "stale"
                existing.last_seen_at = now
                if was_stale:
                    existing.connection_status = "unpaired"
                    await self.event_bus.publish(
                        "device.returning",
                        {
                            "device_id": existing.id,
                            "device_name": existing.name,
                            "returning": True,
                            "timestamp": now.isoformat(),
                        },
                    )
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
                    if classified["identified"] and (
                        existing.name.startswith("LAN Device") or existing.name.startswith("Wi-Fi Device")
                    ):
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

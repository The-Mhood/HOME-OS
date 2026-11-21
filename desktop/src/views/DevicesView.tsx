import React, { useEffect, useRef, useState } from 'react';
import { Copy, Plus, QrCode, Radar, Smartphone, Trash2, Wifi } from 'lucide-react';
import { apiRequest } from '../api/client';
import type { Device, Room } from '../types/models';

interface Props {
  devices: Device[];
  rooms: Room[];
  onRefresh: () => Promise<void>;
  onSelectDevice: (id: string) => void;
  onNotify: (msg: string) => void;
}

interface PairingSessionResponse {
  session_id: string;
  pin: string;
  qr_uri: string;
  deep_link_uri?: string;
  lan_host?: string;
  lan_port?: number;
  qr_svg: string;
  expires_at: string;
}

interface NetDiagnostics {
  primary_lan_ip: string;
  lan_interfaces: Array<{ interface: string; label: string; ip: string; prefix: number }>;
  bind_host: string;
  bind_port: number;
  lan_socket_reachable: boolean;
  macos_firewall_enabled: boolean;
}

interface NativeBridgesStatus {
  adb_binary_installed: boolean;
  adb_binary_path: string | null;
  py_adb_tcp_ready: boolean;
  android_connected: Array<{
    serial: string;
    state: string;
    transport: string;
    model: string;
    wifi_ip: string | null;
    authorized: boolean;
  }>;
  android_mdns_candidates: Array<{ id: string; name: string; ip_address: string | null; port: number | null }>;
  ios_connected: Array<{
    udid: string;
    device_name?: string;
    model?: string;
    os_version?: string;
    transport: string;
    wifi_enabled: boolean;
    usb_connected: boolean;
    ip_address: string | null;
  }>;
  macos_continuity_available: boolean;
}

const ALL_SCOPES = [
  'telemetry:read',
  'notifications:read',
  'notifications:reply',
  'sms:read',
  'sms:send',
  'calls:read',
  'flashlight:control',
  'media:control',
  'volume:control',
  'power:control',
  'apps:launch',
  'apps:manage',
  'files:browse',
  'files:write',
  'screen:view',
  'input:remote',
  'smarthome:read',
  'smarthome:control',
  'smarthome:sensitive',
];

export const DevicesView: React.FC<Props> = ({
  devices,
  rooms,
  onRefresh,
  onSelectDevice,
  onNotify,
}) => {
  const [showPairModal, setShowPairModal] = useState(false);
  const [connectTab, setConnectTab] = useState<'android' | 'ios' | 'other'>('android');
  const [bridges, setBridges] = useState<NativeBridgesStatus | null>(null);
  const [connectingNative, setConnectingNative] = useState(false);

  // Android Direct (Zero-Webpage) inputs
  const [androidPairAddr, setAndroidPairAddr] = useState('');
  const [androidPairCode, setAndroidPairCode] = useState('');
  const [androidConnectAddr, setAndroidConnectAddr] = useState('');
  const [androidLabel, setAndroidLabel] = useState('');

  // iOS Native (Zero-Webpage) input
  const [iosLabel, setIosLabel] = useState('');

  // Agent / TV / Optional QR states
  const [pairAdapter, setPairAdapter] = useState('google_tv');
  const [pairTargetId, setPairTargetId] = useState<string | undefined>(undefined);
  const [selectedScopes, setSelectedScopes] = useState<string[]>([...ALL_SCOPES]);
  const [pairingSession, setPairingSession] = useState<PairingSessionResponse | null>(null);
  const [netDiag, setNetDiag] = useState<NetDiagnostics | null>(null);
  const [overrideHostIp, setOverrideHostIp] = useState<string>('');
  const [completeName, setCompleteName] = useState('');
  const [completeIp, setCompleteIp] = useState('');

  // Room creation & manual discovery
  const [roomName, setRoomName] = useState('');
  const [manualName, setManualName] = useState('');
  const [manualIp, setManualIp] = useState('');
  const [manualCategory, setManualCategory] = useState('tv');
  const [manualAdapter, setManualAdapter] = useState('google_tv');
  const [scanning, setScanning] = useState(false);

  const registered = devices.filter((d) => d.is_registered);
  const unpaired = devices.filter((d) => !d.is_registered);
  const prevRegCount = useRef<number>(registered.length);

  const refreshNativeBridges = () => {
    apiRequest<NativeBridgesStatus>('/devices/native-bridges/status')
      .then(setBridges)
      .catch(() => {});
  };

  useEffect(() => {
    if (showPairModal && pairingSession && registered.length > prevRegCount.current) {
      setShowPairModal(false);
      setPairingSession(null);
      onNotify('Device paired & authenticated over Wi-Fi!');
    }
    prevRegCount.current = registered.length;
  }, [registered.length, showPairModal, pairingSession, onNotify]);

  useEffect(() => {
    if (!showPairModal) return;
    refreshNativeBridges();
    apiRequest<NetDiagnostics>('/system/network-diagnostics')
      .then((d) => {
        setNetDiag(d);
        if (!overrideHostIp && d.primary_lan_ip) {
          setOverrideHostIp(d.primary_lan_ip);
        }
      })
      .catch(() => {});
  }, [showPairModal, overrideHostIp]);

  const openPairForDiscovered = (device: Device) => {
    setPairingSession(null);
    if (device.adapter_type === 'android_adb' || device.os_name === 'Android') {
      setConnectTab('android');
      setAndroidConnectAddr(device.ip_address ? `${device.ip_address}:${device.port || 5555}` : '');
      setAndroidLabel(device.name);
      setShowPairModal(true);
      return;
    }
    if (device.adapter_type === 'ios_native' || device.os_name === 'iOS' || device.manufacturer === 'Apple') {
      setConnectTab('ios');
      setIosLabel(device.name);
      setShowPairModal(true);
      return;
    }
    setConnectTab('other');
    void startPairing(device);
  };

  const connectAndroidDirect = async (usbSerial?: string) => {
    setConnectingNative(true);
    try {
      const res = await apiRequest<{ device: Device; adb_target: string }>('/devices/android/connect', {
        method: 'POST',
        body: JSON.stringify({
          usb_serial: usbSerial || undefined,
          enable_wifi: true,
          pair_address: androidPairAddr || undefined,
          pairing_code: androidPairCode || undefined,
          connect_address: androidConnectAddr || undefined,
          name: androidLabel || undefined,
        }),
      });
      setShowPairModal(false);
      onNotify(
        usbSerial
          ? `Connected ${res.device.name} on permanent Wi-Fi (${res.adb_target}) — you can unplug USB now! Zero webpage needed.`
          : `Connected ${res.device.name} (${res.adb_target}) in background OS mode — zero webpage required!`
      );
      await onRefresh();
      onSelectDevice(res.device.id);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Android background connection failed');
    } finally {
      setConnectingNative(false);
    }
  };

  const connectIosNative = async (udid?: string) => {
    setConnectingNative(true);
    try {
      const res = await apiRequest<{ device: Device }>('/devices/ios/connect', {
        method: 'POST',
        body: JSON.stringify({
          udid: udid || undefined,
          name: iosLabel || undefined,
        }),
      });
      setShowPairModal(false);
      onNotify(`Connected ${res.device.name} via native Apple Lockdown & Continuity — zero webpage required!`);
      await onRefresh();
      onSelectDevice(res.device.id);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'iPhone native connection failed');
    } finally {
      setConnectingNative(false);
    }
  };

  const startPairing = async (device?: Device, customHost?: string) => {
    const adapter = device && device.adapter_type !== 'generic_network' ? device.adapter_type : pairAdapter;
    const hubHost = customHost || overrideHostIp || undefined;
    try {
      const res = await apiRequest<PairingSessionResponse>('/pairing/initiate', {
        method: 'POST',
        body: JSON.stringify({
          adapter_type: adapter,
          device_id: device?.id,
          hub_host: hubHost,
          requested_permissions: selectedScopes,
        }),
      });
      setPairAdapter(adapter);
      setPairTargetId(device?.id);
      setCompleteName(device?.name || '');
      setCompleteIp(device?.ip_address || '');
      setPairingSession(res);
      setShowPairModal(true);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Failed to start pairing');
    }
  };

  const confirmDirectPairing = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pairingSession) return;
    const catMap: Record<string, string> = {
      android_companion: 'smartphone',
      ios_companion: 'smartphone',
      linux_agent: 'desktop',
      windows_agent: 'desktop',
      macos_agent: 'laptop',
      google_tv: 'tv',
    };
    try {
      await apiRequest('/pairing/complete', {
        method: 'POST',
        body: JSON.stringify({
          pairing_session_id: pairingSession.session_id,
          pin_or_token: pairingSession.pin,
          hardware_fingerprint: pairTargetId || `${pairAdapter}-${Date.now()}`,
          name: completeName || `${pairAdapter} Device`,
          category: catMap[pairAdapter] || 'desktop',
          ip_address: completeIp || undefined,
        }),
      });
      setShowPairModal(false);
      setPairingSession(null);
      onNotify('Device enrolled and cryptographically authenticated.');
      await onRefresh();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Enrollment failed');
    }
  };

  const handleAddRoom = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!roomName.trim()) return;
    try {
      await apiRequest('/rooms', {
        method: 'POST',
        body: JSON.stringify({ name: roomName.trim() }),
      });
      setRoomName('');
      onNotify('Room created.');
      await onRefresh();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Could not create room');
    }
  };

  const handleDeleteRoom = async (id: string) => {
    await apiRequest(`/rooms/${id}`, { method: 'DELETE' });
    await onRefresh();
  };

  const handleManualAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!manualName.trim()) return;
    try {
      await apiRequest('/devices/manual', {
        method: 'POST',
        body: JSON.stringify({
          name: manualName.trim(),
          ip_address: manualIp.trim() || null,
          category: manualCategory,
          adapter_type: manualAdapter,
        }),
      });
      setManualName('');
      setManualIp('');
      onNotify('Device added to discovered inventory (unpaired until authenticated).');
      await onRefresh();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Failed to register device');
    }
  };

  const triggerScan = async () => {
    setScanning(true);
    try {
      const res = await apiRequest<{ discovered_in_scan: number; subnets_scanned?: string[] }>(
        '/discovery/scan',
        { method: 'POST' }
      );
      const subTxt = res.subnets_scanned?.length ? ` on ${res.subnets_scanned.join(', ')}` : '';
      onNotify(`Scan complete: found ${res.discovered_in_scan} Wi-Fi / USB host(s)${subTxt}.`);
      await onRefresh();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Network scan failed');
    } finally {
      setScanning(false);
    }
  };

  const enrollLocalHost = async () => {
    try {
      await apiRequest('/devices/enroll-local-host', { method: 'POST' });
      onNotify('Added this computer with native hardware volume, media, apps, SMS & file controls.');
      await onRefresh();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Failed to enroll local host');
    }
  };

  const quickEnrollNetwork = async (deviceId: string) => {
    try {
      await apiRequest(`/devices/${deviceId}/enroll-network`, { method: 'POST' });
      onNotify('Enabled Wi-Fi Ping, Port Inspection & Wake-on-LAN for this device.');
      await onRefresh();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Failed to enable network tools');
    }
  };

  const hasLocalHost = registered.some((d) => d.adapter_type === 'local_host');

  const toggleScope = (scope: string) => {
    setSelectedScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope]
    );
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '22px' }}>
      <div className="section-header">
        <div>
          <h2 style={{ fontSize: '19px', fontWeight: 700 }}>Device Inventory & Background OS Enrollment</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
            Connect Android, iPhone, Smart TVs, and Computers via native OS background daemons — no webpage needs to stay open.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
          {!hasLocalHost && (
            <button className="btn" onClick={enrollLocalHost}>
              + Control This Mac
            </button>
          )}
          <button className="btn" onClick={triggerScan} disabled={scanning}>
            <Radar size={15} />
            {scanning ? 'Scanning Wi-Fi...' : 'Scan Network'}
          </button>
          <button
            className="btn btn-primary"
            onClick={() => {
              setPairingSession(null);
              setConnectTab('android');
              setShowPairModal(true);
            }}
          >
            <Smartphone size={15} />
            Pair New Device
          </button>
        </div>
      </div>

      {/* Registered Devices */}
      <div className="card">
        <div className="section-header">
          <div className="section-title">Registered & Authenticated Devices ({registered.length})</div>
        </div>
        {registered.length === 0 ? (
          <div className="empty-state">No devices enrolled yet. Click &quot;Pair New Device&quot; to connect Android, iPhone, TV, or PC.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Device</th>
                <th>Category / Bridge</th>
                <th>Room</th>
                <th>IP / Target</th>
                <th>Status</th>
                <th>Health</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {registered.map((d) => (
                <tr key={d.id}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{d.name}</div>
                    <div style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                      {d.manufacturer || d.adapter_type} {d.model ? `· ${d.model}` : ''}
                    </div>
                  </td>
                  <td>
                    <div>{d.category} {d.os_name ? `(${d.os_name})` : ''}</div>
                    {(d.adapter_type === 'android_adb' || d.adapter_type === 'ios_native' || d.adapter_type === 'local_host') && (
                      <span style={{ fontSize: '10.5px', color: '#34d399' }}>Background OS Daemon (Zero Webpage)</span>
                    )}
                  </td>
                  <td>{d.room_name || '—'}</td>
                  <td className="mono">{(d.metadata?.adb_target as string) || d.ip_address || 'native-bridge'}</td>
                  <td>
                    <span
                      className={`badge ${
                        d.connection_status === 'online'
                          ? 'badge-online'
                          : d.connection_status === 'standby'
                          ? 'badge-info'
                          : d.connection_status === 'isolated'
                          ? 'badge-isolated'
                          : 'badge-offline'
                      }`}
                    >
                      {d.connection_status === 'standby' ? 'standby (queued ready)' : d.connection_status}
                    </span>
                  </td>
                  <td className="tabular">{d.health_score !== null ? `${d.health_score}%` : d.integration_health}</td>
                  <td>
                    <button className="btn btn-sm btn-primary" onClick={() => onSelectDevice(d.id)}>
                      Manage
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Unpaired Discovered Devices */}
      <div className="card">
        <div className="section-header">
          <div className="section-title">Unpaired Discovered Network Inventory ({unpaired.length})</div>
        </div>
        {unpaired.length === 0 ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>
            No unpaired network devices currently in inventory. Run a scan or add an IP endpoint below.
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Observed Host</th>
                <th>Source</th>
                <th>Candidate Adapter</th>
                <th>IP / MAC</th>
                <th>Capability Status</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {unpaired.map((d) => (
                <tr key={d.id}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{d.name}</div>
                    <div style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>{d.manufacturer || 'Unidentified Vendor'}</div>
                  </td>
                  <td>{d.discovery_source}</td>
                  <td className="mono">{d.adapter_type}</td>
                  <td className="mono">
                    {d.ip_address || '—'} {d.mac_address ? `(${d.mac_address})` : ''}
                  </td>
                  <td>
                    <span className="badge badge-unpaired">Discovery Only — Zero Private Access</span>
                  </td>
                  <td style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                    <button className="btn btn-sm" onClick={() => onSelectDevice(d.id)}>
                      Inspect
                    </button>
                    <button className="btn btn-sm" onClick={() => quickEnrollNetwork(d.id)}>
                      Enable Ping/WoL
                    </button>
                    <button className="btn btn-sm btn-primary" onClick={() => openPairForDiscovered(d)}>
                      Pair
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Rooms & Manual Endpoint Registration */}
      <div className="grid-2">
        <div className="card">
          <div className="section-title" style={{ marginBottom: '12px' }}>Rooms & Zones</div>
          <form onSubmit={handleAddRoom} style={{ display: 'flex', gap: '8px', marginBottom: '14px' }}>
            <input
              className="input"
              style={{ flex: 1 }}
              placeholder="New room name (e.g. Studio, Bedroom)"
              value={roomName}
              onChange={(e) => setRoomName(e.target.value)}
            />
            <button type="submit" className="btn btn-primary">
              <Plus size={14} /> Add Room
            </button>
          </form>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {rooms.map((r) => (
              <span key={r.id} className="badge badge-info" style={{ padding: '5px 10px', fontSize: '12px' }}>
                {r.name}
                <button
                  type="button"
                  style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', marginLeft: '4px' }}
                  onClick={() => handleDeleteRoom(r.id)}
                  title="Delete room"
                >
                  <Trash2 size={12} />
                </button>
              </span>
            ))}
            {rooms.length === 0 && <span style={{ color: 'var(--text-muted)', fontSize: '12.5px' }}>No rooms created yet.</span>}
          </div>
        </div>

        <div className="card">
          <div className="section-title" style={{ marginBottom: '12px' }}>Manual IP Discovery Probe</div>
          <form onSubmit={handleManualAdd} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
            <input
              className="input"
              placeholder="Device label (e.g. Living Room Sony TV)"
              value={manualName}
              onChange={(e) => setManualName(e.target.value)}
              required
            />
            <input
              className="input"
              placeholder="IP Address (e.g. 192.168.1.50)"
              value={manualIp}
              onChange={(e) => setManualIp(e.target.value)}
            />
            <select className="select" value={manualCategory} onChange={(e) => setManualCategory(e.target.value)}>
              <option value="smartphone">Smartphone (Android / iOS)</option>
              <option value="tv">Google TV / Android TV</option>
              <option value="desktop">Desktop Computer</option>
              <option value="laptop">Laptop</option>
              <option value="speaker">Smart Speaker</option>
              <option value="appliance">Wi-Fi Appliance</option>
              <option value="network">Unknown LAN Device</option>
            </select>
            <select className="select" value={manualAdapter} onChange={(e) => setManualAdapter(e.target.value)}>
              <option value="android_adb">Adapter: Android Direct OS (No Webpage)</option>
              <option value="ios_native">Adapter: iPhone Native Bridge (No Webpage)</option>
              <option value="google_tv">Adapter: Google TV Remote v2</option>
              <option value="linux_agent">Adapter: Linux Native Agent</option>
              <option value="windows_agent">Adapter: Windows .NET Agent</option>
              <option value="macos_agent">Adapter: macOS Swift Agent</option>
              <option value="generic_network">Adapter: Generic Unpaired LAN</option>
            </select>
            <button type="submit" className="btn" style={{ gridColumn: 'span 2' }}>
              Register in Discovered Inventory
            </button>
          </form>
        </div>
      </div>

      {/* Zero-Webpage Native OS Pairing Modal */}
      {showPairModal && (
        <div className="modal-backdrop" onClick={() => setShowPairModal(false)}>
          <div className="modal-card" style={{ maxWidth: '640px', width: '95%' }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
              <h3 style={{ fontSize: '17px', fontWeight: 700 }}>
                Connect Device — Background OS Control (No Webpage Required)
              </h3>
              <button className="btn btn-sm" onClick={refreshNativeBridges}>Refresh Detected</button>
            </div>

            <div style={{ display: 'flex', gap: '8px', marginBottom: '16px', flexWrap: 'wrap' }}>
              <button
                type="button"
                className={`btn btn-sm ${connectTab === 'android' ? 'btn-primary' : ''}`}
                onClick={() => { setConnectTab('android'); setPairingSession(null); }}
              >
                Android Phone / Tablet (Direct Wi-Fi / USB)
              </button>
              <button
                type="button"
                className={`btn btn-sm ${connectTab === 'ios' ? 'btn-primary' : ''}`}
                onClick={() => { setConnectTab('ios'); setPairingSession(null); }}
              >
                iPhone / iPad (Wi-Fi Sync & Continuity)
              </button>
              <button
                type="button"
                className={`btn btn-sm ${connectTab === 'other' ? 'btn-primary' : ''}`}
                onClick={() => setConnectTab('other')}
              >
                Smart TV / PC Daemon / QR
              </button>
            </div>

            {connectTab === 'android' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                <div style={{ fontSize: '12.5px', color: '#6ee7b7', background: 'rgba(16, 185, 129, 0.12)', padding: '10px 12px', borderRadius: '8px' }}>
                  <strong>Full Background Control (Screen OFF, Apps, SMS Read/Reply, Volume, Brightness, Bluetooth, Torch, Live Touch):</strong> Works 24/7 over Wi-Fi with zero webpage open on your phone.
                </div>

                {/* Detected USB or Already-Paired Wi-Fi Android Phones */}
                {bridges && bridges.android_connected.length > 0 && (
                  <div style={{ background: 'var(--bg-canvas)', padding: '12px', borderRadius: '8px', border: '1px solid rgba(56,189,248,0.3)' }}>
                    <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '8px' }}>
                      Detected Android Devices Ready Now:
                    </div>
                    {bridges.android_connected.map((ad) => (
                      <div key={ad.serial} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0' }}>
                        <div>
                          <strong>{ad.model}</strong> <span className="mono" style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>({ad.serial} · {ad.transport.toUpperCase()})</span>
                          {!ad.authorized && (
                            <div style={{ fontSize: '11px', color: '#fbbf24' }}>Tap &quot;Allow USB/Wireless Debugging&quot; prompt on your phone screen!</div>
                          )}
                        </div>
                        <button
                          className="btn btn-success btn-sm"
                          disabled={!ad.authorized || connectingNative}
                          onClick={() => {
                            if (ad.transport === 'usb') {
                              void connectAndroidDirect(ad.serial);
                            } else {
                              setAndroidConnectAddr(ad.serial);
                              void apiRequest<{ device: Device; adb_target: string }>('/devices/android/connect', {
                                method: 'POST',
                                body: JSON.stringify({ connect_address: ad.serial, name: ad.model }),
                              }).then(async (res) => {
                                setShowPairModal(false);
                                onNotify(`Connected ${res.device.name} in background mode!`);
                                await onRefresh();
                                onSelectDevice(res.device.id);
                              });
                            }
                          }}
                        >
                          <Wifi size={13} /> {ad.transport === 'usb' ? 'Enable Permanent Wi-Fi & Enroll' : 'Enroll Now'}
                        </button>
                      </div>
                    ))}
                  </div>
                )}

                {/* Method A: Cable-Free Wireless Debugging (Android 11+) */}
                <div style={{ background: 'var(--bg-canvas)', padding: '12px', borderRadius: '8px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  <div style={{ fontSize: '12.5px', fontWeight: 600 }}>
                    Cable-Free Wi-Fi Pairing (Android 11–15 Wireless Debugging):
                  </div>
                  <ol style={{ margin: 0, paddingLeft: '18px', fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.55 }}>
                    <li>On Android, open <strong>Settings → About Phone</strong> and tap <strong>Build Number 7 times</strong> to enable Developer Options.</li>
                    <li>Open <strong>Settings → System → Developer Options → Wireless Debugging</strong> and turn it <strong>ON</strong>.</li>
                    <li>Note the main <strong>IP address &amp; Port</strong> shown on that screen, then tap <strong>Pair device with pairing code</strong> (enter only if pairing for the first time):</li>
                  </ol>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                    <input
                      className="input"
                      placeholder="Main Wireless Debugging IP:Port (e.g. 192.168.1.42:41523)"
                      value={androidConnectAddr}
                      onChange={(e) => setAndroidConnectAddr(e.target.value)}
                    />
                    <input
                      className="input"
                      placeholder="Phone Name (optional, e.g. My Samsung S24)"
                      value={androidLabel}
                      onChange={(e) => setAndroidLabel(e.target.value)}
                    />
                    <input
                      className="input"
                      placeholder="Popup Pairing IP:Port (first time only, e.g. 192.168.1.42:37891)"
                      value={androidPairAddr}
                      onChange={(e) => setAndroidPairAddr(e.target.value)}
                    />
                    <input
                      className="input"
                      placeholder="6-Digit Pairing Code (first time only, e.g. 482910)"
                      value={androidPairCode}
                      onChange={(e) => setAndroidPairCode(e.target.value)}
                    />
                  </div>
                  <div style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                    Tip: You can also plug your Android phone in via USB cable for 5 seconds, click <strong>Refresh Detected</strong> above, and click <strong>Enable Permanent Wi-Fi &amp; Enroll</strong>.
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                    <button className="btn" onClick={() => setShowPairModal(false)}>Cancel</button>
                    <button
                      className="btn btn-primary"
                      disabled={connectingNative || (!androidConnectAddr && !androidPairAddr)}
                      onClick={() => void connectAndroidDirect()}
                    >
                      {connectingNative ? 'Connecting to Android OS...' : 'Pair & Connect Android (Zero Webpage)'}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {connectTab === 'ios' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                <div style={{ fontSize: '12.5px', color: '#6ee7b7', background: 'rgba(16, 185, 129, 0.12)', padding: '10px 12px', borderRadius: '8px' }}>
                  <strong>Native Apple Lockdown + macOS Continuity Bridge:</strong> Read &amp; reply to real SMS / iMessages, place cellular phone calls, view battery/cycle telemetry, list &amp; launch apps, and trigger remote Sleep / Restart with <strong>zero webpage open on your iPhone</strong>.
                </div>

                {bridges && bridges.ios_connected.length > 0 ? (
                  <div style={{ background: 'var(--bg-canvas)', padding: '12px', borderRadius: '8px', border: '1px solid rgba(56,189,248,0.3)' }}>
                    <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '8px' }}>
                      Detected iPhones / iPads on macOS Lockdown (`usbmuxd`):
                    </div>
                    {bridges.ios_connected.map((ios) => (
                      <div key={ios.udid} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0' }}>
                        <div>
                          <strong>{ios.device_name || ios.model || 'iPhone'}</strong>{' '}
                          <span className="mono" style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                            ({ios.wifi_enabled ? 'Wi-Fi Sync Active' : 'USB Connected'} {ios.os_version ? `· iOS ${ios.os_version}` : ''})
                          </span>
                        </div>
                        <button
                          className="btn btn-success btn-sm"
                          disabled={connectingNative}
                          onClick={() => void connectIosNative(ios.udid)}
                        >
                          Connect Native iPhone Bridge
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div style={{ background: 'var(--bg-canvas)', padding: '12px', borderRadius: '8px', fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.55 }}>
                    <strong>How to unlock full Wi-Fi Lockdown + Continuity on your iPhone:</strong>
                    <ol style={{ margin: '6px 0 0', paddingLeft: '18px' }}>
                      <li>For <strong>SMS / iMessage Read &amp; Reply + Cellular Calls</strong>: Ensure your iPhone &amp; Mac use the same Apple ID, and on iPhone enable <strong>Settings → Apps → Messages → Text Message Forwarding → This Mac</strong>.</li>
                      <li>For <strong>Remote Screen Sleep (OFF), Restart, Battery Health &amp; Installed Apps over Wi-Fi</strong>: Plug your iPhone into this Mac via USB once, tap <strong>Trust This Computer</strong> on your iPhone, and click <strong>Refresh Detected</strong> above (HOME OS automatically enables Wi-Fi Sync so you can unplug USB!).</li>
                    </ol>
                  </div>
                )}

                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <input
                    className="input"
                    style={{ flex: 1 }}
                    placeholder="iPhone Label (e.g. My iPhone 15 Pro)"
                    value={iosLabel}
                    onChange={(e) => setIosLabel(e.target.value)}
                  />
                  <button
                    className="btn btn-primary"
                    disabled={connectingNative}
                    onClick={() => void connectIosNative()}
                  >
                    {connectingNative ? 'Connecting...' : 'Enroll iPhone Native Bridge'}
                  </button>
                </div>
              </div>
            )}

            {connectTab === 'other' && (
              !pairingSession ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                  <div>
                    <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
                      Target Device / Daemon Type
                    </label>
                    <select className="select" style={{ width: '100%' }} value={pairAdapter} onChange={(e) => setPairAdapter(e.target.value)}>
                      <option value="google_tv">Google TV / Android TV (Remote v2 TLS)</option>
                      <option value="macos_agent">Secondary Mac Background Daemon (LaunchAgent)</option>
                      <option value="windows_agent">Windows Background Service (.NET 8)</option>
                      <option value="linux_agent">Linux Background Daemon (systemd)</option>
                      <option value="android_companion">Optional Browser Quick-Link (Android)</option>
                      <option value="ios_companion">Optional Browser Quick-Link (iOS)</option>
                    </select>
                  </div>

                  <div>
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px' }}>
                      Permissions Granted ({selectedScopes.length}/{ALL_SCOPES.length} enabled)
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '5px', maxHeight: '140px', overflowY: 'auto' }}>
                      {ALL_SCOPES.map((sc) => (
                        <label key={sc} style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '11px' }}>
                          <input
                            type="checkbox"
                            checked={selectedScopes.includes(sc)}
                            onChange={() => toggleScope(sc)}
                          />
                          <span className="mono">{sc}</span>
                        </label>
                      ))}
                    </div>
                  </div>

                  <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '8px' }}>
                    <button className="btn" onClick={() => setShowPairModal(false)}>
                      Cancel
                    </button>
                    <button className="btn btn-primary" onClick={() => void startPairing()}>
                      <QrCode size={14} /> Generate Pairing Token &amp; PIN
                    </button>
                  </div>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                  <div style={{ display: 'flex', gap: '18px', alignItems: 'center', flexWrap: 'wrap' }}>
                    <div
                      style={{
                        background: '#ffffff',
                        padding: '8px',
                        borderRadius: '12px',
                        width: '200px',
                        height: '200px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        flexShrink: 0,
                      }}
                      dangerouslySetInnerHTML={{ __html: pairingSession.qr_svg }}
                    />
                    <div style={{ flex: 1, minWidth: '210px' }}>
                      <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>6-Digit Pairing PIN</div>
                      <div className="mono tabular" style={{ fontSize: '28px', fontWeight: 700, letterSpacing: '0.15em', color: 'var(--accent-primary)' }}>
                        {pairingSession.pin}
                      </div>
                      <div style={{ fontSize: '11.5px', color: 'var(--text-muted)', marginTop: '8px' }}>
                        Session ID: <span className="mono">{pairingSession.session_id}</span>
                      </div>
                    </div>
                  </div>

                  <div style={{ background: 'var(--bg-canvas)', padding: '10px 12px', borderRadius: '8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px' }}>
                    <a
                      href={pairingSession.qr_uri}
                      target="_blank"
                      rel="noreferrer"
                      className="mono"
                      style={{ color: 'var(--accent-primary)', fontSize: '12px', wordBreak: 'break-all' }}
                    >
                      {pairingSession.qr_uri}
                    </a>
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => {
                        void navigator.clipboard?.writeText(pairingSession.qr_uri);
                        onNotify('Copied pairing URL.');
                      }}
                    >
                      <Copy size={13} /> Copy
                    </button>
                  </div>

                  {(pairAdapter === 'linux_agent' || pairAdapter === 'macos_agent' || pairAdapter === 'windows_agent') && (
                    <div style={{ background: 'var(--bg-canvas)', padding: '10px', borderRadius: '8px' }}>
                      <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>
                        Run on target computer to enroll background daemon:
                      </div>
                      <div className="mono" style={{ wordBreak: 'break-all', color: '#34d399', fontSize: '12px' }}>
                        {pairAdapter === 'macos_agent'
                          ? `homeos-mac-agent pair --url http://${pairingSession.lan_host}:${pairingSession.lan_port} --session ${pairingSession.session_id} --pin ${pairingSession.pin}`
                          : pairAdapter === 'windows_agent'
                          ? `HomeOs.WindowsAgent.exe pair --session ${pairingSession.session_id} --pin ${pairingSession.pin}`
                          : `homeos-linux-agent pair --session ${pairingSession.session_id} --pin ${pairingSession.pin}`}
                      </div>
                    </div>
                  )}

                  <form onSubmit={confirmDirectPairing} style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    <div style={{ fontSize: '12px', fontWeight: 600 }}>Complete Smart TV / Direct Enrollment:</div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                      <input
                        className="input"
                        placeholder="Device Name"
                        value={completeName}
                        onChange={(e) => setCompleteName(e.target.value)}
                        required
                      />
                      <input
                        className="input"
                        placeholder="Device IP"
                        value={completeIp}
                        onChange={(e) => setCompleteIp(e.target.value)}
                      />
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '6px' }}>
                      <button type="button" className="btn" onClick={() => setShowPairModal(false)}>
                        Close
                      </button>
                      <button type="submit" className="btn btn-success">
                        Complete Enrollment
                      </button>
                    </div>
                  </form>
                </div>
              )
            )}
          </div>
        </div>
      )}
    </div>
  );
};

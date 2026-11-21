import React, { useEffect, useRef, useState } from 'react';
import { Copy, Plus, QrCode, Radar, Trash2 } from 'lucide-react';
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

const ALL_SCOPES = [
  'telemetry:read',
  'notifications:read',
  'notifications:reply',
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
  const [pairAdapter, setPairAdapter] = useState('android_companion');
  const [pairTargetId, setPairTargetId] = useState<string | undefined>(undefined);
  const [selectedScopes, setSelectedScopes] = useState<string[]>([
    'telemetry:read',
    'notifications:read',
    'media:control',
    'volume:control',
    'files:browse',
  ]);
  const [pairingSession, setPairingSession] = useState<PairingSessionResponse | null>(null);
  const [netDiag, setNetDiag] = useState<NetDiagnostics | null>(null);
  const [overrideHostIp, setOverrideHostIp] = useState<string>('');

  // Direct Enrollment Completion helper (for Google TV / local desktop PIN confirmation)
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
    apiRequest<NetDiagnostics>('/system/network-diagnostics')
      .then((d) => {
        setNetDiag(d);
        if (!overrideHostIp && d.primary_lan_ip) {
          setOverrideHostIp(d.primary_lan_ip);
        }
      })
      .catch(() => {});
  }, [showPairModal, overrideHostIp]);

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
      onNotify(`Scan complete: found ${res.discovered_in_scan} Wi-Fi host(s)${subTxt}.`);
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
      onNotify('Added this computer with native hardware volume, media, clipboard & file controls.');
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
          <h2 style={{ fontSize: '19px', fontWeight: 700 }}>Device Inventory & Enrollment</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
            Discovered LAN hosts never receive private access automatically. Pair and grant explicit permissions to enable control.
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
              setShowPairModal(true);
            }}
          >
            <QrCode size={15} />
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
          <div className="empty-state">No devices enrolled yet. Click "Pair New Device" to enroll a companion or agent.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Device</th>
                <th>Category / OS</th>
                <th>Room</th>
                <th>IP Address</th>
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
                    {d.category} {d.os_name ? `(${d.os_name})` : ''}
                  </td>
                  <td>{d.room_name || '—'}</td>
                  <td className="mono">{d.ip_address || 'local-ws'}</td>
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
                    <button className="btn btn-sm btn-primary" onClick={() => startPairing(d)}>
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
              <option value="tv">Google TV / Android TV</option>
              <option value="smartphone">Smartphone</option>
              <option value="desktop">Desktop Computer</option>
              <option value="laptop">Laptop</option>
              <option value="speaker">Smart Speaker</option>
              <option value="appliance">Wi-Fi Appliance</option>
              <option value="network">Unknown LAN Device</option>
            </select>
            <select className="select" value={manualAdapter} onChange={(e) => setManualAdapter(e.target.value)}>
              <option value="google_tv">Adapter: Google TV Remote v2</option>
              <option value="android_companion">Adapter: Android Companion</option>
              <option value="ios_companion">Adapter: iOS Companion</option>
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

      {/* Pairing Wizard Modal */}
      {showPairModal && (
        <div className="modal-backdrop" onClick={() => setShowPairModal(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ fontSize: '17px', fontWeight: 700, marginBottom: '8px' }}>
              Cryptographic Device Enrollment
            </h3>
            {!pairingSession ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                <div>
                  <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
                    Platform Integration Adapter
                  </label>
                  <select className="select" style={{ width: '100%' }} value={pairAdapter} onChange={(e) => setPairAdapter(e.target.value)}>
                    <option value="android_companion">Android Companion App (Kotlin)</option>
                    <option value="ios_companion">iOS Companion App (SwiftUI)</option>
                    <option value="linux_agent">Linux Native Agent (Python)</option>
                    <option value="windows_agent">Windows Native Agent (.NET 8)</option>
                    <option value="macos_agent">macOS Native Agent (Swift)</option>
                    <option value="google_tv">Google TV / Android TV (Remote v2 TLS)</option>
                  </select>
                </div>

                <div>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px' }}>
                    Initial Granular Permissions to Authorize
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
                    {ALL_SCOPES.map((sc) => (
                      <label key={sc} style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px' }}>
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
                  <button className="btn btn-primary" onClick={() => startPairing()}>
                    Generate QR & Single-Use PIN
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
                      width: '226px',
                      height: '226px',
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
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '8px' }}>
                      Scan with your <strong>iPhone or Android Camera</strong> on the same Wi-Fi network to pair instantly.
                    </div>
                    <div style={{ fontSize: '11.5px', color: 'var(--text-muted)', marginTop: '8px' }}>
                      Session ID: <span className="mono">{pairingSession.session_id}</span>
                    </div>
                    <div style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                      Expires: {new Date(pairingSession.expires_at).toLocaleTimeString()}
                    </div>
                  </div>
                </div>

                <div style={{ background: 'var(--bg-canvas)', padding: '10px 12px', borderRadius: '8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px' }}>
                  <div style={{ overflow: 'hidden' }}>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '2px' }}>
                      Wi-Fi Companion Pairing Link (open on phone if camera doesn&apos;t scan):
                    </div>
                    <a
                      href={pairingSession.qr_uri}
                      target="_blank"
                      rel="noreferrer"
                      className="mono"
                      style={{ color: 'var(--accent-primary)', fontSize: '12px', wordBreak: 'break-all' }}
                    >
                      {pairingSession.qr_uri}
                    </a>
                  </div>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => {
                      void navigator.clipboard?.writeText(pairingSession.qr_uri);
                      onNotify('Copied Wi-Fi pairing URL to clipboard.');
                    }}
                  >
                    <Copy size={13} /> Copy
                  </button>
                </div>

                {netDiag && !netDiag.lan_socket_reachable && netDiag.primary_lan_ip !== '127.0.0.1' && (
                  <div style={{ fontSize: '12px', color: '#fca5a5', background: 'rgba(239, 68, 68, 0.14)', border: '1px solid rgba(239,68,68,0.35)', padding: '9px 11px', borderRadius: '8px' }}>
                    <strong>Why your phone link isn&apos;t loading:</strong> The running backend is bound to <span className="mono">{netDiag.bind_host}:{netDiag.bind_port}</span> (loopback only). Stop the old server and start with <span className="mono">homeos serve --host 0.0.0.0 --port 8420</span> (or reopen the updated <span className="mono">HOME OS.app</span>).
                  </div>
                )}

                {netDiag && netDiag.lan_socket_reachable && (
                  <div style={{ fontSize: '12px', color: '#6ee7b7', background: 'rgba(16, 185, 129, 0.12)', padding: '8px 10px', borderRadius: '8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
                    <span>✓ Hub listening on Wi-Fi (<span className="mono">{pairingSession.lan_host}:{pairingSession.lan_port}</span>)</span>
                    {netDiag.lan_interfaces.length > 1 && (
                      <select
                        className="select"
                        style={{ padding: '4px 8px', fontSize: '11.5px' }}
                        value={pairingSession.lan_host || overrideHostIp}
                        onChange={(e) => {
                          setOverrideHostIp(e.target.value);
                          void startPairing(undefined, e.target.value);
                        }}
                      >
                        {netDiag.lan_interfaces.map((iface) => (
                          <option key={iface.ip} value={iface.ip}>
                            {iface.label} ({iface.interface}): {iface.ip}
                          </option>
                        ))}
                      </select>
                    )}
                  </div>
                )}

                {netDiag && netDiag.macos_firewall_enabled && (
                  <div style={{ fontSize: '11.5px', color: '#fcd34d', background: 'rgba(245, 158, 11, 0.12)', padding: '8px 10px', borderRadius: '8px' }}>
                    macOS Firewall is enabled: if your phone times out opening the QR link, allow incoming connections for Python / HOME OS in <strong>System Settings → Network → Firewall → Options</strong> (and ensure your phone isn&apos;t on a Guest Wi-Fi with AP Isolation).
                  </div>
                )}

                {pairingSession.lan_host === '127.0.0.1' && (
                  <div style={{ fontSize: '12px', color: '#fcd34d', background: 'rgba(245, 158, 11, 0.12)', padding: '8px 10px', borderRadius: '8px' }}>
                    Note: No active private Wi-Fi IPv4 address was detected on this machine, so the link defaulted to 127.0.0.1. Connect to Wi-Fi so other devices can reach this hub.
                  </div>
                )}

                {(pairAdapter === 'linux_agent' || pairAdapter === 'macos_agent' || pairAdapter === 'windows_agent') && (
                  <div style={{ background: 'var(--bg-canvas)', padding: '10px', borderRadius: '8px' }}>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>
                      Run on target {pairAdapter === 'macos_agent' ? 'Mac' : pairAdapter === 'windows_agent' ? 'Windows PC' : 'Linux host'} to enroll:
                    </div>
                    <div className="mono" style={{ wordBreak: 'break-all', color: '#34d399' }}>
                      {pairAdapter === 'macos_agent'
                        ? `homeos-mac-agent pair --session ${pairingSession.session_id} --pin ${pairingSession.pin}`
                        : pairAdapter === 'windows_agent'
                        ? `HomeOs.WindowsAgent.exe pair --session ${pairingSession.session_id} --pin ${pairingSession.pin}`
                        : `homeos-linux-agent pair --session ${pairingSession.session_id} --pin ${pairingSession.pin}`}
                    </div>
                  </div>
                )}

                <form onSubmit={confirmDirectPairing} style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <div style={{ fontSize: '12px', fontWeight: 600 }}>Or Complete Direct Local/TV Enrollment:</div>
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
                      placeholder="Device IP (optional)"
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
            )}
          </div>
        </div>
      )}
    </div>
  );
};

import React, { useCallback, useEffect, useState } from 'react';
import {
  ArrowLeft,
  KeyRound,
  Lock,
  MessageSquare,
  Phone,
  Power,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Smartphone,
  Sun,
  Trash2,
  Volume2,
} from 'lucide-react';
import { apiRequest } from '../api/client';
import type { CommandExecution, Device, DeviceCapability, Room } from '../types/models';

interface Props {
  deviceId: string;
  rooms: Room[];
  onBack: () => void;
  onRefreshGlobal: () => Promise<void>;
  onNotify: (msg: string) => void;
}

interface InstalledAppItem {
  package_name: string;
  name: string;
}

interface SmsItem {
  id: string;
  address: string;
  body: string;
  service?: string;
  timestamp_ms: number;
  direction: 'incoming' | 'outgoing';
  read: boolean;
}

export const DeviceDetailsView: React.FC<Props> = ({
  deviceId,
  rooms,
  onBack,
  onRefreshGlobal,
  onNotify,
}) => {
  const [device, setDevice] = useState<Device | null>(null);
  const [commands, setCommands] = useState<CommandExecution[]>([]);
  const [editName, setEditName] = useState('');
  const [selectedRoom, setSelectedRoom] = useState('');
  const [volumeLevel, setVolumeLevel] = useState(50);
  const [volumeStream, setVolumeStream] = useState('media');
  const [brightnessLevel, setBrightnessLevel] = useState(65);
  const [appTarget, setAppTarget] = useState('');
  const [appFilter, setAppFilter] = useState('');
  const [installedApps, setInstalledApps] = useState<InstalledAppItem[] | null>(null);
  const [smsMessages, setSmsMessages] = useState<SmsItem[] | null>(null);
  const [smsNeedsFda, setSmsNeedsFda] = useState<string | null>(null);
  const [smsTo, setSmsTo] = useState('');
  const [smsBody, setSmsBody] = useState('');
  const [callNumber, setCallNumber] = useState('');
  const [screenSnapshot, setScreenSnapshot] = useState<{ url: string; width: number; height: number } | null>(null);
  const [remoteText, setRemoteText] = useState('');
  const [notifyText, setNotifyText] = useState('');
  const [clipText, setClipText] = useState('');
  const [netResult, setNetResult] = useState<string | null>(null);
  const [processSnapshot, setProcessSnapshot] = useState<Array<{ pid: number; name: string; cmdline?: string }> | null>(null);
  const [rotatedSecretInfo, setRotatedSecretInfo] = useState<{ key_version: number; device_token: string } | null>(null);

  const loadDetail = useCallback(async () => {
    try {
      const res = await apiRequest<{ device: Device; recent_commands: CommandExecution[] }>(
        `/devices/${deviceId}`
      );
      setDevice(res.device);
      setCommands(res.recent_commands);
      setEditName(res.device.name);
      setSelectedRoom(res.device.room_id || '');
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Failed to load device');
    }
  }, [deviceId, onNotify]);

  useEffect(() => {
    void loadDetail();
  }, [loadDetail]);

  if (!device) {
    return <div className="empty-state">Loading device telemetry and capability contract...</div>;
  }

  const grantedScopes = new Set(
    (device.permissions || []).filter((p) => p.status === 'granted').map((p) => p.permission_scope)
  );

  const sendCommand = async (capabilityKey: string, parameters: Record<string, unknown> = {}) => {
    try {
      const res = await apiRequest<{ command: CommandExecution }>(`/devices/${device.id}/commands`, {
        method: 'POST',
        body: JSON.stringify({ capability_key: capabilityKey, parameters }),
      });
      const cmd = res.command;
      if (cmd.status === 'failed') {
        onNotify(`${capabilityKey} failed: ${cmd.error_message || 'Device error'}`);
        await loadDetail();
        return;
      }
      if (capabilityKey === 'processes.list' && Array.isArray(cmd.result.processes)) {
        setProcessSnapshot(cmd.result.processes as Array<{ pid: number; name: string; cmdline?: string }>);
      }
      if (capabilityKey === 'apps.list' && Array.isArray(cmd.result.apps)) {
        setInstalledApps(cmd.result.apps as InstalledAppItem[]);
      }
      if (capabilityKey === 'sms.list') {
        if (cmd.result.needs_full_disk_access) {
          setSmsNeedsFda(String(cmd.result.reason || 'Full Disk Access required'));
        } else {
          setSmsNeedsFda(null);
        }
        if (Array.isArray(cmd.result.messages)) {
          setSmsMessages(cmd.result.messages as SmsItem[]);
        }
      }
      if (capabilityKey === 'screen.capture' && typeof cmd.result.image_data_url === 'string') {
        setScreenSnapshot({
          url: cmd.result.image_data_url,
          width: Number(cmd.result.width || 1080),
          height: Number(cmd.result.height || 2400),
        });
      }
      if (capabilityKey === 'network.ping') {
        const rtt = cmd.result.rtt_ms != null ? `${cmd.result.rtt_ms} ms` : 'timeout';
        setNetResult(`Ping ${String(cmd.result.ip_address || '')}: ${cmd.result.reachable ? `Reachable (${rtt})` : 'Unreachable'}`);
      } else if (capabilityKey === 'network.port_scan' && Array.isArray(cmd.result.open_services)) {
        const svcs = (cmd.result.open_services as Array<{ port: number; service: string }>)
          .map((s) => `${s.port}/${s.service}`)
          .join(', ');
        setNetResult(`Open TCP services: ${svcs || 'None listening (ports firewalled/closed)'}`);
      } else if (capabilityKey === 'network.wake_on_lan') {
        setNetResult(`Sent 102-byte Magic Packet to ${String(cmd.result.mac_address || '')}`);
      }
      onNotify(`${capabilityKey} → ${cmd.status} (${cmd.verification_method || 'executed'})`);
      await loadDetail();
      await onRefreshGlobal();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Command failed');
    }
  };

  const handleEnrollNetwork = async () => {
    try {
      await apiRequest(`/devices/${device.id}/enroll-network`, { method: 'POST' });
      onNotify('Enabled Wi-Fi Network Tools (Ping, Port Scan, Wake-on-LAN) for this device.');
      await loadDetail();
      await onRefreshGlobal();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Failed to enroll network device');
    }
  };

  const saveMetadata = async (e: React.FormEvent) => {
    e.preventDefault();
    await apiRequest(`/devices/${device.id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        name: editName,
        room_id: selectedRoom || null,
        clear_room: selectedRoom === '',
      }),
    });
    onNotify('Device identity & room assignment updated.');
    await loadDetail();
    await onRefreshGlobal();
  };

  const togglePermission = async (scope: string, currentlyGranted: boolean) => {
    await apiRequest(`/devices/${device.id}/permissions`, {
      method: 'POST',
      body: JSON.stringify({
        permission_scope: scope,
        status: currentlyGranted ? 'revoked' : 'granted',
      }),
    });
    onNotify(`Permission '${scope}' ${currentlyGranted ? 'revoked' : 'granted'}.`);
    await loadDetail();
  };

  const handleRotateKeys = async () => {
    const res = await apiRequest<{ key_version: number; device_token: string }>(
      `/devices/${device.id}/rotate-credentials`,
      { method: 'POST' }
    );
    setRotatedSecretInfo(res);
    onNotify(`Rotated device credentials to key version v${res.key_version}.`);
    await loadDetail();
  };

  const handleIsolateToggle = async () => {
    if (device.lifecycle_state === 'isolated') {
      await apiRequest(`/devices/${device.id}/restore`, { method: 'POST' });
      onNotify('Device restored from quarantine.');
    } else {
      await apiRequest(`/devices/${device.id}/isolate`, {
        method: 'POST',
        body: JSON.stringify({ reason: 'Operator manual quarantine' }),
      });
      onNotify('Device isolated; active sessions terminated.');
    }
    await loadDetail();
    await onRefreshGlobal();
  };

  const handleReconnect = async () => {
    await apiRequest(`/devices/${device.id}/reconnect`, { method: 'POST' });
    onNotify('Refreshed adapter connection status.');
    await loadDetail();
  };

  const handleRemove = async () => {
    await apiRequest(`/devices/${device.id}`, { method: 'DELETE' });
    onNotify('Device removed and credentials revoked.');
    await onRefreshGlobal();
    onBack();
  };

  const handleScreenTap = (e: React.MouseEvent<HTMLImageElement>) => {
    if (!screenSnapshot) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const relX = (e.clientX - rect.left) / rect.width;
    const relY = (e.clientY - rect.top) / rect.height;
    const targetX = Math.round(relX * screenSnapshot.width);
    const targetY = Math.round(relY * screenSnapshot.height);
    void sendCommand('remote.input', { mode: 'tap', x: targetX, y: targetY }).then(() => {
      setTimeout(() => void sendCommand('screen.capture', {}), 650);
    });
  };

  const renderInteractiveCapability = (cap: DeviceCapability) => {
    const hasPerm = grantedScopes.has(cap.requires_permission);
    if (!cap.supported || !hasPerm || !device.is_registered) {
      return (
        <div
          key={cap.id}
          style={{
            padding: '13px',
            borderRadius: '10px',
            background: 'var(--bg-canvas)',
            border: '1px solid var(--border-subtle)',
            opacity: 0.9,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px' }}>
            <strong>{cap.display_name}</strong>
            <span className="badge badge-offline">{!cap.supported ? 'Unsupported' : 'Permission Locked'}</span>
          </div>
          <div style={{ fontSize: '12px', color: '#fbbf24', marginTop: '6px' }}>
            {!cap.supported
              ? cap.unavailable_reason || 'Not supported on this operating system or hardware.'
              : `Requires '${cap.requires_permission}' permission.`}
          </div>
          {cap.supported && !hasPerm && device.is_registered && (
            <button
              className="btn btn-sm btn-success"
              style={{ marginTop: '8px' }}
              onClick={() => void togglePermission(cap.requires_permission, false)}
            >
              Grant &apos;{cap.requires_permission}&apos; Now
            </button>
          )}
        </div>
      );
    }

    return (
      <div
        key={cap.id}
        style={{
          padding: '14px',
          borderRadius: '10px',
          background: 'var(--bg-canvas)',
          border: '1px solid rgba(56, 189, 248, 0.22)',
          display: 'flex',
          flexDirection: 'column',
          gap: '10px',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div style={{ fontWeight: 600 }}>{cap.display_name}</div>
            <div className="mono" style={{ color: 'var(--text-muted)', fontSize: '11px' }}>
              {cap.capability_key} · scope: {cap.requires_permission}
            </div>
          </div>
          <span className="badge badge-online">Ready</span>
        </div>

        {/* POWER: Screen ON / Screen OFF / Restart / Shutdown */}
        {cap.capability_key === 'power.set' && (
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {device.adapter_type !== 'ios_native' && (
              <button
                className="btn btn-success btn-sm"
                onClick={() => sendCommand('power.set', { on: true, action: 'wake_unlock' })}
              >
                <Power size={13} /> Screen ON (Wake)
              </button>
            )}
            <button
              className="btn btn-sm"
              onClick={() => sendCommand('power.set', { on: false, action: 'sleep' })}
            >
              Screen OFF (Lock / Sleep)
            </button>
            {(device.adapter_type === 'android_adb' || device.adapter_type === 'ios_native') && (
              <>
                <button
                  className="btn btn-sm"
                  onClick={() => {
                    if (window.confirm(`Restart ${device.name}?`)) {
                      void sendCommand('power.set', { action: 'reboot' });
                    }
                  }}
                >
                  Restart
                </button>
                <button
                  className="btn btn-sm btn-danger"
                  onClick={() => {
                    if (window.confirm(`Power OFF ${device.name} completely?`)) {
                      void sendCommand('power.set', { action: 'shutdown' });
                    }
                  }}
                >
                  Power Off
                </button>
              </>
            )}
          </div>
        )}

        {/* INSTALLED APPS BROWSER & 1-CLICK LAUNCHER */}
        {cap.capability_key === 'apps.list' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button className="btn btn-sm btn-primary" onClick={() => sendCommand('apps.list', {})}>
                <Smartphone size={13} /> {installedApps ? `Refresh Installed Apps (${installedApps.length})` : 'Load Installed Apps'}
              </button>
              {installedApps && (
                <input
                  className="input"
                  style={{ flex: 1, padding: '4px 8px', fontSize: '12px' }}
                  placeholder="Filter apps (e.g. WhatsApp, YouTube, Settings)..."
                  value={appFilter}
                  onChange={(e) => setAppFilter(e.target.value)}
                />
              )}
            </div>
            {installedApps && (
              <div style={{ maxHeight: '190px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                {installedApps
                  .filter(
                    (a) =>
                      !appFilter ||
                      a.name.toLowerCase().includes(appFilter.toLowerCase()) ||
                      a.package_name.toLowerCase().includes(appFilter.toLowerCase())
                  )
                  .slice(0, 60)
                  .map((a) => (
                    <div
                      key={a.package_name}
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        padding: '5px 8px',
                        borderRadius: '6px',
                        background: 'rgba(255,255,255,0.03)',
                        fontSize: '12px',
                      }}
                    >
                      <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        <strong>{a.name}</strong>{' '}
                        <span className="mono" style={{ fontSize: '10.5px', color: 'var(--text-muted)' }}>
                          {a.package_name}
                        </span>
                      </div>
                      <div style={{ display: 'flex', gap: '5px', flexShrink: 0 }}>
                        <button
                          className="btn btn-sm btn-primary"
                          style={{ padding: '2px 8px', fontSize: '11px' }}
                          onClick={() => sendCommand('apps.launch', { package_name: a.package_name })}
                        >
                          Launch
                        </button>
                        {device.adapter_type === 'android_adb' && (
                          <button
                            className="btn btn-sm btn-danger"
                            style={{ padding: '2px 7px', fontSize: '11px' }}
                            onClick={() => sendCommand('apps.stop', { package_name: a.package_name })}
                          >
                            Stop
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
              </div>
            )}
          </div>
        )}

        {/* DIRECT APP / URL LAUNCH */}
        {(cap.capability_key === 'apps.launch' || cap.capability_key === 'shortcuts.run') && (
          <div style={{ display: 'flex', gap: '8px' }}>
            <input
              className="input"
              style={{ flex: 1 }}
              placeholder={
                cap.capability_key === 'shortcuts.run'
                  ? 'Shortcut name'
                  : device.adapter_type === 'android_adb'
                  ? 'App name (e.g. WhatsApp, YouTube, Settings) or https://...'
                  : device.adapter_type === 'local_host'
                  ? 'App name (e.g. Safari, Music, Notes) or https://...'
                  : 'Bundle ID / App / https://...'
              }
              value={appTarget}
              onChange={(e) => setAppTarget(e.target.value)}
            />
            <button
              className="btn btn-sm btn-primary"
              onClick={() => {
                const trimmed = appTarget.trim();
                if (!trimmed) return;
                if (trimmed.startsWith('http://') || trimmed.startsWith('https://') || trimmed.includes('://')) {
                  void sendCommand(cap.capability_key, { url: trimmed, app_link: trimmed, target: trimmed });
                  return;
                }
                const paramKey =
                  cap.capability_key === 'shortcuts.run'
                    ? 'shortcut_name'
                    : device.adapter_type === 'google_tv'
                    ? 'app_link'
                    : 'package_name';
                void sendCommand(cap.capability_key, { [paramKey]: trimmed, target: trimmed, app_id: trimmed });
              }}
            >
              Launch
            </button>
          </div>
        )}

        {/* VIEW REAL SMS / iMESSAGE INBOX & CLICK TO REPLY */}
        {cap.capability_key === 'sms.list' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <button className="btn btn-sm btn-primary" style={{ alignSelf: 'flex-start' }} onClick={() => sendCommand('sms.list', { limit: 30 })}>
              <MessageSquare size={13} /> Sync &amp; View Real Texts (SMS / iMessage)
            </button>
            {smsNeedsFda && (
              <div style={{ fontSize: '11.5px', color: '#fcd34d', background: 'rgba(245,158,11,0.12)', padding: '8px', borderRadius: '6px' }}>
                <div>{smsNeedsFda}</div>
                <button
                  className="btn btn-sm"
                  style={{ marginTop: '6px' }}
                  onClick={() => void apiRequest('/system/open-privacy-settings', { method: 'POST', body: JSON.stringify({ pane: 'full_disk_access' }) })}
                >
                  Open macOS Full Disk Access Settings
                </button>
              </div>
            )}
            {smsMessages && (
              <div style={{ maxHeight: '220px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {smsMessages.length === 0 ? (
                  <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>No SMS messages found in inbox.</div>
                ) : (
                  smsMessages.map((m) => (
                    <div
                      key={`${m.id}-${m.timestamp_ms}`}
                      style={{
                        padding: '7px 9px',
                        borderRadius: '7px',
                        background: m.direction === 'incoming' ? 'rgba(56,189,248,0.08)' : 'rgba(16,185,129,0.08)',
                        fontSize: '12px',
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '3px' }}>
                        <strong>
                          {m.direction === 'incoming' ? `From ${m.address}` : `To ${m.address}`}
                        </strong>
                        <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                          <span style={{ fontSize: '10.5px', color: 'var(--text-muted)' }}>
                            {new Date(m.timestamp_ms).toLocaleTimeString()}
                          </span>
                          <button
                            className="btn btn-sm"
                            style={{ padding: '1px 7px', fontSize: '10.5px' }}
                            onClick={() => setSmsTo(m.address)}
                          >
                            Reply
                          </button>
                        </div>
                      </div>
                      <div style={{ color: 'var(--text-primary)', whiteSpace: 'pre-wrap' }}>{m.body}</div>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        )}

        {/* SEND & REPLY TO REAL SMS / TEXT MESSAGES */}
        {cap.capability_key === 'sms.send' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '7px' }}>
            <input
              className="input"
              placeholder="Recipient phone number (e.g. +2348012345678)"
              value={smsTo}
              onChange={(e) => setSmsTo(e.target.value)}
            />
            <div style={{ display: 'flex', gap: '8px' }}>
              <input
                className="input"
                style={{ flex: 1 }}
                placeholder="Type SMS / text reply..."
                value={smsBody}
                onChange={(e) => setSmsBody(e.target.value)}
              />
              <button
                className="btn btn-sm btn-success"
                onClick={() => {
                  if (!smsTo.trim() || !smsBody.trim()) return;
                  void sendCommand('sms.send', { to: smsTo.trim(), message: smsBody.trim() });
                  setSmsBody('');
                }}
              >
                Send Text
              </button>
            </div>
          </div>
        )}

        {/* CELLULAR CALL CONTROL */}
        {cap.capability_key === 'call.control' && (
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            <input
              className="input"
              style={{ flex: 1, minWidth: '140px' }}
              placeholder="Phone number to dial..."
              value={callNumber}
              onChange={(e) => setCallNumber(e.target.value)}
            />
            <button
              className="btn btn-sm btn-success"
              onClick={() => {
                if (!callNumber.trim()) return;
                void sendCommand('call.control', { action: 'dial', number: callNumber.trim() });
              }}
            >
              <Phone size={13} /> Call
            </button>
            {device.adapter_type === 'android_adb' && (
              <>
                <button className="btn btn-sm" onClick={() => sendCommand('call.control', { action: 'answer' })}>
                  Answer
                </button>
                <button className="btn btn-sm btn-danger" onClick={() => sendCommand('call.control', { action: 'end' })}>
                  End Call
                </button>
              </>
            )}
          </div>
        )}

        {/* VOLUME CONTROL */}
        {cap.capability_key === 'volume.set' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {device.adapter_type === 'android_adb' && (
              <div style={{ display: 'flex', gap: '6px' }}>
                <select className="select" style={{ fontSize: '11.5px', padding: '3px 7px' }} value={volumeStream} onChange={(e) => setVolumeStream(e.target.value)}>
                  <option value="media">Media Volume</option>
                  <option value="ring">Ringtone Volume</option>
                  <option value="notification">Notification Volume</option>
                  <option value="alarm">Alarm Volume</option>
                </select>
                <button className="btn btn-sm" onClick={() => sendCommand('volume.set', { mute: true })}>
                  Mute / Unmute
                </button>
              </div>
            )}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Volume2 size={15} />
              {device.adapter_type === 'google_tv' ? (
                <>
                  <button className="btn btn-sm" onClick={() => sendCommand('volume.set', { action: 'down' })}>Vol -</button>
                  <button className="btn btn-sm" onClick={() => sendCommand('volume.set', { action: 'up' })}>Vol +</button>
                  <button className="btn btn-sm" onClick={() => sendCommand('volume.set', { action: 'mute' })}>Mute</button>
                </>
              ) : (
                <>
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={volumeLevel}
                    onChange={(e) => setVolumeLevel(Number(e.target.value))}
                    style={{ flex: 1 }}
                  />
                  <span className="tabular" style={{ width: '36px' }}>{volumeLevel}%</span>
                  <button
                    className="btn btn-sm btn-primary"
                    onClick={() => sendCommand('volume.set', { level: volumeLevel, stream: volumeStream })}
                  >
                    Apply
                  </button>
                </>
              )}
            </div>
          </div>
        )}

        {/* DISPLAY BRIGHTNESS */}
        {cap.capability_key === 'brightness.set' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Sun size={15} />
            <input
              type="range"
              min={1}
              max={100}
              value={brightnessLevel}
              onChange={(e) => setBrightnessLevel(Number(e.target.value))}
              style={{ flex: 1 }}
            />
            <span className="tabular" style={{ width: '36px' }}>{brightnessLevel}%</span>
            <button className="btn btn-sm btn-primary" onClick={() => sendCommand('brightness.set', { level: brightnessLevel })}>
              Set
            </button>
            <button className="btn btn-sm" onClick={() => sendCommand('brightness.set', { auto: true })}>
              Auto
            </button>
          </div>
        )}

        {/* HARDWARE RADIOS & SYSTEM TOGGLES */}
        {cap.capability_key === 'hardware.toggle' && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px', fontSize: '12px' }}>
            {[
              { key: 'bluetooth', label: 'Bluetooth' },
              { key: 'dnd', label: 'Do Not Disturb' },
              { key: 'mobile_data', label: 'Mobile Data' },
              { key: 'auto_rotate', label: 'Auto-Rotate' },
              { key: 'stay_awake', label: 'Stay Awake' },
              { key: 'notification_shade', label: 'Notif Shade' },
            ].map((item) => (
              <div
                key={item.key}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: '5px 8px',
                  borderRadius: '6px',
                  background: 'rgba(255,255,255,0.03)',
                }}
              >
                <span>{item.label}</span>
                <div style={{ display: 'flex', gap: '4px' }}>
                  <button
                    className="btn btn-sm btn-success"
                    style={{ padding: '2px 6px', fontSize: '10.5px' }}
                    onClick={() => sendCommand('hardware.toggle', { feature: item.key, enabled: true })}
                  >
                    ON
                  </button>
                  <button
                    className="btn btn-sm"
                    style={{ padding: '2px 6px', fontSize: '10.5px' }}
                    onClick={() => sendCommand('hardware.toggle', { feature: item.key, enabled: false })}
                  >
                    OFF
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* FLASHLIGHT / TORCH */}
        {cap.capability_key === 'flashlight.set' && (
          <div style={{ display: 'flex', gap: '8px' }}>
            <button className="btn btn-primary btn-sm" onClick={() => sendCommand('flashlight.set', { enabled: true })}>
              Torch ON
            </button>
            <button className="btn btn-sm" onClick={() => sendCommand('flashlight.set', { enabled: false })}>
              Torch OFF
            </button>
          </div>
        )}

        {/* MEDIA PLAYBACK */}
        {cap.capability_key === 'media.control' && (
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            {['previous', 'play', 'pause', 'next'].map((act) => (
              <button key={act} className="btn btn-sm" onClick={() => sendCommand('media.control', { action: act })}>
                {act}
              </button>
            ))}
          </div>
        )}

        {/* LIVE SCREEN MIRROR SNAPSHOT + CLICK-TO-TAP */}
        {cap.capability_key === 'screen.capture' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
              <button className="btn btn-sm btn-primary" onClick={() => sendCommand('screen.capture', {})}>
                Capture Live Screen
              </button>
              {device.adapter_type === 'android_adb' && (
                <>
                  <button className="btn btn-sm" onClick={() => sendCommand('remote.input', { mode: 'key', key: 'HOME' })}>Home</button>
                  <button className="btn btn-sm" onClick={() => sendCommand('remote.input', { mode: 'key', key: 'BACK' })}>Back</button>
                  <button className="btn btn-sm" onClick={() => sendCommand('remote.input', { mode: 'key', key: 'RECENTS' })}>Recents</button>
                  <button className="btn btn-sm" onClick={() => sendCommand('remote.input', { mode: 'swipe', x1: 540, y1: 1700, x2: 540, y2: 450 })}>Swipe Up</button>
                </>
              )}
            </div>
            {screenSnapshot && (
              <div>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>
                  Click anywhere on the phone screen below to tap that exact spot ({screenSnapshot.width}×{screenSnapshot.height}):
                </div>
                <img
                  src={screenSnapshot.url}
                  alt="Live Device Screen"
                  onClick={handleScreenTap}
                  style={{
                    maxWidth: '240px',
                    borderRadius: '10px',
                    border: '1px solid var(--accent-primary)',
                    cursor: 'crosshair',
                    display: 'block',
                  }}
                />
              </div>
            )}
          </div>
        )}

        {/* REMOTE HARDWARE KEYS & TEXT INPUT */}
        {cap.capability_key === 'remote.input' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
              {['WAKE', 'SLEEP', 'HOME', 'BACK', 'RECENTS', 'ENTER'].map((k) => (
                <button key={k} className="btn btn-sm" onClick={() => sendCommand('remote.input', { mode: 'key', key: k })}>
                  {k}
                </button>
              ))}
            </div>
            <div style={{ display: 'flex', gap: '6px' }}>
              <input
                className="input"
                style={{ flex: 1 }}
                placeholder="Type text directly into focused field on phone..."
                value={remoteText}
                onChange={(e) => setRemoteText(e.target.value)}
              />
              <button
                className="btn btn-sm btn-primary"
                onClick={() => {
                  if (!remoteText) return;
                  void sendCommand('remote.input', { mode: 'text', text: remoteText });
                  setRemoteText('');
                }}
              >
                Type
              </button>
            </div>
          </div>
        )}

        {cap.capability_key === 'tv.navigate' && (
          <div>
            <div className="dpad-grid">
              <div />
              <button className="btn btn-sm" onClick={() => sendCommand('tv.navigate', { direction: 'DPAD_UP' })}>▲</button>
              <div />
              <button className="btn btn-sm" onClick={() => sendCommand('tv.navigate', { direction: 'DPAD_LEFT' })}>◀</button>
              <button className="btn btn-primary btn-sm" onClick={() => sendCommand('tv.navigate', { direction: 'DPAD_CENTER' })}>OK</button>
              <button className="btn btn-sm" onClick={() => sendCommand('tv.navigate', { direction: 'DPAD_RIGHT' })}>▶</button>
              <div />
              <button className="btn btn-sm" onClick={() => sendCommand('tv.navigate', { direction: 'DPAD_DOWN' })}>▼</button>
              <div />
            </div>
            <div style={{ display: 'flex', justifyContent: 'center', gap: '8px' }}>
              <button className="btn btn-sm" onClick={() => sendCommand('tv.navigate', { direction: 'BACK' })}>Back</button>
              <button className="btn btn-sm" onClick={() => sendCommand('tv.navigate', { direction: 'HOME' })}>Home</button>
            </div>
          </div>
        )}

        {cap.capability_key === 'device.ring' && (
          <div>
            <button
              className="btn btn-sm btn-primary"
              onClick={() => sendCommand('device.ring', { message: 'Find My Phone triggered from HOME OS' })}
            >
              Ring Device Loudly (Siren + Vibrate)
            </button>
          </div>
        )}

        {cap.capability_key === 'device.notify' && (
          <div style={{ display: 'flex', gap: '8px' }}>
            <input
              className="input"
              style={{ flex: 1 }}
              placeholder="Alert message to display on device..."
              value={notifyText}
              onChange={(e) => setNotifyText(e.target.value)}
            />
            <button
              className="btn btn-sm btn-primary"
              onClick={() => {
                const msg = notifyText.trim() || 'Hello from HOME OS Command Centre!';
                void sendCommand('device.notify', { title: 'HOME OS', message: msg });
                setNotifyText('');
              }}
            >
              Send Alert
            </button>
          </div>
        )}

        {cap.capability_key === 'clipboard.send' && (
          <div style={{ display: 'flex', gap: '8px' }}>
            <input
              className="input"
              style={{ flex: 1 }}
              placeholder="Text, link, or code to send to device..."
              value={clipText}
              onChange={(e) => setClipText(e.target.value)}
            />
            <button
              className="btn btn-sm btn-primary"
              onClick={() => {
                if (!clipText.trim()) return;
                void sendCommand('clipboard.send', { text: clipText.trim() });
                setClipText('');
              }}
            >
              Send Text
            </button>
          </div>
        )}

        {(cap.capability_key === 'network.ping' ||
          cap.capability_key === 'network.port_scan' ||
          cap.capability_key === 'network.wake_on_lan') && (
          <div>
            <button className="btn btn-sm btn-primary" onClick={() => sendCommand(cap.capability_key, {})}>
              {cap.capability_key === 'network.ping'
                ? 'Run Live Wi-Fi Ping'
                : cap.capability_key === 'network.port_scan'
                ? 'Scan Open TCP Services'
                : 'Broadcast Wake-on-LAN Magic Packet'}
            </button>
            {netResult && (
              <div className="mono" style={{ marginTop: '8px', fontSize: '11.5px', color: '#38bdf8' }}>
                {netResult}
              </div>
            )}
          </div>
        )}

        {cap.capability_key === 'processes.list' && (
          <div>
            <button className="btn btn-sm btn-primary" onClick={() => sendCommand('processes.list', { limit: 12 })}>
              Inspect Active User Processes
            </button>
            {processSnapshot && (
              <div style={{ marginTop: '8px', maxHeight: '150px', overflowY: 'auto', fontSize: '11.5px' }} className="mono">
                {processSnapshot.map((p) => (
                  <div key={p.pid} style={{ padding: '2px 0', borderBottom: '1px solid var(--border-subtle)' }}>
                    [{p.pid}] {p.name} {p.cmdline ? `— ${p.cmdline}` : ''}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {cap.capability_key === 'notifications.sync' && (
          <div>
            <button
              className="btn btn-sm btn-primary"
              onClick={() =>
                void apiRequest<{ synced_messages: number; synced_notifications: number }>('/communications/sync', {
                  method: 'POST',
                  body: JSON.stringify({ device_id: device.id }),
                }).then((r) => onNotify(`Synced ${r.synced_messages} SMS & ${r.synced_notifications} active notifications into Communications!`))
              }
            >
              Sync Live Notifications (WhatsApp, Telegram, SMS)
            </button>
          </div>
        )}

        {(cap.capability_key === 'telemetry.status' || cap.capability_key === 'lock.lock' || cap.capability_key === 'lock.unlock') && (
          <div>
            <button className="btn btn-sm" onClick={() => sendCommand(cap.capability_key, {})}>
              Execute {cap.display_name}
            </button>
          </div>
        )}
      </div>
    );
  };

  const relevantScopes = Array.from(
    new Set([
      'telemetry:read',
      ...(device.capabilities || []).map((c) => c.requires_permission),
      ...(device.permissions || []).map((p) => p.permission_scope),
    ])
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <button className="btn btn-sm" onClick={onBack}>
            <ArrowLeft size={15} /> Back
          </button>
          <div>
            <h2 style={{ fontSize: '20px', fontWeight: 700 }}>{device.name}</h2>
            <div style={{ fontSize: '12.5px', color: 'var(--text-secondary)' }}>
              {device.category} · {device.manufacturer || 'Unknown'} · {device.os_name || device.adapter_type} {device.os_version || ''}
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          {!device.is_registered && (
            <button className="btn btn-sm btn-primary" onClick={handleEnrollNetwork}>
              <ShieldCheck size={14} /> Enable Wi-Fi Network Tools
            </button>
          )}
          <button className="btn btn-sm" onClick={handleReconnect}>
            <RefreshCw size={14} /> Reconnect / Probe
          </button>
          {device.is_registered && (
            <>
              <button className="btn btn-sm" onClick={handleRotateKeys}>
                <KeyRound size={14} /> Rotate Keys (v{device.key_version || 1})
              </button>
              <button className="btn btn-sm btn-danger" onClick={handleIsolateToggle}>
                <ShieldAlert size={14} />
                {device.lifecycle_state === 'isolated' ? 'Restore from Quarantine' : 'Isolate Device'}
              </button>
            </>
          )}
          <button className="btn btn-sm btn-danger" onClick={handleRemove}>
            <Trash2 size={14} /> Remove
          </button>
        </div>
      </div>

      {rotatedSecretInfo && (
        <div className="card" style={{ borderColor: 'var(--accent-success)' }}>
          <div style={{ fontWeight: 600, color: '#34d399' }}>
            Credentials Rotated to Key Version v{rotatedSecretInfo.key_version}
          </div>
          <div className="mono" style={{ marginTop: '4px', wordBreak: 'break-all' }}>
            New Device Token (shown once): {rotatedSecretInfo.device_token}
          </div>
        </div>
      )}

      {/* Identity, Telemetry & Room Assignment */}
      <div className="grid-2">
        <div className="card">
          <div className="section-title" style={{ marginBottom: '12px' }}>Identity &amp; Room Assignment</div>
          <form onSubmit={saveMetadata} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            <div>
              <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Device Name</label>
              <input className="input" style={{ width: '100%' }} value={editName} onChange={(e) => setEditName(e.target.value)} />
            </div>
            <div>
              <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Assigned Room</label>
              <select className="select" style={{ width: '100%' }} value={selectedRoom} onChange={(e) => setSelectedRoom(e.target.value)}>
                <option value="">Unassigned</option>
                {rooms.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
            </div>
            <button type="submit" className="btn btn-primary" style={{ alignSelf: 'flex-start' }}>
              Save Device Profile
            </button>
          </form>
        </div>

        <div className="card">
          <div className="section-title" style={{ marginBottom: '12px' }}>Live Host &amp; Integration Health</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', fontSize: '12.5px' }}>
            <div>Lifecycle: <strong>{device.lifecycle_state}</strong></div>
            <div>Status: <strong>{device.connection_status}</strong></div>
            <div>IP Address: <span className="mono">{device.ip_address || '—'}</span></div>
            <div>Integration Health: <strong>{device.integration_health}</strong></div>
            <div>Battery: <strong>{device.battery_level !== null ? `${device.battery_level}%` : 'N/A (AC)'}</strong></div>
            <div>Last Seen: <span>{device.last_seen_at ? new Date(device.last_seen_at).toLocaleTimeString() : '—'}</span></div>
          </div>
          {Object.keys(device.health_details || {}).length > 0 && (
            <div style={{ marginTop: '12px', paddingTop: '10px', borderTop: '1px solid var(--border-subtle)' }}>
              <div style={{ fontSize: '11.5px', color: 'var(--text-muted)', marginBottom: '6px' }}>Agent Telemetry Snapshot</div>
              <div className="mono" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px', color: 'var(--text-secondary)' }}>
                {Object.entries(device.health_details)
                  .filter(([, v]) => typeof v !== 'object')
                  .map(([k, v]) => (
                    <div key={k}>
                      {k}: <strong style={{ color: 'var(--text-primary)' }}>{v === null || v === undefined ? 'N/A — unavailable' : String(v)}</strong>
                    </div>
                  ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Capabilities Deck */}
      <div className="card">
        <div className="section-title" style={{ marginBottom: '12px' }}>
          Advertised Capabilities &amp; Live Hardware Controls
        </div>
        <div className="grid-devices">
          {(device.capabilities || []).map((cap) => renderInteractiveCapability(cap))}
        </div>
      </div>

      {/* Granular Permission Controls */}
      {device.is_registered && (
        <div className="card">
          <div className="section-title" style={{ marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Lock size={16} color="var(--accent-primary)" />
            Granular Device Permissions
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: '10px' }}>
            {relevantScopes.map((scope) => {
              const granted = grantedScopes.has(scope);
              return (
                <div
                  key={scope}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '10px 12px',
                    borderRadius: '8px',
                    background: 'var(--bg-canvas)',
                  }}
                >
                  <span className="mono">{scope}</span>
                  <button
                    className={`btn btn-sm ${granted ? 'btn-danger' : 'btn-success'}`}
                    onClick={() => togglePermission(scope, granted)}
                  >
                    {granted ? 'Revoke' : 'Grant'}
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Command Execution Audit Log */}
      <div className="card">
        <div className="section-title" style={{ marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <ShieldCheck size={16} color="var(--accent-success)" />
          8-Step Command Execution History
        </div>
        {commands.length === 0 ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>No commands dispatched to this device yet.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Capability</th>
                <th>Status</th>
                <th>Verification Method</th>
                <th>Retries</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {commands.map((c) => (
                <tr key={c.id}>
                  <td className="tabular">{new Date(c.created_at).toLocaleTimeString()}</td>
                  <td className="mono">{c.capability_key}</td>
                  <td>
                    <span className={`badge ${c.status === 'verified' ? 'badge-online' : c.status === 'failed' ? 'badge-isolated' : 'badge-info'}`}>
                      {c.status}
                    </span>
                  </td>
                  <td className="mono">{c.verification_method || '—'}</td>
                  <td className="tabular">{c.retry_count}</td>
                  <td className="mono" style={{ maxWidth: '280px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {c.error_message || JSON.stringify(c.result)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};

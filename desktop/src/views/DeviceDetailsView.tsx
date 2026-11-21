import React, { useCallback, useEffect, useState } from 'react';
import {
  ArrowLeft,
  KeyRound,
  Lock,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
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
  const [appTarget, setAppTarget] = useState('');
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
      if (capabilityKey === 'processes.list' && Array.isArray(cmd.result.processes)) {
        setProcessSnapshot(cmd.result.processes as Array<{ pid: number; name: string; cmdline?: string }>);
      }
      onNotify(`Command ${capabilityKey} -> ${cmd.status} (${cmd.verification_method || 'queued'})`);
      await loadDetail();
      await onRefreshGlobal();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Command failed');
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
            opacity: 0.85,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <strong>{cap.display_name}</strong>
            <span className="badge badge-offline">{!cap.supported ? 'Unsupported' : 'Permission Locked'}</span>
          </div>
          <div style={{ fontSize: '12px', color: '#fbbf24', marginTop: '6px' }}>
            {!cap.supported
              ? cap.unavailable_reason || 'Not supported on this operating system or hardware.'
              : `Requires '${cap.requires_permission}' permission — grant below to enable.`}
          </div>
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
              {cap.requires_confirmation ? ' · requires confirmation' : ''}
            </div>
          </div>
          <span className="badge badge-online">Ready</span>
        </div>

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

        {cap.capability_key === 'volume.set' && (
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
                <button className="btn btn-sm btn-primary" onClick={() => sendCommand('volume.set', { level: volumeLevel })}>
                  Apply
                </button>
              </>
            )}
          </div>
        )}

        {cap.capability_key === 'media.control' && (
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            {['previous', 'play', 'pause', 'next'].map((act) => (
              <button key={act} className="btn btn-sm" onClick={() => sendCommand('media.control', { action: act })}>
                {act}
              </button>
            ))}
          </div>
        )}

        {cap.capability_key === 'power.set' && (
          <div style={{ display: 'flex', gap: '8px' }}>
            <button className="btn btn-success btn-sm" onClick={() => sendCommand('power.set', { on: true })}>
              Turn ON
            </button>
            <button className="btn btn-sm" onClick={() => sendCommand('power.set', { on: false })}>
              Turn OFF
            </button>
          </div>
        )}

        {(cap.capability_key === 'apps.launch' || cap.capability_key === 'shortcuts.run') && (
          <div style={{ display: 'flex', gap: '8px' }}>
            <input
              className="input"
              style={{ flex: 1 }}
              placeholder={
                cap.capability_key === 'shortcuts.run'
                  ? 'Shortcut name'
                  : device.adapter_type === 'google_tv'
                  ? 'https://www.youtube.com/tv'
                  : 'App ID / package (e.g. terminal_info)'
              }
              value={appTarget}
              onChange={(e) => setAppTarget(e.target.value)}
            />
            <button
              className="btn btn-sm btn-primary"
              onClick={() => {
                if (!appTarget.trim()) return;
                const paramKey =
                  cap.capability_key === 'shortcuts.run'
                    ? 'shortcut_name'
                    : device.adapter_type === 'google_tv'
                    ? 'app_link'
                    : device.adapter_type === 'android_companion'
                    ? 'package_name'
                    : device.adapter_type === 'macos_agent'
                    ? 'bundle_id'
                    : 'app_id';
                void sendCommand(cap.capability_key, { [paramKey]: appTarget.trim() });
              }}
            >
              Launch
            </button>
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

  // Collect all relevant permission scopes for this device's capabilities
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
          <div className="section-title" style={{ marginBottom: '12px' }}>Identity & Room Assignment</div>
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
          <div className="section-title" style={{ marginBottom: '12px' }}>Live Host & Integration Health</div>
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
                      {k}: <strong style={{ color: 'var(--text-primary)' }}>{String(v)}</strong>
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
          Advertised Capabilities & Honest Availability Matrix
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

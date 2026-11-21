import React, { useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Battery,
  CheckCircle2,
  Cpu,
  Music,
  Play,
  Pause,
  Radar,
  Search,
  ShieldAlert,
  Smartphone,
  Tv,
  Wifi,
  Zap,
} from 'lucide-react';
import { apiRequest } from '../api/client';
import type { DashboardSummary, Device } from '../types/models';

interface Props {
  summary: DashboardSummary | null;
  onRefresh: () => Promise<void>;
  onSelectDevice: (deviceId: string) => void;
  onNavigate: (view: string) => void;
  onNotify: (msg: string) => void;
}

export const CommandCentreView: React.FC<Props> = ({
  summary,
  onRefresh,
  onSelectDevice,
  onNavigate,
  onNotify,
}) => {
  const [search, setSearch] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [groupBy, setGroupBy] = useState<'none' | 'room' | 'category' | 'status'>('room');
  const [sortBy, setSortBy] = useState<'name' | 'status' | 'last_seen'>('last_seen');
  const [scanning, setScanning] = useState(false);

  const runScan = async () => {
    setScanning(true);
    try {
      const res = await apiRequest<{ discovered_in_scan: number }>('/discovery/scan', { method: 'POST' });
      onNotify(`Network scan completed (${res.discovered_in_scan} endpoints inspected).`);
      await onRefresh();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Discovery scan failed');
    } finally {
      setScanning(false);
    }
  };

  const approveCommand = async (cmdId: string) => {
    try {
      await apiRequest(`/commands/${cmdId}/approve`, { method: 'POST' });
      onNotify('Sensitive command approved and dispatched.');
      await onRefresh();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Approval failed');
    }
  };

  const rejectCommand = async (cmdId: string) => {
    try {
      await apiRequest(`/commands/${cmdId}/reject`, { method: 'POST' });
      onNotify('Sensitive command rejected.');
      await onRefresh();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Rejection failed');
    }
  };

  const controlMediaSession = async (deviceId: string, action: string) => {
    try {
      await apiRequest(`/devices/${deviceId}/commands`, {
        method: 'POST',
        body: JSON.stringify({ capability_key: 'media.control', parameters: { action } }),
      });
      onNotify(`Sent '${action}' to media session.`);
      await onRefresh();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Media command rejected');
    }
  };

  const filteredDevices = useMemo(() => {
    const list = summary?.devices || [];
    const q = search.trim().toLowerCase();
    const filtered = list.filter((d) => {
      if (categoryFilter !== 'all' && d.category !== categoryFilter) return false;
      if (statusFilter !== 'all' && d.connection_status !== statusFilter) return false;
      if (!q) return true;
      return (
        d.name.toLowerCase().includes(q) ||
        (d.ip_address || '').toLowerCase().includes(q) ||
        (d.manufacturer || '').toLowerCase().includes(q) ||
        (d.room_name || '').toLowerCase().includes(q) ||
        d.category.toLowerCase().includes(q)
      );
    });

    return [...filtered].sort((a, b) => {
      if (sortBy === 'name') return a.name.localeCompare(b.name);
      if (sortBy === 'status') return a.connection_status.localeCompare(b.connection_status);
      return (b.last_seen_at || '').localeCompare(a.last_seen_at || '');
    });
  }, [summary, search, categoryFilter, statusFilter, sortBy]);

  const groupedDevices = useMemo(() => {
    const groups: Record<string, Device[]> = {};
    for (const d of filteredDevices) {
      let key = 'All Devices';
      if (groupBy === 'room') key = d.room_name || 'Unassigned Room';
      else if (groupBy === 'category') key = d.category.toUpperCase();
      else if (groupBy === 'status') key = d.connection_status.toUpperCase();
      if (!groups[key]) groups[key] = [];
      groups[key].push(d);
    }
    return groups;
  }, [filteredDevices, groupBy]);

  if (!summary) {
    return <div className="empty-state">Loading live Command Centre telemetry...</div>;
  }

  const c = summary.counts;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '22px' }}>
      {/* Top KPI Telemetry */}
      <div className="grid-kpi">
        <div className="card">
          <div className="kpi-label">
            <span>Total Discovered</span>
            <Radar size={15} color="var(--accent-primary)" />
          </div>
          <div className="kpi-value">{c.total_discovered}</div>
          <div style={{ fontSize: '11.5px', color: 'var(--text-muted)', marginTop: '4px' }}>
            {c.unpaired} unpaired on LAN
          </div>
        </div>

        <div className="card">
          <div className="kpi-label">
            <span>Registered Devices</span>
            <Cpu size={15} color="var(--accent-purple)" />
          </div>
          <div className="kpi-value">{c.registered}</div>
          <div style={{ fontSize: '11.5px', color: 'var(--text-muted)', marginTop: '4px' }}>
            Enrolled & authenticated
          </div>
        </div>

        <div className="card">
          <div className="kpi-label">
            <span>Online / Offline</span>
            <Wifi size={15} color="var(--accent-success)" />
          </div>
          <div className="kpi-value">
            <span style={{ color: '#34d399' }}>{c.online}</span>
            <span style={{ color: 'var(--text-muted)', fontSize: '18px' }}> / {c.offline}</span>
          </div>
          <div style={{ fontSize: '11.5px', color: 'var(--text-muted)', marginTop: '4px' }}>
            {c.isolated > 0 ? `${c.isolated} isolated` : 'Zero isolated devices'}
          </div>
        </div>

        <div className="card">
          <div className="kpi-label">
            <span>Active Automations</span>
            <Zap size={15} color="var(--accent-warning)" />
          </div>
          <div className="kpi-value">{summary.automation_status.active_rules}</div>
          <div style={{ fontSize: '11.5px', color: 'var(--text-muted)', marginTop: '4px' }}>
            of {summary.automation_status.total_rules} configured rules
          </div>
        </div>

        <div className="card">
          <div className="kpi-label">
            <span>Pending Approvals</span>
            <ShieldAlert size={15} color={c.pending_confirmations > 0 ? 'var(--accent-danger)' : 'var(--text-muted)'} />
          </div>
          <div className="kpi-value">{c.pending_confirmations}</div>
          <div style={{ fontSize: '11.5px', color: 'var(--text-muted)', marginTop: '4px' }}>
            Sensitive consent gates
          </div>
        </div>
      </div>

      {/* Quick Actions Bar */}
      <div className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <button className="btn btn-primary" onClick={runScan} disabled={scanning}>
            <Radar size={15} />
            {scanning ? 'Scanning mDNS / SSDP / ARP...' : 'Scan Local Network'}
          </button>
          <button className="btn" onClick={() => onNavigate('devices')}>
            <Smartphone size={15} />
            Pair Companion / Device
          </button>
          <button className="btn" onClick={() => onNavigate('automations')}>
            <Zap size={15} />
            Create Automation
          </button>
          <button className="btn" onClick={() => onNavigate('integrations')}>
            <Tv size={15} />
            Connect Home Assistant / Matter
          </button>
        </div>

        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          {Object.entries(summary.categories).map(([cat, count]) => (
            <span key={cat} className="badge badge-info">
              {cat}: {count}
            </span>
          ))}
        </div>
      </div>

      {/* Pending Sensitive Approvals Alert */}
      {summary.pending_commands.length > 0 && (
        <div className="card" style={{ borderColor: 'rgba(245, 158, 11, 0.45)' }}>
          <div className="section-header">
            <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#fbbf24' }}>
              <AlertTriangle size={17} />
              Sensitive Operations Awaiting Explicit Owner Consent
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {summary.pending_commands.map((cmd) => (
              <div
                key={cmd.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '10px 14px',
                  background: 'var(--bg-canvas)',
                  borderRadius: '8px',
                }}
              >
                <div>
                  <div style={{ fontWeight: 600 }}>
                    {cmd.device_name || cmd.device_id} — <span className="mono">{cmd.capability_key}</span>
                  </div>
                  <div className="mono" style={{ color: 'var(--text-secondary)' }}>
                    Parameters: {JSON.stringify(cmd.parameters)}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '8px' }}>
                  <button className="btn btn-success btn-sm" onClick={() => approveCommand(cmd.id)}>
                    Approve
                  </button>
                  <button className="btn btn-danger btn-sm" onClick={() => rejectCommand(cmd.id)}>
                    Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Search, Filter, Sort & Grouping Matrix */}
      <div className="card">
        <div className="section-header">
          <div className="section-title">Environment Device Matrix</div>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
            <div style={{ position: 'relative' }}>
              <Search size={14} style={{ position: 'absolute', left: '10px', top: '10px', color: 'var(--text-muted)' }} />
              <input
                className="input"
                style={{ paddingLeft: '30px', width: '210px' }}
                placeholder="Search name, IP, room..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>

            <select className="select" aria-label="Filter category" value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
              <option value="all">All Categories</option>
              <option value="smartphone">Smartphone</option>
              <option value="desktop">Desktop</option>
              <option value="laptop">Laptop</option>
              <option value="tv">TV</option>
              <option value="speaker">Speaker</option>
              <option value="appliance">Appliance</option>
              <option value="smarthome">Smart Home</option>
              <option value="network">Network</option>
            </select>

            <select className="select" aria-label="Filter status" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="all">All Statuses</option>
              <option value="online">Online</option>
              <option value="offline">Offline</option>
              <option value="unpaired">Unpaired</option>
              <option value="isolated">Isolated</option>
            </select>

            <select className="select" aria-label="Group by" value={groupBy} onChange={(e) => setGroupBy(e.target.value as 'none' | 'room' | 'category' | 'status')}>
              <option value="room">Group: Room</option>
              <option value="category">Group: Category</option>
              <option value="status">Group: Status</option>
              <option value="none">No Grouping</option>
            </select>

            <select className="select" aria-label="Sort by" value={sortBy} onChange={(e) => setSortBy(e.target.value as 'name' | 'status' | 'last_seen')}>
              <option value="last_seen">Sort: Last Seen</option>
              <option value="name">Sort: Name</option>
              <option value="status">Sort: Status</option>
            </select>
          </div>
        </div>

        {filteredDevices.length === 0 ? (
          <div className="empty-state">
            No devices match your current filter. Run a Local Network Scan or pair a companion device to populate your inventory.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
            {Object.entries(groupedDevices).map(([groupTitle, devs]) => (
              <div key={groupTitle}>
                <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '10px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  {groupTitle} ({devs.length})
                </div>
                <div className="grid-devices">
                  {devs.map((d) => (
                    <div
                      key={d.id}
                      className="card card-interactive"
                      onClick={() => onSelectDevice(d.id)}
                      style={{ padding: '15px' }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '8px' }}>
                        <div>
                          <div style={{ fontWeight: 600, fontSize: '14.5px' }}>{d.name}</div>
                          <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                            {d.manufacturer || 'Unknown Vendor'} {d.model ? `· ${d.model}` : ''}
                          </div>
                        </div>
                        <span
                          className={`badge ${
                            d.connection_status === 'online'
                              ? 'badge-online'
                              : d.connection_status === 'isolated'
                              ? 'badge-isolated'
                              : d.connection_status === 'unpaired'
                              ? 'badge-unpaired'
                              : 'badge-offline'
                          }`}
                        >
                          {d.connection_status}
                        </span>
                      </div>

                      <div style={{ display: 'flex', gap: '12px', fontSize: '12px', color: 'var(--text-muted)', marginTop: '10px', flexWrap: 'wrap' }}>
                        {d.ip_address && <span className="mono">{d.ip_address}</span>}
                        <span>Lifecycle: {d.lifecycle_state}</span>
                        {d.battery_level !== null && (
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '3px' }}>
                            <Battery size={13} /> {d.battery_level}%
                          </span>
                        )}
                        {d.health_score !== null && <span>Health: {d.health_score}%</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Active Media, Activity, Notifications & Security */}
      <div className="grid-2">
        <div className="card">
          <div className="section-header">
            <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Music size={16} color="var(--accent-primary)" />
              Active Media Sessions
            </div>
          </div>
          {summary.active_media_sessions.length === 0 ? (
            <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>No active media playback reported by enrolled devices.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {summary.active_media_sessions.map((ms) => (
                <div
                  key={ms.id}
                  style={{
                    padding: '12px',
                    borderRadius: '8px',
                    background: 'var(--bg-canvas)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                  }}
                >
                  <div>
                    <div style={{ fontWeight: 600 }}>{ms.title}</div>
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                      {ms.artist || ms.app_name} · on {ms.device_name || ms.device_id} ({ms.playback_state})
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: '6px' }}>
                    <button className="btn btn-sm" onClick={() => controlMediaSession(ms.device_id, 'play')} title="Play">
                      <Play size={13} />
                    </button>
                    <button className="btn btn-sm" onClick={() => controlMediaSession(ms.device_id, 'pause')} title="Pause">
                      <Pause size={13} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card">
          <div className="section-header">
            <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Activity size={16} color="var(--accent-success)" />
              Recent Command Pipeline Activity
            </div>
          </div>
          {summary.recent_activity.length === 0 ? (
            <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>No commands executed yet.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {summary.recent_activity.slice(0, 6).map((cmd) => (
                <div
                  key={cmd.id}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '8px 10px',
                    borderRadius: '6px',
                    background: 'var(--bg-canvas)',
                    fontSize: '12.5px',
                  }}
                >
                  <div>
                    <span style={{ fontWeight: 600 }}>{cmd.device_name || cmd.device_id}</span>{' '}
                    <span className="mono" style={{ color: 'var(--text-secondary)' }}>
                      {cmd.capability_key}
                    </span>
                  </div>
                  <span className={`badge ${cmd.status === 'verified' ? 'badge-online' : cmd.status === 'failed' ? 'badge-isolated' : 'badge-info'}`}>
                    {cmd.status === 'verified' && <CheckCircle2 size={11} />}
                    {cmd.status}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="section-header">
            <div className="section-title">Authorized Notifications</div>
            <button className="btn btn-sm" onClick={() => onNavigate('communications')}>
              Open Inbox
            </button>
          </div>
          {summary.notifications.length === 0 ? (
            <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>No unread authorized device notifications.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {summary.notifications.slice(0, 5).map((n) => (
                <div key={n.id} style={{ padding: '9px 11px', background: 'var(--bg-canvas)', borderRadius: '8px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px' }}>
                    <strong>
                      {n.app_name} · {n.sender}
                    </strong>
                    <span className="badge badge-info">{n.access_level === 'notification_only' ? 'Notification Mirror' : 'Direct'}</span>
                  </div>
                  <div style={{ fontSize: '12.5px', color: 'var(--text-secondary)', marginTop: '3px' }}>{n.body_preview}</div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card">
          <div className="section-header">
            <div className="section-title">Recent Security & Audit Events</div>
            <button className="btn btn-sm" onClick={() => onNavigate('security')}>
              Verify Chain
            </button>
          </div>
          {summary.security_events.length === 0 ? (
            <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>No security events recorded.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {summary.security_events.slice(0, 5).map((ev) => (
                <div
                  key={ev.id}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    padding: '8px 10px',
                    background: 'var(--bg-canvas)',
                    borderRadius: '6px',
                    fontSize: '12px',
                  }}
                >
                  <div>
                    <span className="mono">{ev.event_type}</span>
                    <span style={{ color: 'var(--text-muted)', marginLeft: '8px' }}>({ev.outcome})</span>
                  </div>
                  <span className="mono" style={{ color: 'var(--text-muted)' }}>
                    #{ev.id} · {ev.entry_hash.slice(0, 10)}…
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

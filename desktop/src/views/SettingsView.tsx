import React, { useCallback, useEffect, useState } from 'react';
import { LogOut, Network, Server, Shield } from 'lucide-react';
import { apiRequest } from '../api/client';

interface SystemSettingsData {
  app_name: string;
  version: string;
  bind_host: string;
  bind_port: number;
  database_dialect: string;
  tls_cert_path: string;
  tls_sha256_fingerprint: string;
  session_ttl_minutes: number;
  pairing_ttl_seconds: number;
  replay_window_seconds: number;
  discovery_protocols: string[];
  interfaces: Array<{
    name: string;
    operstate: string;
    mac: string;
    addresses: string[];
  }>;
}

interface SessionRow {
  id: string;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  is_current: boolean;
}

interface Props {
  onLogout: () => Promise<void>;
  onNotify: (msg: string) => void;
}

export const SettingsView: React.FC<Props> = ({ onLogout, onNotify }) => {
  const [settings, setSettings] = useState<SystemSettingsData | null>(null);
  const [sessions, setSessions] = useState<SessionRow[]>([]);

  const loadSettings = useCallback(async () => {
    try {
      const [sysRes, sessRes] = await Promise.all([
        apiRequest<SystemSettingsData>('/system/settings'),
        apiRequest<{ sessions: SessionRow[] }>('/auth/sessions'),
      ]);
      setSettings(sysRes);
      setSessions(sessRes.sessions || []);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Failed to load system settings');
    }
  }, [onNotify]);

  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  const revokeSession = async (id: string) => {
    await apiRequest(`/auth/sessions/${id}`, { method: 'DELETE' });
    onNotify('Operator session revoked.');
    await loadSettings();
  };

  if (!settings) {
    return <div className="empty-state">Loading system diagnostics...</div>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div className="section-header">
        <div>
          <h2 style={{ fontSize: '19px', fontWeight: 700 }}>System Settings & Local Core Diagnostics</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
            Local-first modular monolith configuration, TLS identity, network interfaces, and operator session lifecycle.
          </p>
        </div>

        <button className="btn btn-danger" onClick={onLogout}>
          <LogOut size={15} /> Sign Out Current Session
        </button>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
            <Server size={16} color="var(--accent-primary)" />
            Core Engine Configuration
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', fontSize: '13px' }}>
            <div>Version: <strong>v{settings.version}</strong></div>
            <div>DB Engine: <strong className="mono">{settings.database_dialect} (WAL)</strong></div>
            <div>Bind Address: <span className="mono">{settings.bind_host}:{settings.bind_port}</span></div>
            <div>Replay Guard Window: <strong>±{settings.replay_window_seconds}s</strong></div>
            <div>Session TTL: <strong>{settings.session_ttl_minutes} min</strong></div>
            <div>Pairing Code TTL: <strong>{settings.pairing_ttl_seconds}s</strong></div>
          </div>
          <div style={{ marginTop: '14px', paddingTop: '12px', borderTop: '1px solid var(--border-subtle)' }}>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Shield size={14} /> Local Hub ECDSA P-256 TLS Fingerprint
            </div>
            <div className="mono" style={{ wordBreak: 'break-all', color: 'var(--accent-primary)' }}>
              {settings.tls_sha256_fingerprint}
            </div>
          </div>
        </div>

        <div className="card">
          <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
            <Network size={16} color="var(--accent-success)" />
            Host Network Interfaces
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {settings.interfaces.map((iface) => (
              <div key={iface.name} style={{ padding: '10px', background: 'var(--bg-canvas)', borderRadius: '8px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <strong className="mono">{iface.name}</strong>
                  <span className="badge badge-info">{iface.operstate}</span>
                </div>
                <div className="mono" style={{ color: 'var(--text-secondary)', marginTop: '4px' }}>
                  {iface.addresses.join(', ') || 'No IP assigned'}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Active Operator Sessions */}
      <div className="card">
        <div className="section-title" style={{ marginBottom: '10px' }}>Operator Authentication Sessions</div>
        <table className="data-table">
          <thead>
            <tr>
              <th>Session</th>
              <th>IP Address</th>
              <th>Created</th>
              <th>Expires</th>
              <th>Status</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((s) => (
              <tr key={s.id}>
                <td className="mono">
                  {s.id.slice(0, 12)}… {s.is_current && <span className="badge badge-online">Current</span>}
                </td>
                <td className="mono">{s.ip_address || 'local'}</td>
                <td className="tabular">{new Date(s.created_at).toLocaleString()}</td>
                <td className="tabular">{new Date(s.expires_at).toLocaleString()}</td>
                <td>
                  <span className={`badge ${s.revoked_at ? 'badge-isolated' : 'badge-online'}`}>
                    {s.revoked_at ? 'revoked' : 'active'}
                  </span>
                </td>
                <td>
                  {!s.revoked_at && !s.is_current && (
                    <button className="btn btn-sm btn-danger" onClick={() => revokeSession(s.id)}>
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

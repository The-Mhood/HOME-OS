import React, { useCallback, useEffect, useState } from 'react';
import { AlertOctagon, CheckCircle2, ShieldAlert, ShieldCheck } from 'lucide-react';
import { apiRequest } from '../api/client';
import type { AuditLog, DevicePermission } from '../types/models';

interface PermissionRow {
  device_id: string;
  device_name: string;
  category: string;
  adapter_type: string;
  lifecycle_state: string;
  key_version: number | null;
  permissions: DevicePermission[];
}

interface AuditVerification {
  valid: boolean;
  checked_entries: number;
  head_hash: string;
  broken_at_id: number | null;
  reason: string | null;
}

interface Props {
  onRefreshGlobal: () => Promise<void>;
  onNotify: (msg: string) => void;
}

export const SecurityView: React.FC<Props> = ({ onRefreshGlobal, onNotify }) => {
  const [matrix, setMatrix] = useState<PermissionRow[]>([]);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [verification, setVerification] = useState<AuditVerification | null>(null);
  const [severityFilter, setSeverityFilter] = useState('');

  const loadSecurityData = useCallback(async () => {
    try {
      const query = severityFilter ? `?severity=${encodeURIComponent(severityFilter)}` : '';
      const [permRes, logRes] = await Promise.all([
        apiRequest<{ matrix: PermissionRow[] }>('/security/permissions'),
        apiRequest<{ logs: AuditLog[] }>(`/security/audit-logs${query}`),
      ]);
      setMatrix(permRes.matrix || []);
      setLogs(logRes.logs || []);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Failed to load security data');
    }
  }, [severityFilter, onNotify]);

  useEffect(() => {
    void loadSecurityData();
  }, [loadSecurityData]);

  const verifyHashChain = async () => {
    const report = await apiRequest<AuditVerification>('/security/audit-verify');
    setVerification(report);
    onNotify(
      report.valid
        ? `Audit hash chain cryptographically verified (${report.checked_entries} entries intact).`
        : `TAMPERING DETECTED at entry #${report.broken_at_id}!`
    );
  };

  const revokePermission = async (deviceId: string, scope: string) => {
    await apiRequest(`/devices/${deviceId}/permissions`, {
      method: 'POST',
      body: JSON.stringify({ permission_scope: scope, status: 'revoked', reason: 'Security centre revocation' }),
    });
    onNotify(`Revoked '${scope}'.`);
    await loadSecurityData();
    await onRefreshGlobal();
  };

  const triggerLockdown = async () => {
    const res = await apiRequest<{ devices_isolated: number; automations_paused: number }>(
      '/security/emergency-lockdown',
      { method: 'POST' }
    );
    onNotify(
      `EMERGENCY LOCKDOWN ACTIVE: ${res.devices_isolated} devices isolated, ${res.automations_paused} automations paused.`
    );
    await loadSecurityData();
    await onRefreshGlobal();
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div className="section-header">
        <div>
          <h2 style={{ fontSize: '19px', fontWeight: 700 }}>Permissions, Vault & Cryptographic Security</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
            Inspect and revoke permissions instantaneously, verify the SHA-256 hash-chained audit trail, or isolate all devices.
          </p>
        </div>

        <div style={{ display: 'flex', gap: '10px' }}>
          <button className="btn btn-primary" onClick={verifyHashChain}>
            <ShieldCheck size={15} /> Verify SHA-256 Audit Chain
          </button>
          <button className="btn btn-danger" onClick={triggerLockdown}>
            <AlertOctagon size={15} /> Emergency Disconnect All
          </button>
        </div>
      </div>

      {verification && (
        <div
          className="card"
          style={{
            borderColor: verification.valid ? 'var(--accent-success)' : 'var(--accent-danger)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: '10px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            {verification.valid ? (
              <CheckCircle2 size={20} color="#34d399" />
            ) : (
              <ShieldAlert size={20} color="#ef4444" />
            )}
            <div>
              <div style={{ fontWeight: 700 }}>
                {verification.valid
                  ? `Cryptographic Chain Intact (${verification.checked_entries} Linked Entries Verified)`
                  : `Audit Chain Tampering Detected at Entry #${verification.broken_at_id}`}
              </div>
              <div className="mono" style={{ color: 'var(--text-secondary)' }}>
                Head SHA-256: {verification.head_hash}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Global Permission Matrix */}
      <div className="card">
        <div className="section-title" style={{ marginBottom: '12px' }}>Enrolled Device Permission Matrix</div>
        {matrix.length === 0 ? (
          <div className="empty-state">No enrolled devices with permissions.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Device</th>
                <th>Lifecycle</th>
                <th>Key Version</th>
                <th>Active Granted Permissions (Click to Revoke)</th>
              </tr>
            </thead>
            <tbody>
              {matrix.map((row) => {
                const activePerms = row.permissions.filter((p) => p.status === 'granted');
                return (
                  <tr key={row.device_id}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{row.device_name}</div>
                      <div className="mono" style={{ color: 'var(--text-muted)' }}>{row.adapter_type}</div>
                    </td>
                    <td>
                      <span className={`badge ${row.lifecycle_state === 'isolated' ? 'badge-isolated' : 'badge-online'}`}>
                        {row.lifecycle_state}
                      </span>
                    </td>
                    <td className="mono">v{row.key_version || 1}</td>
                    <td>
                      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                        {activePerms.length === 0 ? (
                          <span style={{ color: 'var(--text-muted)', fontSize: '12px' }}>All permissions revoked</span>
                        ) : (
                          activePerms.map((p) => (
                            <button
                              key={p.id}
                              className="btn btn-sm"
                              onClick={() => revokePermission(row.device_id, p.permission_scope)}
                              title="Click to revoke immediately"
                            >
                              <span className="mono">{p.permission_scope}</span> ×
                            </button>
                          ))
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Hash-Chained Audit Log */}
      <div className="card">
        <div className="section-header">
          <div className="section-title">Tamper-Evident Audit Log</div>
          <select className="select" value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)}>
            <option value="">All Severities</option>
            <option value="info">Info</option>
            <option value="warning">Warning</option>
            <option value="critical">Critical</option>
          </select>
        </div>

        <table className="data-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Timestamp</th>
              <th>Severity</th>
              <th>Event</th>
              <th>Actor</th>
              <th>Outcome</th>
              <th>Entry SHA-256</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((a) => (
              <tr key={a.id}>
                <td className="mono">{a.id}</td>
                <td className="tabular">{new Date(a.created_at).toLocaleTimeString()}</td>
                <td>
                  <span
                    className={`badge ${
                      a.severity === 'critical'
                        ? 'badge-isolated'
                        : a.severity === 'warning'
                        ? 'badge-unpaired'
                        : 'badge-info'
                    }`}
                  >
                    {a.severity}
                  </span>
                </td>
                <td className="mono">{a.event_type}</td>
                <td className="mono">
                  {a.actor_type}:{a.actor_id ? a.actor_id.slice(0, 8) : 'anon'}
                </td>
                <td>{a.outcome}</td>
                <td className="mono" style={{ color: 'var(--text-muted)' }}>
                  {a.entry_hash.slice(0, 16)}…
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

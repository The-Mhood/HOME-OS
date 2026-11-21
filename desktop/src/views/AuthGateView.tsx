import React, { useState } from 'react';
import { Shield, Lock } from 'lucide-react';
import { apiRequest, setStoredToken } from '../api/client';
import type { User } from '../types/models';

interface Props {
  setupRequired: boolean;
  tlsFingerprint: string;
  onAuthenticated: (user: User) => void;
}

export const AuthGateView: React.FC<Props> = ({
  setupRequired,
  tlsFingerprint,
  onAuthenticated,
}) => {
  const [username, setUsername] = useState('admin');
  const [displayName, setDisplayName] = useState('Home Owner');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const endpoint = setupRequired ? '/auth/setup' : '/auth/login';
      const body = setupRequired
        ? { username, display_name: displayName, password }
        : { username, password };
      const res = await apiRequest<{ access_token: string; user: User }>(endpoint, {
        method: 'POST',
        body: JSON.stringify(body),
      });
      setStoredToken(res.access_token);
      onAuthenticated(res.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Authentication failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '24px',
        background: 'radial-gradient(circle at 50% 15%, #111c2d 0%, #080a0f 65%)',
      }}
    >
      <div className="card" style={{ width: '100%', maxWidth: '440px', padding: '28px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '18px' }}>
          <div className="brand-mark">
            <Shield size={18} />
          </div>
          <div>
            <div style={{ fontWeight: 700, fontSize: '17px' }}>HOME OS</div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
              {setupRequired ? 'First-Run Security Vault Initialization' : 'Authorized Command Centre Sign-In'}
            </div>
          </div>
        </div>

        <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '18px' }}>
          {setupRequired
            ? 'Create the local owner account. Credentials are hashed with Argon2id and device keys are encrypted at rest with AES-256-GCM.'
            : 'Authenticate to inspect discovered devices, manage permissions, and control enrolled hardware.'}
        </p>

        {error && (
          <div
            style={{
              background: 'var(--accent-danger-dim)',
              color: '#fca5a5',
              padding: '10px 12px',
              borderRadius: '8px',
              fontSize: '12.5px',
              marginBottom: '14px',
            }}
          >
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <div>
            <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
              Username
            </label>
            <input
              className="input"
              style={{ width: '100%' }}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </div>

          {setupRequired && (
            <div>
              <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
                Display Name
              </label>
              <input
                className="input"
                style={{ width: '100%' }}
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                required
              />
            </div>
          )}

          <div>
            <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
              Passphrase {setupRequired && '(min 8 characters)'}
            </label>
            <input
              type="password"
              className="input"
              style={{ width: '100%' }}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter owner passphrase"
              required
              minLength={8}
            />
          </div>

          <button type="submit" className="btn btn-primary" style={{ marginTop: '6px', padding: '10px' }} disabled={busy}>
            <Lock size={15} />
            {busy ? 'Verifying...' : setupRequired ? 'Initialize Vault & Sign In' : 'Unlock Command Centre'}
          </button>
        </form>

        {tlsFingerprint && (
          <div style={{ marginTop: '18px', paddingTop: '14px', borderTop: '1px solid var(--border-subtle)' }}>
            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>
              Local Hub TLS SHA-256 Fingerprint
            </div>
            <div className="mono" style={{ color: 'var(--text-secondary)', wordBreak: 'break-all', fontSize: '11px' }}>
              {tlsFingerprint}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

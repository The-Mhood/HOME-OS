import React, { useCallback, useEffect, useState } from 'react';
import { Home, Layers, RefreshCw, Trash2 } from 'lucide-react';
import { apiRequest } from '../api/client';
import type { IntegrationConnection } from '../types/models';

interface Props {
  onRefreshGlobal: () => Promise<void>;
  onNotify: (msg: string) => void;
}

export const IntegrationsView: React.FC<Props> = ({ onRefreshGlobal, onNotify }) => {
  const [integrations, setIntegrations] = useState<IntegrationConnection[]>([]);
  const [haUrl, setHaUrl] = useState('http://homeassistant.local:8123');
  const [haToken, setHaToken] = useState('');
  const [matterWs, setMatterWs] = useState('ws://127.0.0.1:5580/ws');

  const loadIntegrations = useCallback(async () => {
    try {
      const res = await apiRequest<{ integrations: IntegrationConnection[] }>('/integrations');
      setIntegrations(res.integrations || []);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Failed to load integrations');
    }
  }, [onNotify]);

  useEffect(() => {
    void loadIntegrations();
  }, [loadIntegrations]);

  const connectHomeAssistant = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const res = await apiRequest<{ integration: IntegrationConnection; synced_devices: unknown[] }>(
        '/integrations/home-assistant',
        {
          method: 'POST',
          body: JSON.stringify({
            name: 'Home Assistant Backbone',
            endpoint_url: haUrl,
            access_token: haToken,
          }),
        }
      );
      setHaToken('');
      onNotify(
        `Home Assistant saved with AES-256-GCM encrypted token (${res.integration.status}, ${res.synced_devices.length} entities synced).`
      );
      await loadIntegrations();
      await onRefreshGlobal();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Failed to configure Home Assistant');
    }
  };

  const connectMatter = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await apiRequest('/integrations/matter', {
        method: 'POST',
        body: JSON.stringify({ name: 'Matter Fabric Controller', ws_url: matterWs }),
      });
      onNotify('Matter Server WebSocket bridge registered.');
      await loadIntegrations();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Failed to configure Matter');
    }
  };

  const syncIntegration = async (id: string) => {
    try {
      const res = await apiRequest<{ synced_count: number }>(`/integrations/${id}/sync`, {
        method: 'POST',
      });
      onNotify(`Synced ${res.synced_count} smart-home entities.`);
      await loadIntegrations();
      await onRefreshGlobal();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Sync failed');
    }
  };

  const deleteIntegration = async (id: string) => {
    await apiRequest(`/integrations/${id}`, { method: 'DELETE' });
    await loadIntegrations();
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div>
        <h2 style={{ fontSize: '19px', fontWeight: 700 }}>Smart-Home Backbone & Companion Integrations</h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
          Reusable Common Device Adapter Contract v1.0 across Home Assistant, Matter, Google TV, Mobile, and Desktop OS agents.
        </p>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
            <Home size={16} color="var(--accent-primary)" />
            Home Assistant Backbone
          </div>
          <p style={{ fontSize: '12.5px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
            Discovers lights, speakers, climate, switches, sensors, scenes, and locks with real post-command entity state verification.
          </p>
          <form onSubmit={connectHomeAssistant} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            <input
              className="input"
              value={haUrl}
              onChange={(e) => setHaUrl(e.target.value)}
              placeholder="http://homeassistant.local:8123"
              required
            />
            <input
              type="password"
              className="input"
              value={haToken}
              onChange={(e) => setHaToken(e.target.value)}
              placeholder="Long-Lived Access Token (encrypted via AES-256-GCM)"
              required
              minLength={8}
            />
            <button type="submit" className="btn btn-primary">
              Save & Sync Home Assistant
            </button>
          </form>
        </div>

        <div className="card">
          <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
            <Layers size={16} color="var(--accent-purple)" />
            Matter Fabric Bridge
          </div>
          <p style={{ fontSize: '12.5px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
            Supports mDNS <span className="mono">_matter._tcp.local.</span> &amp; <span className="mono">_matterc._udp.local.</span> discovery plus <span className="mono">python-matter-server</span> JSON-RPC cluster control.
          </p>
          <form onSubmit={connectMatter} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            <input
              className="input mono"
              value={matterWs}
              onChange={(e) => setMatterWs(e.target.value)}
              placeholder="ws://127.0.0.1:5580/ws"
              required
            />
            <button type="submit" className="btn btn-primary">
              Connect Matter Server Bridge
            </button>
          </form>
        </div>
      </div>

      {/* Active Configured Bridges */}
      <div className="card">
        <div className="section-title" style={{ marginBottom: '10px' }}>Configured Integration Bridges ({integrations.length})</div>
        {integrations.length === 0 ? (
          <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>No external smart-home bridges configured yet.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Integration</th>
                <th>Endpoint</th>
                <th>Status</th>
                <th>Health / Diagnostics</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {integrations.map((ic) => (
                <tr key={ic.id}>
                  <td style={{ fontWeight: 600 }}>{ic.name}</td>
                  <td className="mono">{ic.endpoint_url}</td>
                  <td>
                    <span className={`badge ${ic.status === 'connected' ? 'badge-online' : 'badge-unpaired'}`}>
                      {ic.status}
                    </span>
                  </td>
                  <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{ic.health_message || '—'}</td>
                  <td style={{ display: 'flex', gap: '6px' }}>
                    <button className="btn btn-sm" onClick={() => syncIntegration(ic.id)}>
                      <RefreshCw size={13} /> Sync
                    </button>
                    <button className="btn btn-sm btn-danger" onClick={() => deleteIntegration(ic.id)}>
                      <Trash2 size={13} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Native Companion & Agent Platform Matrix */}
      <div className="card">
        <div className="section-title" style={{ marginBottom: '12px' }}>Native Companion Apps & Platform Agents</div>
        <table className="data-table">
          <thead>
            <tr>
              <th>Platform</th>
              <th>Native Codebase</th>
              <th>Supported Capabilities</th>
              <th>Enforced OS Privacy Boundaries</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>Linux</strong></td>
              <td className="mono">agents/linux (Python 3.11)</td>
              <td>/proc Telemetry, Root-jailed Files, Transfers, Processes, PipeWire/ALSA, MPRIS2</td>
              <td>Dynamic X11 / Wayland / Headless detection; never fabricates unavailable display capture</td>
            </tr>
            <tr>
              <td><strong>Windows 10/11</strong></td>
              <td className="mono">agents/windows (C# / .NET 8)</td>
              <td>DPAPI Vault, WMI, Files, Screen Capture, SendInput, CoreAudio, GSMTC</td>
              <td>UIPI / Winlogon Secure Desktop blocks non-elevated input into UAC dialogs</td>
            </tr>
            <tr>
              <td><strong>macOS 13+</strong></td>
              <td className="mono">agents/macos (Swift 5.9)</td>
              <td>Keychain, ScreenCaptureKit, Quartz Events, NSWorkspace, CoreAudio</td>
              <td>TCC Screen Recording &amp; Accessibility permissions checked via CGPreflight / AXIsProcessTrusted</td>
            </tr>
            <tr>
              <td><strong>Android 10–15</strong></td>
              <td className="mono">companions/android (Kotlin)</td>
              <td>NotificationListener + WhatsApp RemoteInput Reply, MediaProjection, Torch, Volume, SAF</td>
              <td>Per-session MediaProjection consent; Play Store vs Direct telephony flavor separation</td>
            </tr>
            <tr>
              <td><strong>iOS 17+</strong></td>
              <td className="mono">companions/ios (SwiftUI)</td>
              <td>CryptoKit Vault, Telemetry, Shortcuts/AppIntents, Security-Scoped Files, HomeKit/Matter</td>
              <td>SMS, Call History, WhatsApp DB, global notifications &amp; remote touch explicitly unavailable</td>
            </tr>
            <tr>
              <td><strong>Google TV</strong></td>
              <td className="mono">adapters/google_tv.py (TLS v2)</td>
              <td>Port 6467 PIN Pairing, Port 6466 D-Pad, Home/Back, Volume, App Links, Wake-on-LAN</td>
              <td>Requires physical 6-hex-digit PIN confirmation on television screen</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
};

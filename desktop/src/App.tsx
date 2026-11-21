import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertOctagon,
  Cpu,
  FolderOpen,
  LayoutDashboard,
  MessageSquare,
  Plug,
  Settings,
  Shield,
  Sliders,
  Zap,
} from 'lucide-react';
import {
  apiRequest,
  connectOperatorWebSocket,
  getStoredToken,
  setStoredToken,
} from './api/client';
import type { DashboardSummary, User } from './types/models';
import { AuthGateView } from './views/AuthGateView';
import { AutomationsView } from './views/AutomationsView';
import { CommandCentreView } from './views/CommandCentreView';
import { CommunicationsView } from './views/CommunicationsView';
import { DeviceDetailsView } from './views/DeviceDetailsView';
import { DevicesView } from './views/DevicesView';
import { IntegrationsView } from './views/IntegrationsView';
import { MediaFilesView } from './views/MediaFilesView';
import { SecurityView } from './views/SecurityView';
import { SettingsView } from './views/SettingsView';

export const App: React.FC = () => {
  const [booting, setBooting] = useState(true);
  const [setupRequired, setSetupRequired] = useState(false);
  const [tlsFingerprint, setTlsFingerprint] = useState('');
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);

  const [activeView, setActiveView] = useState<string>('command_centre');
  const [selectedDeviceId, setSelectedDeviceId] = useState<string | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const showToast = useCallback((msg: string) => {
    setToast(msg);
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4200);
    return () => clearTimeout(t);
  }, [toast]);

  const refreshSummary = useCallback(async () => {
    if (!getStoredToken()) return;
    try {
      const data = await apiRequest<DashboardSummary>('/dashboard/summary');
      setSummary(data);
    } catch {
      // session may have expired
    }
  }, []);

  useEffect(() => {
    const init = async () => {
      try {
        const st = await apiRequest<{ setup_required: boolean; tls_fingerprint: string }>('/auth/status');
        setSetupRequired(st.setup_required);
        setTlsFingerprint(st.tls_fingerprint);

        if (!st.setup_required && getStoredToken()) {
          try {
            const me = await apiRequest<{ user: User }>('/auth/me');
            setCurrentUser(me.user);
          } catch {
            setStoredToken(null);
          }
        }
      } finally {
        setBooting(false);
      }
    };
    void init();
  }, []);

  useEffect(() => {
    if (!currentUser) return;
    void refreshSummary();

    const disconnectWs = connectOperatorWebSocket(
      (ev) => {
        if (ev.event_type === 'automation.notification') {
          showToast(`${String(ev.payload.title)}: ${String(ev.payload.message)}`);
        }
        if (ev.event_type !== 'ws.heartbeat' && ev.event_type !== 'ws.connected') {
          void refreshSummary();
        }
      },
      setWsConnected
    );

    return () => disconnectWs();
  }, [currentUser, refreshSummary, showToast]);

  const handleLogout = async () => {
    try {
      await apiRequest('/auth/logout', { method: 'POST' });
    } catch {
      // ignore
    }
    setStoredToken(null);
    setCurrentUser(null);
    setSummary(null);
  };

  const handleEmergencyLockdown = async () => {
    try {
      const res = await apiRequest<{ devices_isolated: number }>('/security/emergency-lockdown', {
        method: 'POST',
      });
      showToast(`Emergency Lockdown executed: ${res.devices_isolated} devices isolated.`);
      await refreshSummary();
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Emergency lockdown failed');
    }
  };

  if (booting) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div className="mono" style={{ color: 'var(--text-secondary)' }}>
          Connecting to HOME OS Local Core...
        </div>
      </div>
    );
  }

  if (!currentUser) {
    return (
      <AuthGateView
        setupRequired={setupRequired}
        tlsFingerprint={tlsFingerprint}
        onAuthenticated={(user) => {
          setSetupRequired(false);
          setCurrentUser(user);
        }}
      />
    );
  }

  const openDeviceDetails = (deviceId: string) => {
    setSelectedDeviceId(deviceId);
    setActiveView('device_details');
  };

  const navItems = [
    { id: 'command_centre', label: 'Command Centre', icon: LayoutDashboard },
    { id: 'devices', label: 'Devices', icon: Cpu, badge: summary?.counts.total_discovered },
    {
      id: 'device_details',
      label: 'Device Details',
      icon: Sliders,
      disabled: !selectedDeviceId && (summary?.devices.length || 0) === 0,
    },
    { id: 'communications', label: 'Communications', icon: MessageSquare, badge: summary?.notifications.length },
    { id: 'media', label: 'Media & Files', icon: FolderOpen },
    { id: 'automations', label: 'Automations', icon: Zap, badge: summary?.automation_status.active_rules },
    { id: 'integrations', label: 'Integrations', icon: Plug },
    {
      id: 'security',
      label: 'Permissions & Security',
      icon: Shield,
      badge: summary?.counts.pending_confirmations || undefined,
    },
    { id: 'settings', label: 'Settings', icon: Settings },
  ];

  const activeDeviceId = selectedDeviceId || summary?.devices[0]?.id || null;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-box">
          <div className="brand-mark">
            <Shield size={18} />
          </div>
          <div>
            <div className="brand-title">HOME OS</div>
            <div className="brand-sub">Command Environment v1.0</div>
          </div>
        </div>

        <nav className="nav-list" aria-label="Main Navigation">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                className={`nav-item ${activeView === item.id ? 'active' : ''}`}
                disabled={item.disabled}
                onClick={() => {
                  if (item.id === 'device_details' && !selectedDeviceId && summary?.devices[0]) {
                    setSelectedDeviceId(summary.devices[0].id);
                  }
                  setActiveView(item.id);
                }}
              >
                <span className="nav-item-left">
                  <Icon size={16} />
                  <span>{item.label}</span>
                </span>
                {typeof item.badge === 'number' && item.badge > 0 && (
                  <span className="badge badge-info tabular">{item.badge}</span>
                )}
              </button>
            );
          })}
        </nav>

        <div style={{ padding: '10px', background: 'var(--bg-surface)', borderRadius: '10px', fontSize: '12px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ color: 'var(--text-secondary)' }}>Event Bus</span>
            <span className={`badge ${wsConnected ? 'badge-online' : 'badge-offline'}`}>
              {wsConnected ? 'Live WS' : 'Polling'}
            </span>
          </div>
          <div style={{ marginTop: '6px', color: 'var(--text-muted)', fontSize: '11.5px' }}>
            Signed in as <strong>{currentUser.display_name}</strong>
          </div>
        </div>
      </aside>

      <div className="main-area">
        <header className="topbar">
          <div style={{ fontWeight: 600, fontSize: '15px' }}>
            {navItems.find((n) => n.id === activeView)?.label || 'HOME OS'}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <button className="btn btn-sm btn-danger" onClick={handleEmergencyLockdown}>
              <AlertOctagon size={14} /> Emergency Disconnect
            </button>
          </div>
        </header>

        <main className="content-scroll">
          {activeView === 'command_centre' && (
            <CommandCentreView
              summary={summary}
              onRefresh={refreshSummary}
              onSelectDevice={openDeviceDetails}
              onNavigate={setActiveView}
              onNotify={showToast}
            />
          )}

          {activeView === 'devices' && (
            <DevicesView
              devices={summary?.devices || []}
              rooms={summary?.rooms || []}
              onRefresh={refreshSummary}
              onSelectDevice={openDeviceDetails}
              onNotify={showToast}
            />
          )}

          {activeView === 'device_details' && activeDeviceId && (
            <DeviceDetailsView
              deviceId={activeDeviceId}
              rooms={summary?.rooms || []}
              onBack={() => setActiveView('devices')}
              onRefreshGlobal={refreshSummary}
              onNotify={showToast}
            />
          )}

          {activeView === 'communications' && <CommunicationsView onNotify={showToast} />}

          {activeView === 'media' && (
            <MediaFilesView devices={summary?.devices || []} onNotify={showToast} />
          )}

          {activeView === 'automations' && (
            <AutomationsView devices={summary?.devices || []} onNotify={showToast} />
          )}

          {activeView === 'integrations' && (
            <IntegrationsView onRefreshGlobal={refreshSummary} onNotify={showToast} />
          )}

          {activeView === 'security' && (
            <SecurityView onRefreshGlobal={refreshSummary} onNotify={showToast} />
          )}

          {activeView === 'settings' && (
            <SettingsView onLogout={handleLogout} onNotify={showToast} />
          )}
        </main>
      </div>

      {toast && <div className="toast-banner">{toast}</div>}
    </div>
  );
};

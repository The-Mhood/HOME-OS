import React, { useCallback, useEffect, useState } from 'react';
import { Check, Info, MessageSquare, RefreshCw, Send, X } from 'lucide-react';
import { apiRequest } from '../api/client';
import type { CommunicationItem, Device } from '../types/models';

interface Props {
  onNotify: (msg: string) => void;
}

export const CommunicationsView: React.FC<Props> = ({ onNotify }) => {
  const [items, setItems] = useState<CommunicationItem[]>([]);
  const [smsDevices, setSmsDevices] = useState<Device[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState('');
  const [composeTo, setComposeTo] = useState('');
  const [composeBody, setComposeBody] = useState('');
  const [syncing, setSyncing] = useState(false);
  const [sourceFilter, setSourceFilter] = useState('all');
  const [replyTexts, setReplyTexts] = useState<Record<string, string>>({});

  const loadComms = useCallback(async () => {
    try {
      const query = sourceFilter !== 'all' ? `?source_type=${encodeURIComponent(sourceFilter)}` : '';
      const res = await apiRequest<{ items: CommunicationItem[] }>(`/communications${query}`);
      setItems(res.items || []);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Failed to load communications');
    }
  }, [sourceFilter, onNotify]);

  useEffect(() => {
    void loadComms();
    apiRequest<{ devices: Device[] }>('/devices')
      .then((res) => {
        const capable = (res.devices || []).filter(
          (d) =>
            d.is_registered &&
            (d.adapter_type === 'android_adb' || d.adapter_type === 'ios_native' || d.adapter_type === 'local_host')
        );
        setSmsDevices(capable);
        if (capable.length > 0) {
          setSelectedDeviceId((prev) => prev || capable[0].id);
        }
      })
      .catch(() => {});
  }, [loadComms]);

  const syncFromDevices = async () => {
    setSyncing(true);
    try {
      const res = await apiRequest<{ synced_messages: number; synced_notifications: number; warnings?: string[] }>(
        '/communications/sync',
        { method: 'POST', body: JSON.stringify({}) }
      );
      const warn = res.warnings && res.warnings.length > 0 ? ` (${res.warnings[0]})` : '';
      onNotify(`Synced ${res.synced_messages} SMS/iMessages & ${res.synced_notifications} notifications!${warn}`);
      await loadComms();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Sync failed');
    } finally {
      setSyncing(false);
    }
  };

  const sendDirectSms = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedDeviceId || !composeTo.trim() || !composeBody.trim()) return;
    try {
      await apiRequest('/communications/send-sms', {
        method: 'POST',
        body: JSON.stringify({
          device_id: selectedDeviceId,
          to: composeTo.trim(),
          message: composeBody.trim(),
        }),
      });
      setComposeBody('');
      onNotify(`Dispatched SMS / text to ${composeTo.trim()}!`);
      await syncFromDevices();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'SMS send failed');
    }
  };

  const markRead = async (id: string) => {
    await apiRequest(`/communications/${id}/read`, { method: 'POST' });
    await loadComms();
  };

  const dismiss = async (id: string) => {
    await apiRequest(`/communications/${id}/dismiss`, { method: 'POST' });
    await loadComms();
  };

  const sendReply = async (id: string) => {
    const text = (replyTexts[id] || '').trim();
    if (!text) return;
    try {
      await apiRequest(`/communications/${id}/reply`, {
        method: 'POST',
        body: JSON.stringify({ message: text }),
      });
      setReplyTexts((prev) => ({ ...prev, [id]: '' }));
      onNotify('Reply dispatched through connected device!');
      await loadComms();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Reply failed');
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div className="section-header">
        <div>
          <h2 style={{ fontSize: '19px', fontWeight: 700 }}>Unified Communication &amp; SMS Centre</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
            View &amp; reply to real SMS, iMessages, and app notifications from connected Android &amp; iPhone devices in the background.
          </p>
        </div>

        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          <button className="btn btn-sm btn-success" onClick={() => void syncFromDevices()} disabled={syncing}>
            <RefreshCw size={13} /> {syncing ? 'Syncing Texts...' : 'Sync Texts & Notifications'}
          </button>
          {[
            { id: 'all', label: 'All' },
            { id: 'sms', label: 'SMS / Texts' },
            { id: 'android_notification', label: 'Android Notifications' },
            { id: 'whatsapp_notification', label: 'WhatsApp (Notification)' },
            { id: 'messaging_notification', label: 'Messaging' },
            { id: 'call_event', label: 'Call Alerts' },
          ].map((tab) => (
            <button
              key={tab.id}
              className={`btn btn-sm ${sourceFilter === tab.id ? 'btn-primary' : ''}`}
              onClick={() => setSourceFilter(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Compose Direct SMS / Text */}
      {smsDevices.length > 0 && (
        <form onSubmit={sendDirectSms} className="card" style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
          <select className="select" value={selectedDeviceId} onChange={(e) => setSelectedDeviceId(e.target.value)}>
            {smsDevices.map((d) => (
              <option key={d.id} value={d.id}>
                Send via {d.name}
              </option>
            ))}
          </select>
          <input
            className="input"
            style={{ width: '190px' }}
            placeholder="Phone number (+234...)"
            value={composeTo}
            onChange={(e) => setComposeTo(e.target.value)}
            required
          />
          <input
            className="input"
            style={{ flex: 1, minWidth: '220px' }}
            placeholder="Write SMS / iMessage text..."
            value={composeBody}
            onChange={(e) => setComposeBody(e.target.value)}
            required
          />
          <button type="submit" className="btn btn-primary">
            <Send size={14} /> Send SMS
          </button>
        </form>
      )}

      {/* Platform Privacy & Access Transparency Banner */}
      <div className="card" style={{ borderColor: 'rgba(56, 189, 248, 0.28)', display: 'flex', gap: '12px', alignItems: 'flex-start' }}>
        <Info size={18} color="var(--accent-primary)" style={{ marginTop: '2px', flexShrink: 0 }} />
        <div style={{ fontSize: '12.5px', color: 'var(--text-secondary)' }}>
          <strong style={{ color: 'var(--text-primary)' }}>Access Level Transparency:</strong> Direct SMS threads sync over Android background ADB (<span className="mono">content://sms</span>) and macOS Continuity (<span className="mono">Messages.app</span>), while WhatsApp, Signal, and Telegram alerts use notification-level mirroring without scraping encrypted app databases.
        </div>
      </div>

      {items.length === 0 ? (
        <div className="empty-state">
          <MessageSquare size={26} style={{ marginBottom: '8px', opacity: 0.6 }} />
          <div>No active communications yet. Click &quot;Sync Texts &amp; Notifications&quot; above after connecting your phone.</div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {items.map((item) => (
            <div
              key={item.id}
              className="card"
              style={{
                borderLeft: item.is_read ? undefined : '3px solid var(--accent-primary)',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px' }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 700 }}>{item.sender}</span>
                    {item.title && item.title !== item.sender && (
                      <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{item.title}</span>
                    )}
                    <span className="badge badge-info">{item.app_name}</span>
                    <span className={`badge ${item.access_level === 'notification_only' ? 'badge-unpaired' : 'badge-online'}`}>
                      {item.access_level === 'notification_only'
                        ? 'Notification Mirror Only'
                        : 'Direct OS Permission'}
                    </span>
                    <span style={{ fontSize: '11.5px', color: 'var(--text-muted)' }}>
                      via {item.device_name || item.device_id} · {new Date(item.received_at).toLocaleTimeString()}
                    </span>
                  </div>
                  <div style={{ marginTop: '8px', fontSize: '13.5px', whiteSpace: 'pre-wrap' }}>{item.body_preview}</div>
                </div>

                <div style={{ display: 'flex', gap: '6px' }}>
                  {!item.is_read && (
                    <button className="btn btn-sm" onClick={() => markRead(item.id)} title="Mark read">
                      <Check size={14} />
                    </button>
                  )}
                  <button className="btn btn-sm" onClick={() => dismiss(item.id)} title="Dismiss">
                    <X size={14} />
                  </button>
                </div>
              </div>

              {item.can_reply && (
                <div style={{ display: 'flex', gap: '8px', marginTop: '12px', paddingTop: '10px', borderTop: '1px solid var(--border-subtle)' }}>
                  <input
                    className="input"
                    style={{ flex: 1 }}
                    placeholder={`Send reply to ${item.sender}...`}
                    value={replyTexts[item.id] || ''}
                    onChange={(e) => setReplyTexts((prev) => ({ ...prev, [item.id]: e.target.value }))}
                  />
                  <button className="btn btn-primary btn-sm" onClick={() => sendReply(item.id)}>
                    <Send size={13} /> Reply
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

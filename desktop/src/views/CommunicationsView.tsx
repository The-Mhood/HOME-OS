import React, { useCallback, useEffect, useState } from 'react';
import { Check, Info, MessageSquare, Send, X } from 'lucide-react';
import { apiRequest } from '../api/client';
import type { CommunicationItem } from '../types/models';

interface Props {
  onNotify: (msg: string) => void;
}

export const CommunicationsView: React.FC<Props> = ({ onNotify }) => {
  const [items, setItems] = useState<CommunicationItem[]>([]);
  const [sourceFilter, setSourceFilter] = useState('all');
  const [replyTexts, setReplyTexts] = useState<Record<string, string>>({});

  const loadComms = useCallback(async () => {
    const query = sourceFilter !== 'all' ? `?source_type=${encodeURIComponent(sourceFilter)}` : '';
    const res = await apiRequest<{ items: CommunicationItem[] }>(`/communications${query}`);
    setItems(res.items);
  }, [sourceFilter]);

  useEffect(() => {
    void loadComms();
  }, [loadComms]);

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
      onNotify('Inline reply dispatched via Android Notification RemoteInput.');
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Inline reply failed');
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div className="section-header">
        <div>
          <h2 style={{ fontSize: '19px', fontWeight: 700 }}>Unified Communication Centre</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
            Authorized notification & messaging bridge. Clearly distinguishes Notification Mirroring from direct OS telephony permissions.
          </p>
        </div>

        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          {[
            { id: 'all', label: 'All' },
            { id: 'android_notification', label: 'Android Notifications' },
            { id: 'whatsapp_notification', label: 'WhatsApp (Notification)' },
            { id: 'messaging_notification', label: 'Messaging' },
            { id: 'sms', label: 'Permitted SMS' },
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

      {/* Platform Privacy & Access Transparency Banner */}
      <div className="card" style={{ borderColor: 'rgba(56, 189, 248, 0.28)', display: 'flex', gap: '12px', alignItems: 'flex-start' }}>
        <Info size={18} color="var(--accent-primary)" style={{ marginTop: '2px', flexShrink: 0 }} />
        <div style={{ fontSize: '12.5px', color: 'var(--text-secondary)' }}>
          <strong style={{ color: 'var(--text-primary)' }}>Access Level Transparency:</strong> WhatsApp, Signal, Telegram, and Android app alerts are synced exclusively via Android&apos;s user-authorized <span className="mono">NotificationListenerService</span> &amp; <span className="mono">RemoteInput</span> inline reply actions—HOME OS never scrapes private app databases or bypasses end-to-end encryption. On iOS, Apple restricts global notification, SMS, and call-log access inside the app sandbox.
        </div>
      </div>

      {items.length === 0 ? (
        <div className="empty-state">
          <MessageSquare size={26} style={{ marginBottom: '8px', opacity: 0.6 }} />
          <div>No active communications matching the selected filter.</div>
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
                  <div style={{ marginTop: '8px', fontSize: '13.5px' }}>{item.body_preview}</div>
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
                    placeholder={`Send authorized RemoteInput reply to ${item.sender}...`}
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

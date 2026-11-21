import React, { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, Play, Plus, Trash2, Zap } from 'lucide-react';
import { apiRequest } from '../api/client';
import type { AutomationRule, AutomationRun, Device } from '../types/models';

interface Props {
  devices: Device[];
  onNotify: (msg: string) => void;
}

export const AutomationsView: React.FC<Props> = ({ devices, onNotify }) => {
  const registered = devices.filter((d) => d.is_registered);
  const [rules, setRules] = useState<AutomationRule[]>([]);
  const [runs, setRuns] = useState<AutomationRun[]>([]);

  // Rule builder state
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [triggerType, setTriggerType] = useState<'event' | 'schedule' | 'device_state'>('schedule');
  const [scheduleAt, setScheduleAt] = useState('00:00');
  const [eventName, setEventName] = useState('device.connected');
  const [stateDeviceId, setStateDeviceId] = useState('');
  const [stateValue, setStateValue] = useState('20');

  const [conditionDeviceId, setConditionDeviceId] = useState('');
  const [conditionStatus, setConditionStatus] = useState('offline');

  const [actionType, setActionType] = useState<'device_command' | 'hub_notification'>('hub_notification');
  const [actionDeviceId, setActionDeviceId] = useState('');
  const [actionCapability, setActionCapability] = useState('power.set');
  const [actionParamsJson, setActionParamsJson] = useState('{"on": false}');
  const [notifTitle, setNotifTitle] = useState('HOME OS Alert');
  const [notifMessage, setNotifMessage] = useState('Automation rule triggered.');
  const [maxRetries, setMaxRetries] = useState(2);
  const [requiresConfirmation, setRequiresConfirmation] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const [rRes, runRes] = await Promise.all([
        apiRequest<{ rules: AutomationRule[] }>('/automations/rules'),
        apiRequest<{ runs: AutomationRun[] }>('/automations/runs'),
      ]);
      setRules(rRes.rules || []);
      setRuns(runRes.runs || []);
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Failed to load automations');
    }
  }, [onNotify]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const applyPreset = (preset: 'phone_offline' | 'media_evening' | 'midnight_shutdown') => {
    if (preset === 'phone_offline') {
      setName('Phone Offline Alert on Network Join');
      setDescription('When laptop connects to home network, notify if phone is offline.');
      setTriggerType('event');
      setEventName('device.connected');
      setActionType('hub_notification');
      setNotifTitle('Phone Offline Check');
      setNotifMessage('Your laptop connected to the home network while your phone is offline.');
    } else if (preset === 'media_evening') {
      setName('Evening Speaker Playback & TV Volume Reduction');
      setDescription('Play media on speaker and lower television volume.');
      setTriggerType('schedule');
      setScheduleAt('20:00');
      setActionType('device_command');
      setActionCapability('volume.set');
      setActionParamsJson('{"level": 20}');
    } else {
      setName('Midnight Appliance Shutdown');
      setDescription('Turn off selected compatible appliances at 00:00.');
      setTriggerType('schedule');
      setScheduleAt('00:00');
      setActionType('device_command');
      setActionCapability('power.set');
      setActionParamsJson('{"on": false}');
      setRequiresConfirmation(true);
    }
  };

  const handleCreateRule = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const triggerConfig =
        triggerType === 'schedule'
          ? { at: scheduleAt }
          : triggerType === 'event'
          ? { event_type: eventName }
          : { device_id: stateDeviceId, field: 'battery_level', op: 'lte', value: Number(stateValue) };

      const conditions = conditionDeviceId
        ? {
            operator: 'all',
            clauses: [{ type: 'device_status', device_id: conditionDeviceId, op: 'eq', value: conditionStatus }],
          }
        : { operator: 'all', clauses: [] };

      const actions =
        actionType === 'hub_notification'
          ? [{ type: 'hub_notification', title: notifTitle, message: notifMessage }]
          : [
              {
                type: 'device_command',
                device_id: actionDeviceId,
                capability_key: actionCapability,
                parameters: JSON.parse(actionParamsJson),
              },
            ];

      await apiRequest('/automations/rules', {
        method: 'POST',
        body: JSON.stringify({
          name,
          description,
          trigger_type: triggerType,
          trigger_config: triggerConfig,
          conditions,
          actions,
          retry_policy: { max_retries: maxRetries },
          requires_confirmation: requiresConfirmation,
        }),
      });
      setName('');
      setDescription('');
      onNotify('Automation rule created.');
      await loadData();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : 'Invalid automation configuration');
    }
  };

  const toggleRule = async (rule: AutomationRule) => {
    await apiRequest(`/automations/rules/${rule.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled: !rule.enabled }),
    });
    await loadData();
  };

  const triggerRuleNow = async (ruleId: string) => {
    const res = await apiRequest<{ run: AutomationRun }>(`/automations/rules/${ruleId}/trigger`, {
      method: 'POST',
    });
    onNotify(`Automation run ${res.run.status}.`);
    await loadData();
  };

  const approveRun = async (runId: string) => {
    await apiRequest(`/automations/runs/${runId}/approve`, { method: 'POST' });
    onNotify('Confirmed and executed sensitive automation run.');
    await loadData();
  };

  const deleteRule = async (ruleId: string) => {
    await apiRequest(`/automations/rules/${ruleId}`, { method: 'DELETE' });
    await loadData();
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div className="section-header">
        <div>
          <h2 style={{ fontSize: '19px', fontWeight: 700 }}>Cross-Device Automation Engine</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
            Structured event, schedule, and device-state rules with compound conditions, retries, and sensitive action confirmation.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <button className="btn btn-sm" onClick={() => applyPreset('phone_offline')}>
            Blueprint: Phone Offline Alert
          </button>
          <button className="btn btn-sm" onClick={() => applyPreset('media_evening')}>
            Blueprint: Evening Media
          </button>
          <button className="btn btn-sm" onClick={() => applyPreset('midnight_shutdown')}>
            Blueprint: Midnight Shutdown
          </button>
        </div>
      </div>

      {/* Rule Builder */}
      <div className="card">
        <div className="section-title" style={{ marginBottom: '12px' }}>Create Structured Automation Rule</div>
        <form onSubmit={handleCreateRule} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: '12px' }}>
          <div>
            <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Rule Name</label>
            <input className="input" style={{ width: '100%' }} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Midnight Appliance Off" required />
          </div>

          <div>
            <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Trigger Type</label>
            <select className="select" style={{ width: '100%' }} value={triggerType} onChange={(e) => setTriggerType(e.target.value as 'event' | 'schedule' | 'device_state')}>
              <option value="schedule">Time Schedule (UTC HH:MM)</option>
              <option value="event">System / Network Event</option>
              <option value="device_state">Device Battery Threshold</option>
            </select>
          </div>

          {triggerType === 'schedule' && (
            <div>
              <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Time (HH:MM)</label>
              <input className="input" style={{ width: '100%' }} value={scheduleAt} onChange={(e) => setScheduleAt(e.target.value)} placeholder="00:00" />
            </div>
          )}

          {triggerType === 'event' && (
            <div>
              <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Event Key</label>
              <select className="select" style={{ width: '100%' }} value={eventName} onChange={(e) => setEventName(e.target.value)}>
                <option value="device.connected">device.connected</option>
                <option value="device.disconnected">device.disconnected</option>
                <option value="communication.received">communication.received</option>
              </select>
            </div>
          )}

          {triggerType === 'device_state' && (
            <>
              <div>
                <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Monitored Device</label>
                <select className="select" style={{ width: '100%' }} value={stateDeviceId} onChange={(e) => setStateDeviceId(e.target.value)} required>
                  <option value="">Select device...</option>
                  {registered.map((d) => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Battery &lt;= (%)</label>
                <input className="input" style={{ width: '100%' }} type="number" value={stateValue} onChange={(e) => setStateValue(e.target.value)} />
              </div>
            </>
          )}

          <div>
            <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Optional Condition: If Device Status Is</label>
            <div style={{ display: 'flex', gap: '6px' }}>
              <select className="select" style={{ flex: 1 }} value={conditionDeviceId} onChange={(e) => setConditionDeviceId(e.target.value)}>
                <option value="">(No condition)</option>
                {registered.map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </select>
              <select className="select" value={conditionStatus} onChange={(e) => setConditionStatus(e.target.value)}>
                <option value="offline">offline</option>
                <option value="online">online</option>
              </select>
            </div>
          </div>

          <div>
            <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Action Type</label>
            <select className="select" style={{ width: '100%' }} value={actionType} onChange={(e) => setActionType(e.target.value as 'device_command' | 'hub_notification')}>
              <option value="hub_notification">Send Command Centre Alert</option>
              <option value="device_command">Execute Enrolled Device Command</option>
            </select>
          </div>

          {actionType === 'hub_notification' ? (
            <>
              <div>
                <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Alert Title</label>
                <input className="input" style={{ width: '100%' }} value={notifTitle} onChange={(e) => setNotifTitle(e.target.value)} />
              </div>
              <div>
                <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Alert Message</label>
                <input className="input" style={{ width: '100%' }} value={notifMessage} onChange={(e) => setNotifMessage(e.target.value)} />
              </div>
            </>
          ) : (
            <>
              <div>
                <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Target Device</label>
                <select className="select" style={{ width: '100%' }} value={actionDeviceId} onChange={(e) => setActionDeviceId(e.target.value)} required>
                  <option value="">Select target device...</option>
                  {registered.map((d) => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Capability Key</label>
                <input className="input mono" style={{ width: '100%' }} value={actionCapability} onChange={(e) => setActionCapability(e.target.value)} />
              </div>
              <div>
                <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Parameters JSON</label>
                <input className="input mono" style={{ width: '100%' }} value={actionParamsJson} onChange={(e) => setActionParamsJson(e.target.value)} />
              </div>
            </>
          )}

          <div>
            <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Max Retries</label>
            <input className="input" type="number" min={0} max={5} style={{ width: '100%' }} value={maxRetries} onChange={(e) => setMaxRetries(Number(e.target.value))} />
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', paddingTop: '18px' }}>
            <input
              type="checkbox"
              id="req_conf"
              checked={requiresConfirmation}
              onChange={(e) => setRequiresConfirmation(e.target.checked)}
            />
            <label htmlFor="req_conf" style={{ fontSize: '12.5px' }}>
              Require Owner Confirmation Before Execution
            </label>
          </div>

          <div style={{ display: 'flex', alignItems: 'flex-end' }}>
            <button type="submit" className="btn btn-primary" style={{ width: '100%' }}>
              <Plus size={15} /> Save Automation Rule
            </button>
          </div>
        </form>
      </div>

      {/* Configured Rules & Execution History */}
      <div className="grid-2">
        <div className="card">
          <div className="section-title" style={{ marginBottom: '12px' }}>Configured Rules ({rules.length})</div>
          {rules.length === 0 ? (
            <div className="empty-state">No automation rules configured yet.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {rules.map((r) => (
                <div key={r.id} style={{ padding: '12px', background: 'var(--bg-canvas)', borderRadius: '10px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div>
                      <div style={{ fontWeight: 600 }}>{r.name}</div>
                      <div className="mono" style={{ color: 'var(--text-muted)', fontSize: '11.5px' }}>
                        trigger: {r.trigger_type} {JSON.stringify(r.trigger_config)}
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: '6px' }}>
                      <button className="btn btn-sm" onClick={() => toggleRule(r)}>
                        {r.enabled ? 'Disable' : 'Enable'}
                      </button>
                      <button className="btn btn-sm btn-primary" onClick={() => triggerRuleNow(r.id)}>
                        <Play size={12} /> Run
                      </button>
                      <button className="btn btn-sm btn-danger" onClick={() => deleteRule(r.id)}>
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card">
          <div className="section-title" style={{ marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Zap size={16} color="var(--accent-warning)" />
            Execution History & Confirmation Queue ({runs.length})
          </div>
          {runs.length === 0 ? (
            <div className="empty-state">No automation runs recorded yet.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', maxHeight: '420px', overflowY: 'auto' }}>
              {runs.map((run) => (
                <div key={run.id} style={{ padding: '12px', background: 'var(--bg-canvas)', borderRadius: '8px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <strong>{run.rule_name || run.rule_id}</strong>
                    <span
                      className={`badge ${
                        run.status === 'succeeded'
                          ? 'badge-online'
                          : run.status === 'awaiting_confirmation'
                          ? 'badge-unpaired'
                          : 'badge-isolated'
                      }`}
                    >
                      {run.status === 'succeeded' && <CheckCircle2 size={11} />}
                      {run.status}
                    </span>
                  </div>
                  <div style={{ fontSize: '11.5px', color: 'var(--text-muted)', marginTop: '3px' }}>
                    Reason: {run.trigger_reason} · {new Date(run.started_at).toLocaleTimeString()}
                  </div>
                  {run.status === 'awaiting_confirmation' && (
                    <button className="btn btn-success btn-sm" style={{ marginTop: '8px' }} onClick={() => approveRun(run.id)}>
                      Approve & Execute Steps
                    </button>
                  )}
                  {run.step_results.length > 0 && (
                    <div className="mono" style={{ marginTop: '6px', fontSize: '11px', color: 'var(--text-secondary)' }}>
                      {run.step_results.map((s, idx) => (
                        <div key={idx}>
                          Step {String(s.step)}: {String(s.type)} → {String(s.status)}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

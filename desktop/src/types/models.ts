export interface User {
  id: string;
  username: string;
  display_name: string;
  role: string;
  created_at: string | null;
  last_login_at: string | null;
}

export interface Room {
  id: string;
  name: string;
  floor: string | null;
  icon: string;
  created_at: string;
}

export interface DeviceCapability {
  id: string;
  capability_key: string;
  category: string;
  display_name: string;
  description: string | null;
  supported: boolean;
  unavailable_reason: string | null;
  requires_permission: string;
  requires_confirmation: boolean;
  parameters_schema: Record<string, unknown>;
}

export interface DevicePermission {
  id: string;
  device_id: string;
  permission_scope: string;
  status: 'granted' | 'revoked' | 'denied' | 'expired';
  granted_at: string | null;
  revoked_at: string | null;
  reason: string | null;
}

export interface Device {
  id: string;
  hardware_fingerprint: string;
  name: string;
  category: string;
  manufacturer: string | null;
  model: string | null;
  os_name: string | null;
  os_version: string | null;
  ip_address: string | null;
  mac_address: string | null;
  port: number | null;
  hostname: string | null;
  room_id: string | null;
  room_name: string | null;
  group_tags: string[];
  lifecycle_state: string;
  is_registered: boolean;
  discovery_source: string;
  adapter_type: string;
  connection_status: string;
  battery_level: number | null;
  is_charging: boolean | null;
  health_score: number | null;
  integration_health: string;
  health_details: Record<string, unknown>;
  metadata: Record<string, unknown>;
  first_seen_at: string | null;
  last_seen_at: string | null;
  enrolled_at: string | null;
  key_version: number | null;
  capabilities?: DeviceCapability[];
  permissions?: DevicePermission[];
}

export interface CommandExecution {
  id: string;
  device_id: string;
  device_name: string | null;
  automation_run_id: string | null;
  capability_key: string;
  parameters: Record<string, unknown>;
  status: string;
  verification_method: string | null;
  result: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  retry_count: number;
  created_at: string;
  completed_at: string | null;
}

export interface CommunicationItem {
  id: string;
  device_id: string;
  device_name: string | null;
  source_type: string;
  access_level: 'notification_only' | 'direct_permission';
  app_package: string | null;
  app_name: string;
  sender: string;
  title: string | null;
  body_preview: string;
  can_reply: boolean;
  is_read: boolean;
  dismissed: boolean;
  received_at: string;
}

export interface ActiveMediaSession {
  id: string;
  device_id: string;
  device_name: string | null;
  app_name: string;
  title: string;
  artist: string | null;
  album: string | null;
  playback_state: string;
  position_ms: number;
  duration_ms: number;
  volume_percent: number | null;
  is_muted: boolean;
  updated_at: string;
}

export interface MediaItem {
  id: string;
  device_id: string;
  media_type: 'photo' | 'video' | 'audio';
  title: string;
  remote_path: string;
  mime_type: string;
  size_bytes: number;
  indexed_at: string;
}

export interface FileTransfer {
  id: string;
  direction: string;
  source_device_id: string | null;
  target_device_id: string | null;
  source_path: string;
  target_path: string;
  file_name: string;
  mime_type: string;
  size_bytes: number;
  transferred_bytes: number;
  sha256_checksum: string | null;
  status: string;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface AutomationRule {
  id: string;
  name: string;
  description: string | null;
  enabled: boolean;
  trigger_type: 'event' | 'schedule' | 'device_state';
  trigger_config: Record<string, unknown>;
  conditions: Record<string, unknown>;
  actions: Array<Record<string, unknown>>;
  retry_policy: Record<string, unknown>;
  requires_confirmation: boolean;
  last_triggered_at: string | null;
  created_at: string;
}

export interface AutomationRun {
  id: string;
  rule_id: string;
  rule_name: string | null;
  trigger_reason: string;
  status: string;
  step_results: Array<Record<string, unknown>>;
  error_message: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface IntegrationConnection {
  id: string;
  integration_type: string;
  name: string;
  endpoint_url: string;
  status: string;
  health_message: string | null;
  config: Record<string, unknown>;
  last_sync_at: string | null;
  created_at: string;
}

export interface AuditLog {
  id: number;
  severity: 'info' | 'warning' | 'critical';
  category: string;
  event_type: string;
  actor_type: string;
  actor_id: string | null;
  device_id: string | null;
  ip_address: string | null;
  outcome: string;
  details: Record<string, unknown>;
  prev_hash: string;
  entry_hash: string;
  created_at: string;
}

export interface DashboardSummary {
  counts: {
    total_discovered: number;
    registered: number;
    unpaired: number;
    online: number;
    offline: number;
    isolated: number;
    pending_confirmations: number;
  };
  categories: Record<string, number>;
  rooms: Room[];
  devices: Device[];
  recent_activity: CommandExecution[];
  pending_commands: CommandExecution[];
  notifications: CommunicationItem[];
  active_media_sessions: ActiveMediaSession[];
  security_events: AuditLog[];
  automation_status: {
    active_rules: number;
    total_rules: number;
    awaiting_confirmation_runs: number;
  };
}

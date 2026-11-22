import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { App } from '../App';

const mockSummary = {
  counts: {
    total_discovered: 2,
    registered: 1,
    unpaired: 1,
    online: 1,
    offline: 0,
    isolated: 0,
    pending_confirmations: 1,
  },
  categories: { desktop: 1, tv: 1 },
  rooms: [{ id: 'r-1', name: 'Studio', floor: '1', icon: 'home', created_at: '2026-11-21T00:00:00Z' }],
  devices: [
    {
      id: 'dev-linux-1',
      hardware_fingerprint: 'linux-fp-1',
      name: 'Studio Linux Workstation',
      category: 'desktop',
      manufacturer: 'Linux Native Host',
      model: 'x86_64',
      os_name: 'Linux',
      os_version: '6.1',
      ip_address: '192.168.1.20',
      mac_address: null,
      port: null,
      hostname: 'studio-box',
      room_id: 'r-1',
      room_name: 'Studio',
      group_tags: [],
      lifecycle_state: 'authenticated',
      is_registered: true,
      discovery_source: 'paired',
      adapter_type: 'linux_agent',
      connection_status: 'online',
      battery_level: null,
      is_charging: null,
      health_score: 100,
      integration_health: 'healthy',
      health_details: { cpu_cores: 8 },
      metadata: {},
      first_seen_at: '2026-11-21T00:00:00Z',
      last_seen_at: '2026-11-21T00:05:00Z',
      enrolled_at: '2026-11-21T00:01:00Z',
      key_version: 1,
      capabilities: [
        {
          id: 'cap-1',
          capability_key: 'screen.capture',
          category: 'screen',
          display_name: 'Authorized Display Snapshot',
          description: null,
          supported: false,
          unavailable_reason: 'No active X11 or Wayland display session detected.',
          requires_permission: 'screen:view',
          requires_confirmation: true,
          parameters_schema: {},
        },
      ],
      permissions: [
        {
          id: 'p-1',
          device_id: 'dev-linux-1',
          permission_scope: 'telemetry:read',
          status: 'granted',
          granted_at: '2026-11-21T00:01:00Z',
          revoked_at: null,
          reason: null,
        },
      ],
    },
    {
      id: 'dev-tv-unpaired',
      hardware_fingerprint: 'mdns-tv-2',
      name: 'Unpaired Living Room TV',
      category: 'tv',
      manufacturer: 'Sony',
      model: 'Bravia',
      os_name: 'Android TV',
      os_version: null,
      ip_address: '192.168.1.55',
      mac_address: null,
      port: 6466,
      hostname: null,
      room_id: null,
      room_name: null,
      group_tags: [],
      lifecycle_state: 'identified',
      is_registered: false,
      discovery_source: 'mdns',
      adapter_type: 'google_tv',
      connection_status: 'unpaired',
      battery_level: null,
      is_charging: null,
      health_score: null,
      integration_health: 'unverified_until_paired',
      health_details: {},
      metadata: {},
      first_seen_at: '2026-11-21T00:00:00Z',
      last_seen_at: '2026-11-21T00:05:00Z',
      enrolled_at: null,
      key_version: null,
      capabilities: [],
      permissions: [],
    },
  ],
  recent_activity: [],
  pending_commands: [
    {
      id: 'cmd-sens-1',
      device_id: 'dev-linux-1',
      device_name: 'Studio Linux Workstation',
      automation_run_id: null,
      capability_key: 'lock.unlock',
      parameters: { door: 'front' },
      status: 'pending_confirmation',
      verification_method: null,
      result: {},
      error_code: null,
      error_message: null,
      retry_count: 0,
      created_at: '2026-11-21T00:04:00Z',
      completed_at: null,
    },
  ],
  notifications: [],
  active_media_sessions: [],
  security_events: [],
  automation_status: {
    active_rules: 1,
    total_rules: 1,
    awaiting_confirmation_runs: 0,
  },
};

function installMockApi() {
  localStorage.setItem('homeos_session_token', 'valid-token-xyz');
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.endsWith('/auth/status')) {
      return new Response(JSON.stringify({ setup_required: false, tls_fingerprint: 'fp123' }), { status: 200 });
    }
    if (url.endsWith('/auth/me')) {
      return new Response(
        JSON.stringify({ user: { id: 'u1', username: 'admin', display_name: 'System Owner', role: 'owner' } }),
        { status: 200 }
      );
    }
    if (url.endsWith('/dashboard/summary')) {
      return new Response(JSON.stringify(mockSummary), { status: 200 });
    }
    if (url.includes('/devices/dev-linux-1')) {
      return new Response(
        JSON.stringify({ device: mockSummary.devices[0], recent_commands: [], media_session: null }),
        { status: 200 }
      );
    }
    if (url.includes('/communications')) {
      return new Response(
        JSON.stringify({
          items: [
            {
              id: 'comm-1',
              device_id: 'dev-android-1',
              device_name: 'Pixel 9 Pro',
              source_type: 'whatsapp_notification',
              app_name: 'WhatsApp',
              sender: 'Adaobi',
              title: 'New Message',
              body_preview: 'Are you home?',
              can_reply: true,
              access_level: 'notification_only',
              is_read: false,
              received_at: '2026-11-21T00:05:00Z',
            },
          ],
        }),
        { status: 200 }
      );
    }
    if (url.includes('/files/browse/')) {
      return new Response(
        JSON.stringify({
          current_path: '',
          entries: [{ name: 'notes.txt', path: 'notes.txt', is_dir: false, size: 128 }],
        }),
        { status: 200 }
      );
    }
    if (url.includes('/files/transfers')) {
      return new Response(JSON.stringify({ transfers: [] }), { status: 200 });
    }
    if (url.includes('/media/sessions')) {
      return new Response(JSON.stringify({ sessions: [] }), { status: 200 });
    }
    if (url.includes('/media/items')) {
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    }
    if (url.includes('/automations/rules')) {
      return new Response(JSON.stringify({ rules: [] }), { status: 200 });
    }
    if (url.includes('/automations/runs')) {
      return new Response(
        JSON.stringify({
          runs: [
            {
              id: 'run-1',
              rule_id: 'rule-1',
              rule_name: 'Midnight Appliance Shutdown',
              trigger_reason: 'schedule:00:00',
              status: 'awaiting_confirmation',
              step_results: [],
              error_message: null,
              started_at: '2026-11-21T00:00:00Z',
              completed_at: null,
            },
          ],
        }),
        { status: 200 }
      );
    }
    if (url.includes('/integrations')) {
      return new Response(JSON.stringify({ integrations: [] }), { status: 200 });
    }
    if (url.includes('/security/permissions')) {
      return new Response(
        JSON.stringify({
          matrix: [
            {
              device_id: 'dev-linux-1',
              device_name: 'Studio Linux Workstation',
              category: 'desktop',
              adapter_type: 'linux_agent',
              lifecycle_state: 'authenticated',
              key_version: 1,
              permissions: mockSummary.devices[0].permissions,
            },
          ],
        }),
        { status: 200 }
      );
    }
    if (url.includes('/security/audit-logs')) {
      return new Response(
        JSON.stringify({
          logs: [
            {
              id: 1,
              event_type: 'auth.setup_owner',
              severity: 'info',
              actor_type: 'user',
              actor_id: 'u1',
              device_id: null,
              ip_address: '127.0.0.1',
              outcome: 'success',
              details: {},
              prev_hash: '0'.repeat(64),
              entry_hash: 'a'.repeat(64),
              created_at: '2026-11-21T00:00:00Z',
            },
          ],
        }),
        { status: 200 }
      );
    }
    if (url.includes('/security/audit-verify')) {
      return new Response(
        JSON.stringify({
          valid: true,
          checked_entries: 14,
          head_hash: 'f'.repeat(64),
          broken_at_id: null,
          reason: null,
        }),
        { status: 200 }
      );
    }
    if (url.includes('/system/settings')) {
      return new Response(
        JSON.stringify({
          app_name: 'HOME OS',
          version: '2.0.0',
          bind_host: '127.0.0.1',
          bind_port: 8420,
          database_dialect: 'sqlite',
          tls_cert_path: '/data/tls.crt',
          tls_sha256_fingerprint: 'deadbeef1234',
          session_ttl_minutes: 480,
          pairing_ttl_seconds: 300,
          replay_window_seconds: 60,
          discovery_protocols: ['mDNS'],
          interfaces: [{ name: 'eth0', operstate: 'up', mac: '00:11:22', addresses: ['192.168.1.20/24'] }],
        }),
        { status: 200 }
      );
    }
    if (url.includes('/auth/sessions')) {
      return new Response(
        JSON.stringify({
          sessions: [
            {
              id: 'sess-1234567890',
              ip_address: '127.0.0.1',
              user_agent: 'Vitest',
              created_at: '2026-11-21T00:00:00Z',
              expires_at: '2026-11-21T08:00:00Z',
              revoked_at: null,
              is_current: true,
            },
          ],
        }),
        { status: 200 }
      );
    }
    return new Response(JSON.stringify({}), { status: 200 });
  });
}

describe('HOME OS Desktop Application — All 9 Views', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('renders First-Run Security Vault Initialization when setup_required is true', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      new Response(
        JSON.stringify({ setup_required: true, tls_fingerprint: 'abcd1234fingerprint' }),
        { status: 200 }
      )
    );

    render(<App />);
    await waitFor(() => {
      expect(screen.getByText(/First-Run Security Vault Initialization/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/abcd1234fingerprint/i)).toBeInTheDocument();
  });

  it('navigates across Command Centre, Devices, Device Details, Communications, Media, Automations, Integrations, Security, and Settings', async () => {
    installMockApi();
    render(<App />);

    // 1. Command Centre
    await waitFor(() => {
      expect(screen.getByText('Studio Linux Workstation')).toBeInTheDocument();
    });
    expect(screen.getByText(/Sensitive Operations Awaiting Explicit Owner Consent/i)).toBeInTheDocument();

    // 2. Devices
    fireEvent.click(screen.getByText('Devices'));
    await waitFor(() => {
      expect(screen.getByText(/Discovery Only — Zero Private Access/i)).toBeInTheDocument();
    });

    // 3. Device Details
    fireEvent.click(screen.getByText('Manage'));
    await waitFor(() => {
      expect(screen.getByText(/No active X11 or Wayland display session detected/i)).toBeInTheDocument();
    });

    // 4. Communications — verify Notification Mirroring vs Direct Permission transparency
    fireEvent.click(screen.getByText('Communications'));
    await waitFor(() => {
      expect(screen.getByText('Adaobi')).toBeInTheDocument();
      expect(screen.getByText('Notification Mirror Only')).toBeInTheDocument();
    });

    // 5. Media & Files
    fireEvent.click(screen.getByText('Media & Files'));
    await waitFor(() => {
      expect(screen.getByText('notes.txt')).toBeInTheDocument();
    });

    // 6. Automations
    fireEvent.click(screen.getByText('Automations'));
    await waitFor(() => {
      expect(screen.getByText('Approve & Execute Steps')).toBeInTheDocument();
    });

    // 7. Integrations
    fireEvent.click(screen.getByText('Integrations'));
    await waitFor(() => {
      expect(screen.getByText('Home Assistant Backbone')).toBeInTheDocument();
      expect(screen.getByText('Matter Fabric Bridge')).toBeInTheDocument();
    });

    // 8. Permissions & Security + Audit Chain Verification
    fireEvent.click(screen.getByText('Permissions & Security'));
    await waitFor(() => {
      expect(screen.getByText('Verify SHA-256 Audit Chain')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('Verify SHA-256 Audit Chain'));
    await waitFor(() => {
      expect(screen.getByText(/Cryptographic Chain Intact/i)).toBeInTheDocument();
    });

    // 9. Settings
    fireEvent.click(screen.getByText('Settings'));
    await waitFor(() => {
      expect(screen.getByText(/deadbeef1234/i)).toBeInTheDocument();
    });
  });
});

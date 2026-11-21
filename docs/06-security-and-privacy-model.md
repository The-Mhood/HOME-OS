# 06 — Security, Cryptography & Privacy Compliance Model

## 1. Zero-Privilege Discovery Principle
Discovering a device via mDNS, SSDP/UPnP, or ARP neighbor tables places it in `lifecycle_state = discovered` / `identified` with `is_registered = false`.
- **Zero private telemetry, notifications, files, or controls** are accessible on discovered-only devices.
- Every UI view explicitly badges unpaired devices as `Discovery Only — Zero Private Access`.

## 2. Cryptographic Primitives
| Control Area | Implementation |
|---|---|
| Owner Password Storage | **Argon2id** (`argon2-cffi`) |
| Local Secret Vault | **AES-256-GCM** envelope encryption keyed by `0600` local `master.key` |
| Hub TLS Identity | Auto-provisioned **ECDSA P-256** X.509 certificate + SHA-256 fingerprint pinning |
| Device Pairing | Short-lived (300s) 6-digit single-use pairing code + QR payload (`homeos://pair?...`) |
| Device Authentication | Per-device 256-bit secret with **HMAC-SHA256** over `device_id:timestamp:nonce:sha256(body)` |
| Replay Protection | Strict `±60s` timestamp skew check + database-backed `ReplayNonce` deduplication |
| Credential Rotation | Live key-version rotation (`POST /api/v1/devices/{id}/rotate-credentials`) invalidating prior secrets |
| Audit Trail Integrity | Append-only `AuditLog` with **SHA-256 hash chaining** (`entry_hash = SHA256(prev_hash || canonical_payload)`), verifiable via CLI (`homeos verify-audit`) and UI |

## 3. Granular Permission Scopes & Instant Revocation
Every command passes through an **8-step execution pipeline** (`CommandOrchestrator`):
1. Device registration & isolation verification (`isolated` devices are hard-blocked).
2. Capability support verification (`supported == true`).
3. Granular permission scope check (`telemetry:read`, `notifications:read`, `notifications:reply`, `media:control`, `files:browse`, `files:transfer`, `screen:view`, `input:control`, `power:control`, `locks:control`, `processes:manage`, `apps:launch`).
4. Explicit user confirmation gate (`pending_confirmation`) for sensitive operations (`locks:control`, `screen:view`, `input:control`, `power:control`, `apps:launch`, or automation rules with `requires_confirmation`).
5. Per-device sliding-window rate limiting.
6. Adapter dispatch (`dispatched` / `acknowledged`).
7. Post-action verification (`verified` vs `failed` — HOME OS never reports physical execution merely because a packet was sent).
8. Tamper-evident SHA-256 audit log recording.

## 4. Emergency Disconnect Lockdown
Clicking **Emergency Disconnect** (`POST /api/v1/security/emergency-lockdown`):
- Marks all enrolled devices `lifecycle_state = isolated` and `connection_status = isolated`.
- Terminates active WebSocket agent sessions immediately.
- Pauses all enabled automation rules.
- Logs a `critical` tamper-evident audit entry.

## 5. Platform Privacy & Anti-Bypass Boundaries
HOME OS strictly respects operating system sandboxes and user consent:
- **WhatsApp / Messaging**: Uses Android's official `NotificationListenerService` and inline `RemoteInput` notification actions. Never reads private SQLite databases, accessibility text trees of private chats, or end-to-end encrypted keystores.
- **iOS**: Explicitly marks SMS, call history, global third-party notifications, WhatsApp access, and system-wide remote touch as `supported: false` with clear explanations rather than attempting private API workarounds.
- **Linux / macOS / Windows**: Never bypasses Wayland portal permission prompts, macOS TCC Screen Recording/Accessibility prompts, or Windows UAC/Winlogon Secure Desktop UIPI isolation.
- **File Access**: Enforces strict `Path.resolve()` canonical root jailing (`AuthorizedFileJail`) blocking `..` traversal and unauthorized symlink escapes.

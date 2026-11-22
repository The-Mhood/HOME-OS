# HOME OS — System Requirements & Architecture Specification

**Version:** 2.0.0  
**Architecture Style:** Modular Monolith Core + Authenticated Bidirectional Adapter Bus + Electron/React Desktop Command Shell

---

## 1. Seven-Stage Device Security Lifecycle

HOME OS enforces strict separation between seeing a network packet and controlling a device:

1. **`discovered`** — Network presence observed via mDNS, SSDP, ARP, or manual IP probe. **Zero private access.**
2. **`identified`** — Device category, manufacturer/model hint, and candidate adapter determined. Unknown devices remain visible in inventory with `adapter_type="generic_network"` and honest capability status.
3. **`enrolled`** — User explicitly initiates pairing (QR token, 6-digit PIN, or integration credential) and binds device identity to the local vault.
4. **`authenticated`** — Mutual cryptographic proof verified (SHA-256 hashed device token + AES-256-GCM stored HMAC-SHA256 signing secret / mutual TLS cert).
5. **`authorized`** — User explicitly grants one or more granular permission scopes (`telemetry:read`, `media:control`, `files:browse`, etc.). Default grant set on enrollment is minimal (`telemetry:read` only unless user selects additional scopes).
6. **`capability_synced`** — Adapter reports structured `CapabilityDescriptor` entries including `supported: bool`, `unavailable_reason: str | None`, `requires_permission: str`, and `requires_confirmation: bool`.
7. **`controllable`** — Commands permitted solely for capabilities where `lifecycle == authenticated`, `device != isolated`, `capability.supported == True`, and `permission == granted`.

---

## 2. Common Device Adapter Contract (`v1.0`)

All adapters inherit `BaseDeviceAdapter` (`backend/home_os/adapters/base.py`):

```python
class BaseDeviceAdapter(ABC):
    adapter_type: str
    contract_version: str = "1.0"

    async def discover(self, timeout: float = 3.0) -> list[DiscoveredEndpoint]: ...
    async def identify(self, endpoint: DiscoveredEndpoint) -> DeviceIdentity: ...
    async def pair(self, device_id: str, request: PairingRequest) -> PairingChallenge: ...
    async def authenticate(self, device_id: str, proof: AuthProof) -> AuthResult: ...
    async def get_status(self, device_id: str) -> DeviceStatusSnapshot: ...
    async def get_capabilities(self, device_id: str) -> list[CapabilityDescriptor]: ...
    async def execute_command(self, device_id: str, command: AdapterCommand) -> CommandExecutionReceipt: ...
    async def subscribe_events(self, device_id: str, callback: EventCallback) -> SubscriptionHandle: ...
    async def disconnect(self, device_id: str, reason: str = "operator_requested") -> None: ...
    async def revoke_access(self, device_id: str, reason: str = "permission_revoked") -> None: ...
```

---

## 3. Eight-Step Command Execution Pipeline

Every command submitted via UI, REST API, or Automation Engine executes through `CommandOrchestrator`:

1. **Caller Authentication** — Validates operator session JWT against non-revoked, non-expired `UserSession` record (or signed internal Automation run context).
2. **Device Authorization** — Confirms device exists, `lifecycle_state == "authenticated"`, `connection_status != "isolated"`, and active `DeviceCredential` is unrevoked and unexpired.
3. **Capability Validation** — Confirms target `capability_key` is registered on this device with `supported == True`. If `supported == False`, rejects with `CAPABILITY_UNSUPPORTED` and exact platform reason.
4. **Permission Validation** — Confirms required `permission_scope` has `status == "granted"` and is unexpired on this device. Otherwise rejects with `PERMISSION_DENIED`.
5. **Parameter & Consent Validation** — Validates parameters against JSON schema; if `requires_confirmation == True` and no valid confirmation approval is attached, pauses command in `pending_confirmation` state and notifies operator.
6. **Command Execution (with Timeouts & Retries)** — Assigns command UUID, enforces per-command `timeout_ms`, applies transient transport retries, and dispatches to the concrete device adapter.
7. **Result Verification** — Distinguishes `dispatched` / `acknowledged` from `verified`. Marks `verified` **only** when the adapter returns post-execution state confirmation or `get_status()` confirms the target state transition.
8. **Tamper-Evident Audit Logging** — Records command outcome in `AuditLog` with SHA-256 hash chaining (`entry_hash = SHA256(prev_hash || canonical_payload)`) and broadcasts real-time update over `/api/v1/ws`.

---

## 4. Repository Layout

```text
HOME-OS/
├── alembic/                      # Database migrations (SQLite & PostgreSQL compatible)
├── alembic.ini
├── backend/
│   └── home_os/
│       ├── main.py               # FastAPI app factory & lifespan management
│       ├── cli.py                # `homeos` CLI (serve, migrate, certgen, status)
│       ├── config.py             # Secure-by-default environment configuration
│       ├── db/                   # SQLAlchemy 2.x models & session management
│       ├── security/             # Argon2id, AES-256-GCM vault, HMAC replay guard, TLS, rate limit, hash-chain audit
│       ├── adapters/             # Base contract + Agent WS, Android, iOS, Windows, macOS, Linux, Google TV, Home Assistant, Matter, Generic LAN
│       ├── services/             # Discovery, Pairing, CommandOrchestrator, Communications, MediaFiles, AutomationEngine, EventBus
│       ├── api/                  # Versioned `/api/v1` REST & WebSocket routers
│       └── schemas/              # Shared Pydantic v2 API contracts
├── agents/
│   ├── linux/                    # Runnable native Python/Linux companion agent + systemd + .deb builder
│   ├── windows/                  # Native C# / .NET 8 Windows agent project
│   └── macos/                    # Native Swift 5.9 macOS agent + TCC permission handlers + LaunchAgent
├── companions/
│   ├── android/                  # Native Kotlin + Jetpack Compose Android companion app
│   └── ios/                      # Native Swift + SwiftUI iOS companion app + AppIntents + HomeKit
├── desktop/
│   ├── electron/                 # Hardened Electron main & preload TypeScript processes
│   ├── src/                      # React + TypeScript 9-view Desktop Command Interface
│   └── electron-builder.yml      # Cross-platform Windows/macOS/Linux installer config
├── scripts/                      # Packaging, installation, and verification scripts
├── tests/                        # Unit, integration, E2E, security, and performance test suites
└── docs/                         # Architecture, research, security, user & developer documentation
```

---

## 5. Acceptance Criteria Summary

1. **Zero Unauthenticated Access:** Every control, inventory, media, communication, automation, and security endpoint rejects unauthenticated requests with `401 Unauthorized` and logs the attempt.
2. **Zero Plaintext Credentials:** Database inspection confirms user passwords use Argon2id, device tokens use SHA-256 digests, and device HMAC secrets / Home Assistant tokens use AES-256-GCM ciphertext.
3. **Replay & Rate-Limit Protection:** Duplicate nonces or stale timestamps (`> 60s`) on signed device channels are rejected with `401/403` and trigger `replay_attack_blocked` security events.
4. **Honest Capability Representation:** Unsupported or unpermitted capabilities cannot be invoked and expose human-readable platform explanations to the desktop UI.
5. **Real Live Execution:** Local Linux machine can be discovered, paired, permissioned, monitored, browsed (within authorized directories), automated, isolated, and revoked end-to-end against the running backend and desktop interface.

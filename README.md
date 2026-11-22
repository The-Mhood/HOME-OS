# HOME OS

**Universal Personal Device Management Platform — Local-First Command Environment**

HOME OS is an installable, local-first personal command environment that enables a single authorized owner to discover, pair, inspect, organize, automate, and control permitted devices across mobile phones, computers, televisions, speakers, and smart-home ecosystems—without cloud lock-in, fabricated telemetry, or OS sandbox bypasses.

> **Executive & Principal Engineering Handoff (`v1.4.0`)**: See [`docs/EXECUTIVE_HANDOFF_AND_WORLD_CLASS_ROADMAP.md`](docs/EXECUTIVE_HANDOFF_AND_WORLD_CLASS_ROADMAP.md) for the complete technical due-diligence handoff, 8-adapter architecture inventory, security/cryptography audit, and the 18-day home-alpha execution sprint.

---

## Architecture Overview

```text
HOME-OS/
├── backend/home_os/         # FastAPI + AsyncIO + SQLAlchemy 2.0 + Alembic Modular Monolith
│   ├── adapters/            # Common Device Adapter Contract v1.0 (AgentWS, GoogleTV, HomeAssistant, Matter, Generic)
│   ├── security/            # Argon2id, AES-256-GCM Vault, ECDSA P-256 TLS, HMAC-SHA256 Replay Guard, Hash-Chained Audit
│   ├── services/            # 8-Step Command Orchestrator, Discovery, Pairing, Comms, Media/Files, Automation Engine
│   └── api/                 # Versioned REST (/api/v1) + Real-Time WebSocket Event Bus (/api/v1/ws)
├── desktop/                 # Electron + React 18 + TypeScript Desktop Command Environment (All 9 Functional Views)
├── agents/
│   ├── linux/               # Native Python 3.11 Linux Daemon (0600 Vault, /proc, PipeWire/MPRIS2, Root-Jailed Files)
│   ├── windows/             # Native C# / .NET 8 Windows Worker Service (DPAPI, WMI, SendInput, CoreAudio, GSMTC)
│   └── macos/               # Native Swift 5.9 macOS Daemon (Keychain, TCC ScreenCaptureKit / Quartz / NSWorkspace)
├── companions/
│   ├── android/             # Native Kotlin + Compose Android App (NotificationListener + WhatsApp RemoteInput, MediaProjection, SAF)
│   └── ios/                 # Native SwiftUI iOS App (Shortcuts/AppIntents, Security-Scoped Files, HomeKit/Matter)
├── alembic/                 # Reproducible 17-Table Relational Schema Migrations (SQLite WAL + PostgreSQL path)
├── tests/                   # Unit, Integration, Live Dual-Agent E2E, Security, and 25-Device Concurrency Load Tests
├── releases/                # Installable Wheels, Source Tarballs, Desktop Bundle, and SHA-256 Manifests
└── docs/                    # Architecture, Platform Feasibility, Risk Register, Security Model & Operator Guides
```

---

## Key Capabilities & Security Guarantees

1. **Zero-Privilege Discovery**: Discovering a LAN device via mDNS, SSDP/UPnP, or ARP never grants private access; unpaired devices remain explicitly badged `Discovery Only — Zero Private Access` until paired with short-lived single-use credentials.
2. **8-Step Verified Command Execution Pipeline**: Every command enforces registration, isolation state, capability support, granular permission scope, explicit owner confirmation for sensitive scopes (`locks:control`, `screen:view`, `input:control`, `power:control`, `apps:launch`), rate limiting, post-dispatch verification (`dispatched`/`acknowledged` vs `verified`), and SHA-256 hash-chained audit logging.
3. **All 9 Functional Desktop Views**:
   - **Command Centre**: Real backend metrics, room/category/status filtering, active media sessions, pending sensitive confirmations, recent activity, and security alerts.
   - **Devices**: LAN scanning, 6-digit + QR pairing workflow, unknown device inspection, room assignment, and instant isolation.
   - **Device Details**: Live telemetry, capability matrix with honest unavailable reasons, interactive controls (Google TV D-pad/PIN, volume, power), credential rotation, and granular permission toggles.
   - **Communications**: Authorized Android/WhatsApp/messaging notification mirroring with inline `RemoteInput` replies, clearly distinguished from direct OS SMS/call permissions.
   - **Media & Files**: Root-jailed remote directory browser, SHA-256 verified uploads/downloads, device-to-device verified file relay, and authenticated audio streaming.
   - **Automations**: Structured event, schedule, and device-state rules with compound conditions, retry policies, execution logs, and sensitive-action confirmation queues.
   - **Integrations**: Home Assistant REST/WebSocket backbone, Matter Server JSON-RPC bridge, and native companion platform matrix.
   - **Permissions & Security**: Global permission revocation matrix, one-click SHA-256 audit log chain verification, and **Emergency Disconnect All** lockdown.
   - **Settings**: Local ECDSA P-256 TLS fingerprint, network interface diagnostics, and operator session revocation.

---

## Quick Start

### 1. Install & Start Local Hub
```bash
# Install release packages into ~/.local/share/home-os/venv
bash scripts/install-local.sh

# Start Core Engine (binds strictly to 127.0.0.1:8420 by default)
homeos serve --host 127.0.0.1 --port 8420
```

### 2. Launch Desktop Command Environment
```bash
cd desktop
npm install
npm run dev
```

### 3. Pair Local Linux Host Agent
1. Open HOME OS, initialize your **System Owner** vault password, and generate a **Linux Agent** pairing code in **Devices**.
2. Enroll and start the native Linux agent:
```bash
mkdir -p ~/HomeOS-Shared
homeos-linux-agent enroll --hub http://127.0.0.1:8420 --code <6_DIGIT_CODE> --name "My Linux Host" --shared-dir ~/HomeOS-Shared
homeos-linux-agent run
```

---

## Running Automated Verification Suites

```bash
# Backend, Security, Live Dual-Agent E2E, and 25-Device Load Suite
.venv/bin/pytest -v --cov=home_os --cov=homeos_linux_agent

# Verify SHA-256 Tamper-Evident Audit Chain
.venv/bin/homeos verify-audit

# Desktop UI TypeScript + Vitest 9-View Suite + Production Bundle
cd desktop && npm test && npm run build
```

---

## Documentation Index

- [`docs/01-engineering-assessment.md`](docs/01-engineering-assessment.md) — Repository & Environment Assessment
- [`docs/02-platform-research.md`](docs/02-platform-research.md) — OS Permission & Sandbox Feasibility Matrix
- [`docs/03-requirements-and-architecture.md`](docs/03-requirements-and-architecture.md) — Modular Monolith, Data Model, Security & Adapter Contract
- [`docs/04-risk-register.md`](docs/04-risk-register.md) — Security, Privacy & Platform Risk Register
- [`docs/05-installation-and-setup.md`](docs/05-installation-and-setup.md) — Operator Setup & Multi-Platform Enrollment Guide
- [`docs/06-security-and-privacy-model.md`](docs/06-security-and-privacy-model.md) — Cryptographic Vault, Auth, Permissions & Anti-Bypass Model
- [`docs/07-adapter-developer-guide.md`](docs/07-adapter-developer-guide.md) — Extending HOME OS with New Device Adapters
- [`docs/08-troubleshooting-and-limitations.md`](docs/08-troubleshooting-and-limitations.md) — Troubleshooting & 5-Tier Deliverable Honesty Matrix
- [`docs/09-home-alpha-release-checklist.md`](docs/09-home-alpha-release-checklist.md) — Home-Alpha Release Gate (verified evidence)
- [`PROJECT_STATUS.md`](PROJECT_STATUS.md) — Complete Milestone, Test & Release Readiness Ledger

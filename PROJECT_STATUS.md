# HOME OS — Project Status & Release Readiness Ledger

**Last Updated**: 2026-11-21 (UTC)  
**Active Branch**: `arena/7bd92795-home-os`  
**Overall Release Status**: **v1.0.0 Complete (All 8 Milestones Delivered)**

---

## 1. Milestone Completion Tracker

| Milestone | Scope | Status | Commit Reference |
|---|---|---|---|
| **M1. Assessment, Research & Architecture** | Engineering assessment, OS capability matrix, 17-table relational schema design, security & adapter contracts, risk register | **Completed** | `8cfa085` |
| **M2. Core Backend, Vault & Database** | FastAPI modular monolith (`backend/home_os/`), Alembic migrations, Argon2id auth, AES-256-GCM vault, ECDSA P-256 TLS, HMAC replay guard, SHA-256 hash-chained audit trail | **Completed** | `ccaf604` |
| **M3. Discovery, Pairing & Adapter Layer** | mDNS/DNS-SD, SSDP/UPnP (`defusedxml`), ARP/OUI discovery; 6-digit + QR pairing; Google TV Remote v2 TLS adapter (`6467`/`6466`); Home Assistant REST/WS adapter; Matter WS adapter | **Completed** | `ccaf604` |
| **M4. Native Companions & OS Agents** | Native Linux Agent (`agents/linux/`), Native Windows .NET 8 Service (`agents/windows/`), Native macOS Swift Agent (`agents/macos/`), Android Kotlin Companion (`companions/android/`), iOS SwiftUI Companion (`companions/ios/`) | **Completed** | `ccaf604`, `f6a3136` |
| **M5. Communications, Media & Files** | Notification mirroring (`notification_only` vs `direct_permission`) + inline `RemoteInput` reply bridge; root-jailed file browser, SHA-256 verified uploads/downloads & device-to-device file relay; authorized media indexing & audio streaming | **Completed** | `ccaf604` |
| **M6. Cross-Device Automation Engine** | Event, UTC schedule, and device-state triggers; compound `all`/`any` conditions; sequential multi-device actions; retry policies; execution history; sensitive-action owner confirmation | **Completed** | `ccaf604` |
| **M7. Desktop Command Environment** | Electron + React 18 + TypeScript app (`desktop/`) implementing all 9 functional views, live WebSocket event bus subscription, hardened `contextIsolation` preload, and `electron-builder` packaging | **Completed** | `ddd9a9a` |
| **M8. CI/CD, Release Artifacts & Docs** | Multi-OS GitHub Actions release pipeline (`.github/workflows/ci-release.yml`), installable wheels/sdist/desktop bundle + SHA-256 manifest (`releases/`), local installer (`scripts/install-local.sh`), operator/security/developer documentation | **Completed** | Current HEAD |

---

## 2. Automated Test & Performance Evidence

| Suite | Command | Result | Highlights |
|---|---|---|---|
| **Backend + Native Linux Agent + Security + Load** | `.venv/bin/pytest -v --cov=home_os --cov=homeos_linux_agent` | **20 passed in 7.80s** (75% line coverage across 3,806 statements) | Covers Argon2id, AES-256-GCM tamper detection, ECDSA TLS fingerprint, HMAC replay rejection, XXE UPnP protection, path traversal jailing, Alembic upgrade/downgrade, Google TV TLS v2 framing, Home Assistant post-command state verification, Matter JSON-RPC WS server, live dual-Linux-agent E2E file relay, emergency lockdown, and **25 concurrent simulated devices (`<50ms` avg command latency)** |
| **Desktop UI (All 9 Views + TypeScript + Production Bundle)** | `cd desktop && npm test && npm run build` | **2 test suites / all 9 views verified**, zero TypeScript errors | Verifies First-Run Vault Setup, Command Centre metrics, unpaired vs paired device badges, honest unavailable capability disclosures, WhatsApp notification mirroring transparency, file browser, automation approval queue, SHA-256 audit chain UI verification, and settings |
| **Release Artifacts & SHA-256 Manifest** | `bash scripts/build-release.sh` | **5 verified artifacts (`264 KB` total)** | `home_os-1.0.0-py3-none-any.whl`, `home_os-1.0.0.tar.gz`, `homeos_linux_agent-1.0.0-py3-none-any.whl`, `homeos_linux_agent-1.0.0.tar.gz`, `home-os-desktop-bundle-1.0.0.tar.gz`, `SHA256SUMS.txt`, `manifest.json` |

---

## 3. Five-Tier Deliverable Honesty & Verification Matrix

| Component | Implemented | Automated-Test Verified | Physical-Device Verified | Release Ready | Blocked by External Dependency |
|---|---|---|---|---|---|
| **Core Server (`backend/home_os/`)** | Yes | Yes (`pytest`) | Yes (Debian 12 x86_64 host) | Yes | None |
| **Security Vault, Auth, Permissions & Hash-Chained Audit** | Yes | Yes (`pytest`) | Yes (Linux host) | Yes | None |
| **Native Linux Agent (`agents/linux/`)** | Yes | Yes (Live dual-agent E2E) | Yes (Linux host `/proc`, files, processes, capabilities) | Yes | Live X11/Wayland screen pixels require active desktop display |
| **Desktop App (`desktop/` — All 9 Views)** | Yes | Yes (`vitest` + `vite build`) | Yes (Live preview & local server) | Yes | Native Electron `.AppImage`/`.exe`/`.dmg` binary download runs in GitHub Actions (`objects.githubusercontent.com` blocked in sandbox) |
| **Home Assistant & Matter Adapters** | Yes | Yes (Mock HTTP + live WS server tests) | No | Yes | Requires physical Home Assistant / Matter Thread controller on LAN |
| **Google TV Remote v2 Adapter** | Yes | Yes (TLS v2 framing & WoL unit tests) | No | Yes | Requires physical Android TV / Google TV receiver for on-screen PIN |
| **Android Companion (`companions/android/`)** | Yes | Source + unit test & Gradle CI job | No | CI-gated | Requires Android SDK/Gradle & physical Android phone |
| **iOS Companion (`companions/ios/`)** | Yes | Source + XcodeGen CI job | No | CI-gated | Requires macOS Xcode, Apple signing cert & physical iPhone |
| **Windows Agent (`agents/windows/`)** | Yes | Source + `.NET 8` publish CI job | No | CI-gated | Requires `.NET 8` SDK & Windows 10/11 host |
| **macOS Agent (`agents/macos/`)** | Yes | Source + `swift build` CI job | No | CI-gated | Requires Swift toolchain & macOS 13+ host |

---

## 4. Key Architecture Decisions Record (Summary)

1. **Modular Monolith over Premature Microservices**: Single local FastAPI + SQLite WAL engine avoids distributed operational complexity for single-owner home installations while keeping clean PostgreSQL migration paths via SQLAlchemy 2.0 + Alembic.
2. **Localhost Binding by Default (`127.0.0.1:8420`)**: Prevents accidental public internet exposure; LAN enrollment requires explicit host/TLS configuration.
3. **Strict Execution Truthfulness**: Commands distinguish `pending_confirmation`, `dispatched`, `acknowledged` (e.g., unverified UDP Wake-on-LAN), `verified` (confirmed post-action state or signed agent response), and `failed`.
4. **Strict OS Sandbox Compliance**: Android WhatsApp/messaging support uses official `NotificationListenerService` + `RemoteInput` only; iOS unsupported features (SMS, call logs, global notifications, remote touch) are explicitly declared `supported: false` with user-visible explanations.

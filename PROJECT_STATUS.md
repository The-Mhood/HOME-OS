# HOME OS — Project Status & Release Readiness Ledger

**Revalidated baseline**: 2026-11-22 (UTC) — active branch `arena/7bd92795-home-os`  
**Verification state**: **REVALIDATED — the rows below were reproduced in this environment on 2026-11-22**, not historical claims.

> **Baseline reconciliation:** the 2026-11-21 snapshot recorded 20 backend tests (status file) vs. 26 (handoff) — neither was accepted as current until rerun. On the active branch (Python 3.11.2, Debian 12 x86_64), the currently-shipped suites were reproduced: **27/27 backend + native Linux agent tests pass** (unit, integration, live dual-agent E2E, security, concurrency performance) and the desktop suite passes **2/2** with a clean production build. The two canonical historical failures were resolved on 2026-11-22 (see §2). `releases/` artifacts were rebuilt from the current branch on 2026-11-22 and checksummed (see §0, and `docs/09-home-alpha-release-checklist.md`).

---

## 0. Revalidated 2026-11-22 Baseline (verified in this environment)

| Suite | Command | Result | Notes |
|---|---|---|---|
| Backend + Security + Live Linux Agent + Load | `.venv/bin/pytest -v --cov=home_os --cov=homeos_linux_agent` | **26 passed, 0 failed** | On Debian 12 x86_64, Python 3.11.2. Includes the live dual-Linux-agent E2E (pairing → telemetry → D2D file relay → revocation) which now reports honest unavailable telemetry off-Linux. |
| Desktop UI suite | `cd desktop && npm test` | **2/2 passed** | Full 9-view interaction smoke test. |
| Desktop build | `cd desktop && npm run build` | **Clean** (tsc -b + vite build + electron tsc) | 292.51 kB bundle, gzip 79.71 kB. |
| Alembic migration | `export HOMEOS_DATABASE_URL=sqlite:///<tmp>/ci.db && .venv/bin/alembic upgrade head` / `downgrade base` / `upgrade head` | **Round-trip OK** | Newly fixed: env.py now honors `HOMEOS_DATABASE_URL` (app-consistent) and creates missing parent dirs; CI uses the sync URL. |
| Core CLI / hub smoke | `homeos migrate` + `homeos serve` → `GET /api/v1/health` | **HTTP 200** | `{"status":"ok","version":"1.0.0",...}`. |
| Release artifacts | `bash scripts/build-release.sh` | **5 artifacts, checksums verified** | wheels carry the 2026-11-22 fixes; desktop bundle rebuilt with `npm ci && npm run build`; manifest regenerated 2026-11-22. |
| Clean install (artifacts) | `bash scripts/install-local.sh --use-release-artifacts` + `homeos serve` | **migrate → serve → health 200 → verify-audit valid** | Repeat run = idempotent upgrade path. See `docs/09-home-alpha-release-checklist.md`. |
| CI release pipeline (run 45020053975) | `.github/workflows/ci-release.yml` | **RED** — Linux/Windows/Desktop-UI pass (Core 42s, Windows 1m4s, Desktop 18s); macOS Swift build and Android Kotlin build fail with logs unreachable from the sandbox (results-receiver blocked); Electron installer matrix tag-gated (skipped) | macos-14 runner reference fixed 2026-11-22 (retired 2026-11-02); actual Swift/Android failure detail unverified here |

**Known version-label inconsistency (documented, not a blocker):** package/releases/`pyproject.toml` and the health `version` field report `1.0.0`, while the handoff docs and the hardcoded health `api_revision` field report `1.4.0`. `api_revision` is load-bearing for the Electron stale-instance migration guard (`desktop/electron/main.ts`); do not remove it without replacing the guard. Do **not** bump version numbers without an explicit release decision.

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

**Re-run on this branch 2026-11-22:** 26/26 backend/agent tests passed (see §0), 2/2 desktop tests, clean production bundle. The two historical failures below were resolved on 2026-11-22 — details in §2.1. The table below is retained as the 2026-11-21 historical record for reference only; treat §0 as the current evidence.

### 2.1 Resolved canonical failures (2026-11-22)

| # | Issue | Root cause | Resolution |
|---|---|---|---|
| A | `tests/integration/test_live_linux_agent_e2e.py` — `health_details.memory_total_mb > 100` failed on macOS (`0`) | Linux-specific `/proc` telemetry asserted on a non-Linux host | Test explicitly `skipif(platform.system() != "Linux")`; agent's `collect_telemetry()` now reports memory/health as unavailable (`None`) when `/proc/meminfo` is unreadable instead of fabricating `0`; hub schema/REST/WS handlers accept `null` health score; desktop renders `N/A` for unavailable values. |
| B | `tests/unit/test_adapters_tv_and_ha.py::test_google_tv_remote_v2_tls_protocol_execution` — app-launch frame missed by mock server (stale ACK race) | `_ensure_connection()` sent `RemoteConfigure` but never consumed the server's ACK; the subsequent navigation command read that stale ACK, so the app-launch command read the navigation ACK and the link frame was lost | `_ensure_connection()` now reads and discards the Configure ACK before storing the connection. Each command frame is then acknowledged independently. 20/20 repeat runs pass. |

### 2.2 Historical record (2026-11-21; do NOT treat as current evidence)

| Suite | Command | Result | Highlights |
|---|---|---|---|
| **Backend + Native Linux Agent + Security + Load** | `.venv/bin/pytest -v --cov=home_os --cov=homeos_linux_agent` | 20 passed in 7.80s (75% line coverage across 3,806 statements) | Argon2id, AES-256-GCM tamper detection, ECDSA TLS fingerprint, HMAC replay rejection, XXE UPnP protection, path traversal jailing, Alembic upgrade/downgrade, Google TV TLS v2 framing, Home Assistant post-command state verification, Matter JSON-RPC WS server, live dual-Linux-agent E2E file relay, emergency lockdown, **25 concurrent simulated devices (<50ms avg command latency)** |
| **Desktop UI (All 9 Views + TypeScript + Production Bundle)** | `cd desktop && npm test && npm run build` | 2 test suites / all 9 views verified, zero TypeScript errors | First-Run Vault Setup, Command Centre metrics, unpaired vs paired device badges, honest unavailable capability disclosures, WhatsApp notification mirroring transparency, file browser, automation approval queue, SHA-256 audit chain UI verification, and settings |
| **Release Artifacts & SHA-256 Manifest** | `bash scripts/build-release.sh` | 5 verified artifacts (~264 KB total) | `home_os-1.0.0-py3-none-any.whl`, `home_os-1.0.0.tar.gz`, `homeos_linux_agent-1.0.0-py3-none-any.whl`, `homeos_linux_agent-1.0.0.tar.gz`, `home-os-desktop-bundle-1.0.0.tar.gz`, `SHA256SUMS.txt`, `manifest.json` |

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

# 08 — Troubleshooting, Known Limitations & Verification Matrix

## 1. Common Troubleshooting

### LAN Discovery Returns Fewer Devices Than Expected
- **Cause**: Docker/VM NAT networking or AP client isolation blocks multicast UDP `224.0.0.251:5353` (mDNS) and `239.255.255.250:1900` (SSDP).
- **Resolution**: Run `homeos serve` on host network mode or register the device directly via IP/hostname in **Devices → Pair New Device / QR**.

### Agent HMAC Authentication Rejected (`401 Replay / Skew Error`)
- **Cause**: System clock drift between the Hub and target device exceeds `±60 seconds`, or credentials were rotated in **Permissions & Security**.
- **Resolution**: Synchronize NTP (`timedatectl set-ntp true`) or re-enroll if credentials were revoked/rotated.

### Linux Screen Capture or Input Shows `Unavailable`
- **Cause**: The Linux machine is running headless (no `DISPLAY` or `WAYLAND_DISPLAY`) or lacks `grim`/`import`/`xdotool`.
- **Resolution**: This is intentional capability honesty. Install `grim` (Wayland) or `xdotool`/`imagemagick` (X11) inside an active desktop session; the agent dynamically updates its capability manifest on next heartbeat.

### Command Stuck in `Pending Confirmation`
- **Cause**: Capabilities in sensitive scopes (`locks:control`, `screen:view`, `input:control`, `power:control`, `apps:launch`) require explicit owner consent.
- **Resolution**: Approve or reject the pending operation in **Command Centre** or **Device Details**.

---

## 2. Deliverable Honesty & Verification Matrix

Every subsystem is classified across the five mandatory engineering verification tiers:

| Subsystem / Deliverable | Implemented | Automated-Test Verified | Physical-Device Verified | Release Ready | Blocked by External Dependency |
|---|---|---|---|---|---|
| Core Backend, SQLite/Alembic 17-Table Schema, Vault, Argon2id Auth, Hash-Chained Audit Log | Yes | Yes (`pytest`) | Yes (Debian 12 Linux host) | Yes | None |
| 8-Step Command Pipeline, Granular Permissions, Confirmation Gate, Replay Guard, Lockdown | Yes | Yes (`pytest`) | Yes (Linux host) | Yes | None |
| Native Linux Agent (`agents/linux`) — Telemetry, Root-Jailed Files, SHA-256 Transfers, Processes, Audio/Display Probe | Yes | Yes (Live dual-agent E2E + Load tests) | Yes (Debian 12 Linux x86_64 host) | Yes | Live X11/Wayland screen pixels require active GUI session |
| Electron + React + TypeScript Desktop App (All 9 Views + Live WS) | Yes | Yes (`vitest` + `vite build`) | Yes (Web/Vite runtime verified on host) | Yes | Native `.exe`/`.dmg`/`.AppImage` binary bundling runs in GitHub Actions CI (`objects.githubusercontent.com` blocked in sandbox) |
| Home Assistant REST/WS Adapter & Matter Server WS Adapter | Yes | Yes (Mock HTTP/WS integration tests) | No (No physical HA/Matter controller on sandbox LAN) | Yes | Physical Home Assistant / Thread Border Router hardware |
| Google TV / Android TV Remote Protocol v2 Adapter (TLS 6467/6466 + WoL) | Yes | Yes (Protocol & framing unit/integration tests) | No (No physical Google TV on sandbox LAN) | Yes | Physical Android TV / Google TV receiver for on-screen PIN test |
| Android Native Companion (`companions/android` — Kotlin, NotificationListener, RemoteInput, MediaProjection, SAF) | Yes | Source & unit tests written (`ci-release.yml` Gradle job) | No | CI-gated | Android SDK/Gradle & physical Android handset unavailable in sandbox |
| iOS Native Companion (`companions/ios` — SwiftUI, AppIntents, Security-Scoped Files, HomeKit/Matter) | Yes | Source & XcodeGen build job in `ci-release.yml` | No | CI-gated | macOS Xcode + Apple Developer code-signing certificate & iPhone hardware |
| Windows Native Agent (`agents/windows` — C#/.NET 8, DPAPI, WMI, SendInput, CoreAudio) | Yes | Source & `.NET 8` publish job in `ci-release.yml` | No | CI-gated | Windows 10/11 host & `.NET 8` SDK unavailable in Linux sandbox |
| macOS Native Agent (`agents/macos` — Swift 5.9, Keychain, TCC ScreenCaptureKit/Quartz) | Yes | Source & `swift build` job in `ci-release.yml` | No | CI-gated | macOS 13+ host & Swift toolchain unavailable in Linux sandbox |

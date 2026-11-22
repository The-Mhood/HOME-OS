# 05 — Installation, Enrollment & Operator Setup Guide

## 1. Local Core Hub & Desktop Command Environment

### Quick One-Command Local Install (Linux / macOS)
```bash
bash scripts/install-local.sh
```
This installs `homeos` (Core Engine CLI) and `homeos-linux-agent` (Native Linux Companion Daemon) from the **current source tree** into `~/.local/share/home-os/venv` and links binaries into `~/.local/bin/`. To instead verify the committed `releases/` artifacts (historical builds, not re-verified), pass `--use-release-artifacts`.

### Starting the Core Engine
```bash
# Run database migrations (automatically executed on startup as well)
homeos migrate

# Start local-first FastAPI hub (binds strictly to 127.0.0.1:8420 by default)
homeos serve --host 127.0.0.1 --port 8420

# Optional: Serve directly over local ECDSA P-256 TLS
homeos serve --host 0.0.0.0 --port 8420 --tls
```

### Launching the Desktop Command Environment
```bash
cd desktop
npm install
npm run dev       # Development server with WS + REST proxy
npm run build     # Compiles dist/ and dist-electron/
```
When `desktop/dist` is built, `homeos serve` also serves the compiled SPA directly at `http://127.0.0.1:8420/`.

---

## 2. First-Run Security Vault Initialization
1. Open the HOME OS Desktop App or web console.
2. On first launch (`setup_required = true`), HOME OS displays its local **ECDSA P-256 TLS Certificate SHA-256 Fingerprint** and prompts you to create the **System Owner** account.
3. No default passwords or hardcoded credentials exist anywhere in the codebase. Passwords are hashed with **Argon2id**.

---

## 3. Enrolling Native Platform Agents & Mobile Companions

### A. Linux Native Agent (`agents/linux`)
1. In HOME OS → **Devices** → click **Pair New Device / QR**, choose **Linux Agent**, select desired permission scopes, and copy the 6-digit pairing code.
2. On the Linux machine:
   ```bash
   mkdir -p ~/HomeOS-Shared
   homeos-linux-agent enroll \
     --hub http://127.0.0.1:8420 \
     --code 123456 \
     --name "Workstation Linux" \
     --shared-dir ~/HomeOS-Shared

   homeos-linux-agent run
   ```
3. Optional user `systemd` service:
   ```bash
   cp agents/linux/systemd/homeos-linux-agent.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now homeos-linux-agent
   ```

### B. Windows 10/11 Native Agent (`agents/windows`)
```powershell
cd agents\windows
dotnet publish HomeOs.WindowsAgent\HomeOs.WindowsAgent.csproj -c Release -r win-x64 --self-contained -o C:\ProgramData\HomeOS\bin
C:\ProgramData\HomeOS\bin\HomeOs.WindowsAgent.exe enroll --hub https://<HUB_IP>:8420 --code <6_DIGIT_CODE> --name "Gaming-PC"
sc.exe create HomeOsAgent binPath= "C:\ProgramData\HomeOS\bin\HomeOs.WindowsAgent.exe" start= auto
sc.exe start HomeOsAgent
```
Secrets are encrypted using Windows **DPAPI (`ProtectedData`)**.

### C. macOS 13+ Native Agent (`agents/macos`)
```bash
# Full one-command macOS install (Core Hub + Desktop UI + Swift macOS Agent):
bash scripts/install-macos.sh

# Pair using QR URI or Session ID + 6-digit PIN from Devices -> Pair New Device:
~/.local/bin/homeos-mac-agent pair --qr "homeos://pair?..." --name "MacBook-Pro"
~/.local/bin/homeos-mac-agent run
```
Secrets are stored in **macOS Keychain**. Grant Screen Recording and Accessibility under **System Settings → Privacy & Security** only if remote display snapshots or input injection are desired.

### D. Android Companion (`companions/android`)
- Build flavors:
  - `playStore`: Play-compliant notification mirroring (`NotificationListenerService`), inline WhatsApp/messaging replies (`RemoteInput`), `MediaProjection` screen capture, SAF file sharing, torch, and media controls.
  - `directCompanion`: Adds opt-in SMS/call-state bridge for direct-install enterprise/personal APK builds.
- Open the Android app, enter the Hub URL + 6-digit Pairing Code (or scan the pairing QR payload), and explicitly toggle only the Android OS permissions you choose to grant.

### E. iOS Companion (`companions/ios`)
- Generate Xcode project via `xcodegen generate` inside `companions/ios`, sign with an Apple Developer team, and install onto iPhone/iPad (iOS 17+).
- Supports enrollment, battery/thermal/storage telemetry, Apple **Shortcuts (`AppIntents`)**, security-scoped folder sharing, media playback state, and HomeKit/Matter fabric delegation.

### F. Google TV / Android TV
1. Ensure your Google TV / Sony Bravia / Shield TV is on the same LAN.
2. Run **Scan Network** in **Devices** (discovers `_androidtvremote2._tcp.local.` on TLS ports `6467`/`6466`).
3. Initiate Google TV pairing to trigger the 6-hex-digit PIN on the television screen and submit the PIN to complete mutual certificate pairing.

### G. Home Assistant & Matter
1. Navigate to **Integrations**.
2. Enter your Home Assistant URL and Long-Lived Access Token (stored encrypted via AES-256-GCM in the local vault) or your `python-matter-server` WebSocket endpoint (`ws://127.0.0.1:5580/ws`).
3. Click **Sync** to import lights, switches, speakers, climate devices, sensors, scenes, and locks.

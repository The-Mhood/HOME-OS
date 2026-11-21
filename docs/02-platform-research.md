# HOME OS — Platform API, Protocol & Security Research

**Date:** 2026-11-21

---

## 1. Local Network Discovery Protocols

| Protocol | Mechanism | Target Devices | Security & Privacy Notes |
|---|---|---|---|
| **mDNS / DNS-SD** (RFC 6762 / 6763) | Multicast UDP `224.0.0.251:5353` browsing `_homeos._tcp.local.`, `_androidtvremote2._tcp.local.`, `_googlecast._tcp.local.`, `_home-assistant._tcp.local.`, `_matter._tcp.local.`, `_matterc._udp.local.`, `_hap._tcp.local.`, `_airplay._tcp.local.`, `_companion-link._tcp.local.` | HOME OS agents, Android TV / Google TV, Chromecast, Home Assistant, Matter nodes, Apple devices | Unauthenticated broadcast metadata; treated strictly as untrusted `discovered` hints until paired. |
| **SSDP / UPnP** | Multicast UDP `239.255.255.250:1900` `M-SEARCH` (`ST: ssdp:all`) + HTTP XML description fetch | Smart TVs, speakers (Sonos/DLNA), routers, Wi-Fi appliances | XML parsed strictly via `defusedxml` to block XXE/billion-laughs attacks; `LOCATION` URL validated against RFC1918/link-local addresses to prevent SSRF. |
| **ARP / Neighbour Inspection** | Linux `/proc/net/arp`, `ip -j neigh`, `ip -j addr` + IEEE OUI prefix lookup | Any IPv4/IPv6 LAN host even when multicast is silent | Identifies IP/MAC presence only; classified honestly as `generic_network` (`Unpaired — Discovery Only`). |
| **Manual & QR Pairing** | Ephemeral `homeos://pair` URI with host, port, TLS cert SHA-256 fingerprint, and short-lived single-use pairing token | Android, iOS, segmented VLANs where multicast does not traverse | Token expires in 300s, single-use, hashed at rest, bound to challenge nonce. |

---

## 2. Android Platform Capabilities & Policy Boundaries (API 29–35)

| Capability | Official Android API | Required User Action / Permission | Limitations & Google Play Policy |
|---|---|---|---|
| **Enrollment & Crypto** | `AndroidKeyStore` (`EC` / `AES/GCM/NoPadding`) | Scan QR / enter pairing code | Hardware-backed keystore on compatible devices. |
| **Notifications & Inline Reply** | `NotificationListenerService` (`StatusBarNotification`, `Notification.Action`, `RemoteInput`) | User enables HOME OS in *Settings → Notification read, reply & control* | Legitimate mechanism for reading Android, WhatsApp, Telegram, Signal, and SMS notifications and sending inline replies **without** reading private app databases. |
| **Screen Viewing** | `MediaProjectionManager.createScreenCaptureIntent()` + Foreground Service (`mediaProjection`) | Explicit system consent dialog **every session** (enforced on Android 14+) | Cannot start silently in background; user always sees active cast indicator. |
| **Remote Touch / Navigation** | `AccessibilityService` (`dispatchGesture`, `performGlobalAction`) | Manual enablement in *Accessibility Settings* (+ *Allow restricted settings* on Android 13+ sideloaded APKs) | Google Play restricts `AccessibilityService` to accessibility use cases unless justified or distributed via direct/enterprise APK. Documented transparently in UI. |
| **Flashlight** | `CameraManager.setTorchMode(cameraId, enabled)` | No camera preview permission required for torch API |Unavailable only on devices without flash unit (`FLASH_INFO_AVAILABLE == false`). |
| **Media & Volume** | `MediaSessionManager.getActiveSessions()`, `AudioManager` | Notification listener access required for active media controller inspection; `AudioManager` handles stream volume (`STREAM_MUSIC`, `STREAM_RING`) | Do-Not-Disturb changes require `ACCESS_NOTIFICATION_POLICY`. |
| **File Access** | Storage Access Framework (`ACTION_OPEN_DOCUMENT_TREE`, `DocumentFile`) + `MediaStore` | User explicitly picks folder tree(s) | Avoids broad `MANAGE_EXTERNAL_STORAGE` unless explicitly needed; scoped to user-chosen directories. |
| **SMS & Call Log** | `SmsManager`, `Telephony.Sms`, `CallLog.Calls` | Runtime `READ_SMS`, `SEND_SMS`, `READ_CALL_LOG` | Google Play restricts these permissions to Default Phone/SMS handlers. Direct companion build supports runtime opt-in; Play-compliant flavour falls back cleanly to `NotificationListenerService` call/SMS notifications. |

---

## 3. iOS Platform Capabilities & Sandbox Boundaries (iOS 17+, Swift/SwiftUI)

Apple enforces strict per-app sandboxing on iOS. HOME OS implements every legitimate Apple API and explicitly marks impossible operations as unsupported:

### Supported on iOS
- **Secure Enrollment & Telemetry:** `CryptoKit` (`P256.Signing`, `HMAC<SHA256>`), Keychain storage, `UIDevice` battery level/state, `ProcessInfo.thermalState`, storage capacity, `NWPathMonitor` connectivity.
- **Shortcuts & App Intents:** `AppIntents` framework (`AppShortcutsProvider`) + `shortcuts://run-shortcut` URL execution for user-approved iOS automation workflows.
- **User-Authorized File Sharing:** `UIDocumentPickerViewController` / SwiftUI `.fileImporter` with security-scoped bookmarks (`startAccessingSecurityScopedResource`).
- **Supported Media Controls:** `MPMusicPlayerController.systemMusicPlayer` (Apple Music / system media queue when Media Library permission granted).
- **Smart Home:** `HomeKit` (`HMHomeManager` authorized homes, rooms, accessories, and characteristic reads/writes) and `MatterSupport` commissioning.

### Unsupported on iOS (Documented Explicitly in Code & UI)
- **System-wide Notification Sync / WhatsApp Notification Reading:** Unsupported for companion software (`ANCS` is hardware-restricted to paired Bluetooth LE accessories; third-party iOS apps cannot read another app's notifications).
- **SMS & Call History Access:** Completely prohibited by iOS sandbox; no public API exists.
- **Unattended Screen Sharing & Remote Touch Control:** ReplayKit `RPSystemBroadcastPickerView` requires live user broadcast initiation and iOS provides **zero** public API for injecting touch events into other apps.
- **Arbitrary Process Management:** Prohibited by iOS sandbox.

---

## 4. Desktop Agents (Windows, macOS, Linux)

| Platform | Implementation | Credential Storage | OS Security Boundaries Handled |
|---|---|---|---|
| **Windows 10/11** | C# / .NET 8 (`HomeOs.WindowsAgent`) | Windows DPAPI (`ProtectedData`, `CurrentUser`) | UIPI prevents non-elevated agents from injecting input into elevated (UAC) windows or the Winlogon Secure Desktop; explicitly detected and reported. Core Audio COM (`IAudioEndpointVolume`) & WinRT `GSMTC` for media. |
| **macOS 13+** | Swift 5.9 (`HomeOsMacAgent`) | macOS Keychain (`SecItem`) | TCC permissions checked via `CGPreflightScreenCaptureAccess()` (Screen Recording) and `AXIsProcessTrusted()` (Accessibility) before advertising `screen:view` or `input:remote`. |
| **Linux** | Python 3.11 (`homeos-linux-agent`) | AES-256-GCM encrypted store (`0600`) / Secret Service | Distinguishes `X11`, `Wayland` (compositor/PipeWire portal limits on global input/capture), and `headless` sessions; probes `wpctl`/`pactl`/`amixer` & `playerctl` MPRIS2 dynamically. |

---

## 5. Google TV / Android TV Integration

- **Primary Protocol:** **Android TV Remote Protocol v2** (`_androidtvremote2._tcp.local.`).
  - **Pairing (TLS Port 6467):** Mutual TLS handshake using generated client X.509 certificate and TV server certificate; 6-digit hexadecimal PIN displayed on TV screen is hashed with both TLS public keys (`SHA-256(client_modulus || client_exponent || server_modulus || server_exponent || pin_bytes)`) to prove physical proximity!
  - **Operational Channel (TLS Port 6466):** Length-delimited Protobuf messages supporting `RemoteKeyInject` (`DPAD_UP`, `DPAD_DOWN`, `DPAD_LEFT`, `DPAD_RIGHT`, `DPAD_CENTER`, `BACK`, `HOME`, `VOLUME_UP`, `VOLUME_DOWN`, `VOLUME_MUTE`, `MEDIA_PLAY_PAUSE`, `MEDIA_NEXT`, `MEDIA_PREVIOUS`, `POWER`), `RemoteAppLinkLaunchRequest` (deep-linking Netflix, YouTube, Prime Video, Plex, etc.), volume level updates, and active app package telemetry.
- **Wake-on-LAN:** Magic packet (`FF:FF:FF:FF:FF:FF` + 16× MAC) for waking sleeping TVs when MAC is known.

---

## 6. Smart-Home Backbone: Home Assistant & Matter

- **Home Assistant Integration:**
  - Connects via Long-Lived Access Token (stored AES-256-GCM encrypted) over REST (`/api/config`, `/api/states`, `/api/services`) and persistent WebSocket (`/api/websocket`) for real-time `state_changed` event streaming and device/entity/area registry discovery.
  - Maps `light`, `switch`, `media_player` (smart speakers & TVs), `climate`, `fan`, `cover`, `vacuum`, `scene`, `sensor`, and `lock` (gated behind `smarthome:sensitive` + mandatory user confirmation).
- **Matter Integration:**
  - Discovers commissionable (`_matterc._udp.local.`) and operational (`_matter._tcp.local.`) Matter nodes on LAN via mDNS, and integrates with both Home Assistant's Matter server entities and the standard `python-matter-server` WebSocket JSON-RPC schema (`commission_with_code`, `get_nodes`, `device_command`).

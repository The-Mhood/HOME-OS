import AppIntents
import CryptoKit
import Foundation
#if canImport(HomeKit)
import HomeKit
#endif
#if canImport(MediaPlayer)
import MediaPlayer
#endif
#if canImport(UIKit)
import UIKit
#endif

/// Honest, sandbox-compliant iOS capability provider.
/// Never attempts to circumvent Apple iOS sandbox restrictions; documents unavailable capabilities transparently.
public final class IosCapabilitiesManager: NSObject {

    public private(set) var securityScopedFolderBookmark: Data?

    public func saveAuthorizedFolderBookmark(url: URL) throws {
        guard url.startAccessingSecurityScopedResource() else {
            throw NSError(domain: "HomeOsIos", code: 403, userInfo: [NSLocalizedDescriptionKey: "Security-scoped access denied"])
        }
        defer { url.stopAccessingSecurityScopedResource() }
        self.securityScopedFolderBookmark = try url.bookmarkData(options: .minimalBookmark, includingResourceValuesForKeys: nil, relativeTo: nil)
    }

    public func buildCapabilitiesManifest() -> [[String: Any]] {
        return [
            [
                "capability_key": "telemetry.status",
                "category": "telemetry",
                "display_name": "Battery, Thermal & Storage Status",
                "supported": true,
                "requires_permission": "telemetry:read",
                "requires_confirmation": false,
            ],
            [
                "capability_key": "shortcuts.run",
                "category": "automation",
                "display_name": "Run Approved Apple Shortcut / AppIntent",
                "supported": true,
                "requires_permission": "apps:launch",
                "requires_confirmation": false,
            ],
            [
                "capability_key": "files.browse",
                "category": "files",
                "display_name": "User-Selected Security-Scoped Folder",
                "supported": securityScopedFolderBookmark != nil,
                "unavailable_reason": securityScopedFolderBookmark != nil ? NSNull() : "Select a folder in the HOME OS iOS app using the Document Picker first.",
                "requires_permission": "files:browse",
                "requires_confirmation": false,
            ],
            [
                "capability_key": "media.control",
                "category": "media",
                "display_name": "System Music Player Controls",
                "supported": true,
                "requires_permission": "media:control",
                "requires_confirmation": false,
            ],
            [
                "capability_key": "homekit.control",
                "category": "smarthome",
                "display_name": "Authorized HomeKit & Matter Accessories",
                "supported": true,
                "requires_permission": "smarthome:control",
                "requires_confirmation": false,
            ],
            // Explicitly documented iOS platform restrictions:
            [
                "capability_key": "notifications.sync",
                "category": "communications",
                "display_name": "System-Wide Notification Sync",
                "supported": false,
                "unavailable_reason": "Unsupported on iOS: Apple restricts global notification reading (ANCS) to Bluetooth LE peripherals.",
                "requires_permission": "notifications:read",
                "requires_confirmation": false,
            ],
            [
                "capability_key": "sms.send",
                "category": "communications",
                "display_name": "Background SMS / WhatsApp Database Access",
                "supported": false,
                "unavailable_reason": "Unsupported on iOS: Apple sandbox strictly isolates Messages and third-party messaging databases.",
                "requires_permission": "sms:send",
                "requires_confirmation": true,
            ],
            [
                "capability_key": "calls.log",
                "category": "communications",
                "display_name": "Call History Synchronization",
                "supported": false,
                "unavailable_reason": "Unsupported on iOS: Apple provides no public API for reading device call history.",
                "requires_permission": "calls:read",
                "requires_confirmation": false,
            ],
            [
                "capability_key": "input.remote",
                "category": "input",
                "display_name": "System-Wide Remote Touch Injection",
                "supported": false,
                "unavailable_reason": "Unsupported on iOS: Apple prohibits cross-application remote touch synthesis.",
                "requires_permission": "input:remote",
                "requires_confirmation": true,
            ],
        ]
    }

    public func collectTelemetry() -> [String: Any] {
        var batteryPct = 100
        var charging = false
        #if canImport(UIKit)
        UIDevice.current.isBatteryMonitoringEnabled = true
        if UIDevice.current.batteryLevel >= 0 {
            batteryPct = Int(UIDevice.current.batteryLevel * 100)
        }
        charging = UIDevice.current.batteryState == .charging || UIDevice.current.batteryState == .full
        #endif

        return [
            "battery_level": batteryPct,
            "is_charging": charging,
            "health_score": 100,
            "attributes": [
                "thermal_state": ProcessInfo.processInfo.thermalState.rawValue,
                "processor_count": ProcessInfo.processInfo.processorCount,
            ],
            "capabilities": buildCapabilitiesManifest(),
        ]
    }

    public func runShortcut(named shortcutName: String) {
        #if canImport(UIKit)
        let encoded = shortcutName.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? shortcutName
        if let url = URL(string: "shortcuts://run-shortcut?name=\(encoded)") {
            DispatchQueue.main.async {
                UIApplication.shared.open(url)
            }
        }
        #endif
    }

    public func controlSystemMedia(action: String) {
        #if canImport(MediaPlayer)
        let player = MPMusicPlayerController.systemMusicPlayer
        switch action.lowercased() {
        case "play": player.play()
        case "pause": player.pause()
        case "next": player.skipToNextItem()
        case "previous": player.skipToPreviousItem()
        default: break
        }
        #endif
    }
}

public struct TriggerHomeOsSceneIntent: AppIntent {
    public static var title: LocalizedStringResource = "Trigger HOME OS Automation"
    @Parameter(title: "Rule ID")
    public var ruleId: String

    public init() {}

    public func perform() async throws -> some IntentResult {
        return .result()
    }
}

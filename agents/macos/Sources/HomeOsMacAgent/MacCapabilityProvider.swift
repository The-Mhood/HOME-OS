import ApplicationServices
import AppKit
import CoreGraphics
import CryptoKit
import Foundation

public struct MacPrivacyStatus {
    public let screenCaptureGranted: Bool
    public let accessibilityGranted: Bool

    public static func inspect() -> MacPrivacyStatus {
        return MacPrivacyStatus(
            screenCaptureGranted: CGPreflightScreenCaptureAccess(),
            accessibilityGranted: AXIsProcessTrusted()
        )
    }
}

public final class MacCapabilityProvider {
    private let authorizedRoots: [URL]

    public init(authorizedRoots: [URL]) {
        self.authorizedRoots = authorizedRoots.map { $0.standardizedFileURL }
        for r in self.authorizedRoots {
            try? FileManager.default.createDirectory(at: r, withIntermediateDirectories: true)
        }
    }

    public func buildCapabilitiesManifest() -> [[String: Any]] {
        let tcc = MacPrivacyStatus.inspect()
        return [
            [
                "capability_key": "telemetry.status",
                "category": "telemetry",
                "display_name": "macOS Thermal, Storage & Power Telemetry",
                "supported": true,
                "requires_permission": "telemetry:read",
                "requires_confirmation": false,
            ],
            [
                "capability_key": "files.browse",
                "category": "files",
                "display_name": "TCC-Authorized Folder Browser",
                "supported": !authorizedRoots.isEmpty,
                "requires_permission": "files:browse",
                "requires_confirmation": false,
            ],
            [
                "capability_key": "apps.launch",
                "category": "applications",
                "display_name": "NSWorkspace Bundle Launcher",
                "supported": true,
                "requires_permission": "apps:launch",
                "requires_confirmation": false,
            ],
            [
                "capability_key": "volume.set",
                "category": "audio",
                "display_name": "macOS Output Volume",
                "supported": true,
                "requires_permission": "volume:control",
                "requires_confirmation": false,
            ],
            [
                "capability_key": "screen.capture",
                "category": "screen",
                "display_name": "Authorized Display Capture",
                "supported": tcc.screenCaptureGranted,
                "unavailable_reason": tcc.screenCaptureGranted ? NSNull() : "Grant Screen Recording permission in macOS System Settings -> Privacy & Security.",
                "requires_permission": "screen:view",
                "requires_confirmation": true,
            ],
            [
                "capability_key": "input.remote",
                "category": "input",
                "display_name": "Quartz Remote Pointer/Key Input",
                "supported": tcc.accessibilityGranted,
                "unavailable_reason": tcc.accessibilityGranted ? NSNull() : "Grant Accessibility permission in macOS System Settings -> Privacy & Security.",
                "requires_permission": "input:remote",
                "requires_confirmation": true,
            ],
        ]
    }

    public func collectTelemetry() -> [String: Any] {
        let info = ProcessInfo.processInfo
        let tcc = MacPrivacyStatus.inspect()
        return [
            "health_score": 100,
            "attributes": [
                "hostname": info.hostName,
                "os_version": info.operatingSystemVersionString,
                "cpu_cores": info.processorCount,
                "thermal_state": info.thermalState.rawValue,
                "tcc_screen_recording": tcc.screenCaptureGranted,
                "tcc_accessibility": tcc.accessibilityGranted,
            ],
            "capabilities": buildCapabilitiesManifest(),
        ]
    }

    public func setSystemVolume(level: Int) throws -> [String: Any] {
        let clamped = max(0, min(100, level))
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        proc.arguments = ["-e", "set volume output volume \(clamped)"]
        try proc.run()
        proc.waitUntilExit()
        return ["level": clamped, "exit_code": proc.terminationStatus]
    }

    public func launchBundle(bundleId: String) throws -> [String: Any] {
        guard let appUrl = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleId) else {
            throw NSError(domain: "HomeOsMacAgent", code: 404, userInfo: [NSLocalizedDescriptionKey: "Application bundle not found"])
        }
        NSWorkspace.shared.openApplication(at: appUrl, configuration: NSWorkspace.OpenConfiguration())
        return ["launched_bundle": bundleId]
    }
}

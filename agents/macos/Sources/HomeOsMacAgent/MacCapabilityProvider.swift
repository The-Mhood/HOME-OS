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
        var caps: [[String: Any]] = [
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
                "capability_key": "files.transfer",
                "category": "files",
                "display_name": "SHA-256 Verified File Transfer",
                "supported": !authorizedRoots.isEmpty,
                "requires_permission": "files:transfer",
                "requires_confirmation": false,
            ],
            [
                "capability_key": "apps.launch",
                "category": "applications",
                "display_name": "NSWorkspace Bundle Launcher",
                "supported": true,
                "requires_permission": "apps:launch",
                "requires_confirmation": true,
            ],
            [
                "capability_key": "volume.set",
                "category": "audio",
                "display_name": "macOS Output Volume",
                "supported": true,
                "requires_permission": "volume:control",
                "requires_confirmation": false,
            ],
        ]

        var screenCap: [String: Any] = [
            "capability_key": "screen.capture",
            "category": "screen",
            "display_name": "Authorized Display Capture",
            "supported": tcc.screenCaptureGranted,
            "requires_permission": "screen:view",
            "requires_confirmation": true,
        ]
        if !tcc.screenCaptureGranted {
            screenCap["unavailable_reason"] = "Grant Screen Recording permission in macOS System Settings -> Privacy & Security."
        }
        caps.append(screenCap)

        var inputCap: [String: Any] = [
            "capability_key": "input.remote",
            "category": "input",
            "display_name": "Quartz Remote Pointer/Key Input",
            "supported": tcc.accessibilityGranted,
            "requires_permission": "input:control",
            "requires_confirmation": true,
        ]
        if !tcc.accessibilityGranted {
            inputCap["unavailable_reason"] = "Grant Accessibility permission in macOS System Settings -> Privacy & Security."
        }
        caps.append(inputCap)

        return caps
    }

    private func readMacBattery() -> (level: Int?, charging: Bool?) {
        let proc = Process()
        let pipe = Pipe()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/pmset")
        proc.arguments = ["-g", "batt"]
        proc.standardOutput = pipe
        do {
            try proc.run()
            proc.waitUntilExit()
            let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            if let range = out.range(of: #"\d+%"#, options: .regularExpression) {
                let pctStr = out[range].dropLast()
                let pct = Int(pctStr)
                let isCharging = out.contains("AC Power") || out.contains("; charging") || out.contains("; charged")
                return (pct, isCharging)
            }
        } catch {}
        return (nil, nil)
    }

    public func collectTelemetry() -> [String: Any] {
        let info = ProcessInfo.processInfo
        let tcc = MacPrivacyStatus.inspect()
        let batt = readMacBattery()
        var payload: [String: Any] = [
            "health_score": 100,
            "attributes": [
                "hostname": info.hostName,
                "os_version": info.operatingSystemVersionString,
                "cpu_cores": info.processorCount,
                "physical_memory_gb": Int(info.physicalMemory / (1024 * 1024 * 1024)),
                "thermal_state": info.thermalState.rawValue,
                "tcc_screen_recording": tcc.screenCaptureGranted,
                "tcc_accessibility": tcc.accessibilityGranted,
                "authorized_roots": authorizedRoots.map { $0.path },
            ],
            "capabilities": buildCapabilitiesManifest(),
        ]
        if let lvl = batt.level {
            payload["battery_level"] = lvl
        }
        if let chg = batt.charging {
            payload["is_charging"] = chg
        }
        return payload
    }

    private func resolveJailedURL(_ rawPath: String) throws -> URL {
        guard let baseRoot = authorizedRoots.first else {
            throw NSError(domain: "HomeOsMacAgent", code: 403, userInfo: [NSLocalizedDescriptionKey: "No authorized roots configured"])
        }
        let candidate: URL
        if rawPath.isEmpty {
            candidate = baseRoot
        } else if rawPath.hasPrefix("/") {
            candidate = URL(fileURLWithPath: rawPath).standardizedFileURL.resolvingSymlinksInPath()
        } else {
            candidate = baseRoot.appendingPathComponent(rawPath).standardizedFileURL.resolvingSymlinksInPath()
        }

        for root in authorizedRoots {
            let rootPath = root.resolvingSymlinksInPath().path
            let candPath = candidate.path
            if candPath == rootPath || candPath.hasPrefix(rootPath + "/") {
                return candidate
            }
        }
        throw NSError(domain: "HomeOsMacAgent", code: 403, userInfo: [NSLocalizedDescriptionKey: "Path '\(rawPath)' is outside authorized roots"])
    }

    public func browseFiles(parameters: [String: Any]) throws -> [String: Any] {
        let action = parameters["action"] as? String ?? "list"
        let rawPath = parameters["path"] as? String ?? ""
        let target = try resolveJailedURL(rawPath)
        let fm = FileManager.default

        if action == "read_file" {
            let data = try Data(contentsOf: target)
            let sha = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
            return [
                "file_name": target.lastPathComponent,
                "size": data.count,
                "sha256": sha,
                "content_b64": data.base64EncodedString(),
            ]
        }

        let items = try fm.contentsOfDirectory(at: target, includingPropertiesForKeys: [.isDirectoryKey, .fileSizeKey, .contentModificationDateKey], options: [.skipsHiddenFiles])
        var entries: [[String: Any]] = []
        for child in items.sorted(by: { $0.lastPathComponent.lowercased() < $1.lastPathComponent.lowercased() }) {
            guard let jailedChild = try? resolveJailedURL(child.path) else { continue }
            let vals = try? jailedChild.resourceValues(forKeys: [.isDirectoryKey, .fileSizeKey, .contentModificationDateKey])
            let isDir = vals?.isDirectory ?? false
            entries.append([
                "name": jailedChild.lastPathComponent,
                "path": jailedChild.path,
                "is_dir": isDir,
                "size": isDir ? 0 : (vals?.fileSize ?? 0),
                "modified_at": Int((vals?.contentModificationDate ?? Date()).timeIntervalSince1970),
            ])
        }
        return [
            "current_path": target.path,
            "authorized_roots": authorizedRoots.map { $0.path },
            "entries": entries,
        ]
    }

    public func transferFile(parameters: [String: Any]) throws -> [String: Any] {
        let targetDirStr = parameters["target_dir"] as? String ?? ""
        let targetDir = try resolveJailedURL(targetDirStr)
        try FileManager.default.createDirectory(at: targetDir, withIntermediateDirectories: true)

        let rawName = (parameters["file_name"] as? String ?? "transfer.bin")
        let safeName = URL(fileURLWithPath: rawName).lastPathComponent
        let dest = try resolveJailedURL(targetDir.appendingPathComponent(safeName).path)

        guard let b64 = parameters["content_b64"] as? String,
              let raw = Data(base64Encoded: b64) else {
            throw NSError(domain: "HomeOsMacAgent", code: 400, userInfo: [NSLocalizedDescriptionKey: "Invalid base64 payload"])
        }
        let actualSha = SHA256.hash(data: raw).map { String(format: "%02x", $0) }.joined()
        if let expected = parameters["expected_sha256"] as? String, !expected.isEmpty, expected.lowercased() != actualSha {
            throw NSError(domain: "HomeOsMacAgent", code: 422, userInfo: [NSLocalizedDescriptionKey: "SHA-256 checksum mismatch"])
        }
        try raw.write(to: dest, options: .atomic)
        return [
            "written_path": dest.path,
            "size": raw.count,
            "sha256": actualSha,
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
            throw NSError(domain: "HomeOsMacAgent", code: 404, userInfo: [NSLocalizedDescriptionKey: "Application bundle '\(bundleId)' not found"])
        }
        NSWorkspace.shared.openApplication(at: appUrl, configuration: NSWorkspace.OpenConfiguration())
        return ["launched_bundle": bundleId]
    }
}

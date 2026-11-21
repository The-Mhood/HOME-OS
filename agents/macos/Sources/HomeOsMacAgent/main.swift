import CryptoKit
import Foundation

@main
struct HomeOsMacAgentMain {
    private static func argValue(_ flag: String, in args: [String]) -> String? {
        guard let idx = args.firstIndex(of: flag), idx + 1 < args.count else { return nil }
        return args[idx + 1]
    }

    private static func hardwareFingerprint() -> String {
        let host = ProcessInfo.processInfo.hostName
        let digest = SHA256.hash(data: Data("macos-\(host)".utf8)).map { String(format: "%02x", $0) }.joined()
        return "macos-\(digest.prefix(24))"
    }

    private static func printUsage() {
        print("""
        HOME OS Native macOS Agent v1.0.0

        Usage:
          homeos-mac-agent pair --hub <http://127.0.0.1:8420> --session <SESSION_ID> --pin <6_DIGIT_PIN> [--name "My Mac"] [--share-dir ~/HomeOS-Shared]
          homeos-mac-agent pair --qr "<homeos://pair?...>" [--name "My Mac"]
          homeos-mac-agent run [--share-dir ~/HomeOS-Shared]
          homeos-mac-agent status
        """)
    }

    static func main() async {
        let args = Array(CommandLine.arguments.dropFirst())
        guard let subcmd = args.first else {
            printUsage()
            return
        }

        let defaultShared = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("HomeOS-Shared")
        let sharePath = argValue("--share-dir", in: args)
        let sharedDir = sharePath.map { URL(fileURLWithPath: ($0 as NSString).expandingTildeInPath) } ?? defaultShared
        let provider = MacCapabilityProvider(authorizedRoots: [sharedDir])

        if subcmd == "status" {
            if let creds = KeychainVault.load() {
                print("Enrolled: YES")
                print("  Device ID:   \(creds.deviceId)")
                print("  Hub URL:     \(creds.hubHttpUrl)")
                print("  Key Version: v\(creds.keyVersion)")
                print("  Shared Dir:  \(sharedDir.path)")
            } else {
                print("Enrolled: NO (run 'homeos-mac-agent pair ...' to enroll)")
            }
            return
        }

        if subcmd == "pair" || subcmd == "enroll" {
            var hubUrl = argValue("--hub", in: args) ?? "http://127.0.0.1:8420"
            var sessionId = argValue("--session", in: args) ?? ""
            var pinOrToken = argValue("--pin", in: args) ?? argValue("--code", in: args) ?? ""
            let customName = argValue("--name", in: args)

            if let qr = argValue("--qr", in: args), let comps = URLComponents(string: qr) {
                let items = comps.queryItems ?? []
                if let qSession = items.first(where: { $0.name == "session_id" })?.value { sessionId = qSession }
                if let qToken = items.first(where: { $0.name == "token" || $0.name == "pin" })?.value { pinOrToken = qToken }
                if let qHub = items.first(where: { $0.name == "hub" })?.value { hubUrl = qHub }
            }

            if sessionId.isEmpty || pinOrToken.isEmpty {
                print("Error: --session <SESSION_ID> and --pin <6_DIGIT_PIN> (or --qr <URI>) are required.")
                printUsage()
                exit(1)
            }

            let trimmedHub = hubUrl.hasSuffix("/") ? String(hubUrl.dropLast()) : hubUrl
            guard let endpoint = URL(string: "\(trimmedHub)/api/v1/pairing/complete") else {
                print("Error: Invalid Hub URL \(trimmedHub)")
                exit(1)
            }

            let info = ProcessInfo.processInfo
            let bodyObj: [String: Any] = [
                "pairing_session_id": sessionId,
                "pin_or_token": pinOrToken,
                "hardware_fingerprint": hardwareFingerprint(),
                "name": customName ?? "\(info.hostName) (macOS)",
                "category": "laptop",
                "manufacturer": "Apple",
                "model": "Mac",
                "os_name": "macOS",
                "os_version": info.operatingSystemVersionString,
                "capabilities": provider.buildCapabilitiesManifest(),
                "metadata": ["agent_version": "1.0.0", "shared_root": sharedDir.path],
            ]

            do {
                var req = URLRequest(url: endpoint)
                req.httpMethod = "POST"
                req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                req.httpBody = try JSONSerialization.data(withJSONObject: bodyObj)

                let (respData, resp) = try await URLSession.shared.data(for: req)
                guard let httpResp = resp as? HTTPURLResponse, (200..<300).contains(httpResp.statusCode) else {
                    let msg = String(data: respData, encoding: .utf8) ?? "HTTP error"
                    print("Pairing failed: \(msg)")
                    exit(1)
                }

                guard let json = try JSONSerialization.jsonObject(with: respData) as? [String: Any],
                      let credsDict = json["credentials"] as? [String: Any],
                      let devId = credsDict["device_id"] as? String,
                      let devToken = credsDict["device_token"] as? String,
                      let hmacSec = credsDict["hmac_secret"] as? String else {
                    print("Pairing response missing credentials payload")
                    exit(1)
                }
                let keyVer = credsDict["key_version"] as? Int ?? 1
                let enrollment = MacEnrollment(
                    hubHttpUrl: trimmedHub,
                    deviceId: devId,
                    deviceToken: devToken,
                    hmacSecret: hmacSec,
                    keyVersion: keyVer
                )
                try KeychainVault.save(enrollment)
                print("Successfully enrolled macOS Agent in Keychain! Device ID: \(devId)")
                print("Start live connection with: homeos-mac-agent run")
            } catch {
                print("Enrollment error: \(error.localizedDescription)")
                exit(1)
            }
            return
        }

        guard subcmd == "run" else {
            printUsage()
            return
        }

        guard let creds = KeychainVault.load() else {
            print("HOME OS macOS Agent: No Keychain enrollment found. Run 'homeos-mac-agent pair ...' first.")
            exit(1)
        }

        print("HOME OS macOS Agent connecting to \(creds.hubHttpUrl) (Device ID: \(creds.deviceId))...")
        print("Authorized shared directory: \(sharedDir.path)")

        while true {
            let ts = Int64(Date().timeIntervalSince1970)
            let nonce = UUID().uuidString.replacingOccurrences(of: "-", with: "")
            let sig = KeychainVault.signRequest(
                secret: creds.hmacSecret,
                method: "WS",
                path: "/api/v1/agent/ws",
                timestamp: ts,
                nonce: nonce
            )

            let wsBase = creds.hubHttpUrl
                .replacingOccurrences(of: "https://", with: "wss://")
                .replacingOccurrences(of: "http://", with: "ws://")

            var comps = URLComponents(string: "\(wsBase)/api/v1/agent/ws")
            comps?.queryItems = [
                URLQueryItem(name: "device_id", value: creds.deviceId),
                URLQueryItem(name: "token", value: creds.deviceToken),
                URLQueryItem(name: "timestamp", value: String(ts)),
                URLQueryItem(name: "nonce", value: nonce),
                URLQueryItem(name: "signature", value: sig),
            ]
            guard let url = comps?.url else { return }

            let task = URLSession.shared.webSocketTask(with: url)
            task.resume()

            // Periodic telemetry sender task
            let telemetryLoop = Task {
                while !Task.isCancelled {
                    if let tData = try? JSONSerialization.data(withJSONObject: ["type": "telemetry", "payload": provider.collectTelemetry()]),
                       let tStr = String(data: tData, encoding: .utf8) {
                        try? await task.send(.string(tStr))
                    }
                    try? await Task.sleep(nanoseconds: 10_000_000_000)
                }
            }

            while true {
                guard let message = try? await task.receive() else { break }
                if case let .string(text) = message,
                   let data = text.data(using: .utf8),
                   let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   obj["type"] as? String == "command.execute",
                   let cmdId = obj["command_id"] as? String,
                   let cap = obj["capability_key"] as? String {
                    let params = obj["parameters"] as? [String: Any] ?? [:]
                    var receipt: [String: Any] = [
                        "type": "command.receipt",
                        "command_id": cmdId,
                        "acknowledged": true,
                        "state_verified": true,
                        "verification_method": "macos_native_api",
                    ]
                    do {
                        switch cap {
                        case "telemetry.status":
                            receipt["result"] = provider.collectTelemetry()
                        case "files.browse":
                            receipt["result"] = try provider.browseFiles(parameters: params)
                        case "files.transfer":
                            receipt["result"] = try provider.transferFile(parameters: params)
                        case "volume.set":
                            receipt["result"] = try provider.setSystemVolume(level: params["level"] as? Int ?? 50)
                        case "apps.launch":
                            let bundleId = (params["bundle_id"] as? String) ?? (params["app_id"] as? String) ?? ""
                            receipt["result"] = try provider.launchBundle(bundleId: bundleId)
                        default:
                            receipt["acknowledged"] = false
                            receipt["state_verified"] = false
                            receipt["error_code"] = "UNSUPPORTED_MAC_COMMAND"
                            receipt["error_message"] = "Capability '\(cap)' is not enabled on this macOS host."
                        }
                    } catch {
                        receipt["acknowledged"] = false
                        receipt["state_verified"] = false
                        receipt["error_code"] = "EXECUTION_ERROR"
                        receipt["error_message"] = error.localizedDescription
                    }
                    if let respData = try? JSONSerialization.data(withJSONObject: receipt),
                       let respStr = String(data: respData, encoding: .utf8) {
                        try? await task.send(.string(respStr))
                    }
                }
            }

            telemetryLoop.cancel()
            task.cancel(with: .goingAway, reason: nil)
            print("Connection lost; reconnecting in 3s...")
            try? await Task.sleep(nanoseconds: 3_000_000_000)
        }
    }
}

import Foundation

@main
struct HomeOsMacAgentMain {
    static func main() async {
        let sharedDir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("HomeOS-Shared")
        let provider = MacCapabilityProvider(authorizedRoots: [sharedDir])

        guard let creds = KeychainVault.load() else {
            print("HOME OS macOS Agent: No Keychain enrollment found. Run pairing first.")
            return
        }

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
        guard let url = URL(string: "\(wsBase)/api/v1/agent/ws?device_id=\(creds.deviceId)&token=\(creds.deviceToken)&timestamp=\(ts)&nonce=\(nonce)&signature=\(sig)") else {
            return
        }

        let task = URLSession.shared.webSocketTask(with: url)
        task.resume()

        if let initialData = try? JSONSerialization.data(withJSONObject: ["type": "telemetry", "payload": provider.collectTelemetry()]),
           let initialText = String(data: initialData, encoding: .utf8) {
            try? await task.send(.string(initialText))
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
                    case "volume.set":
                        receipt["result"] = try provider.setSystemVolume(level: params["level"] as? Int ?? 50)
                    case "apps.launch":
                        receipt["result"] = try provider.launchBundle(bundleId: params["bundle_id"] as? String ?? "")
                    default:
                        receipt["acknowledged"] = false
                        receipt["state_verified"] = false
                        receipt["error_code"] = "UNSUPPORTED_MAC_COMMAND"
                    }
                } catch {
                    receipt["acknowledged"] = false
                    receipt["state_verified"] = false
                    receipt["error_message"] = error.localizedDescription
                }
                if let respData = try? JSONSerialization.data(withJSONObject: receipt),
                   let respStr = String(data: respData, encoding: .utf8) {
                    try? await task.send(.string(respStr))
                }
            }
        }
    }
}

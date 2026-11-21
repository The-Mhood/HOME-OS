import CryptoKit
import Foundation
import Security

public struct MacEnrollment: Codable {
    public let hubHttpUrl: String
    public let deviceId: String
    public let deviceToken: String
    public let hmacSecret: String
    public let keyVersion: Int
}

public enum KeychainVault {
    private static let service = "com.homeos.macagent.vault"
    private static let account = "enrollment"

    public static func save(_ enrollment: MacEnrollment) throws {
        let data = try JSONEncoder().encode(enrollment)
        let deleteQuery: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(deleteQuery as CFDictionary)

        let addQuery: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            kSecValueData as String: data,
        ]
        let status = SecItemAdd(addQuery as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
        }
    }

    public static func load() -> MacEnrollment? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data else { return nil }
        return try? JSONDecoder().decode(MacEnrollment.self, from: data)
    }

    public static func signRequest(secret: String, method: String, path: String, timestamp: Int64, nonce: String, body: Data = Data()) -> String {
        let bodySha = SHA256.hash(data: body).map { String(format: "%02x", $0) }.joined()
        let canonical = "\(method.uppercased())|\(path)|\(timestamp)|\(nonce)|\(bodySha)"
        let key = SymmetricKey(data: Data(secret.utf8))
        let mac = HMAC<SHA256>.authenticationCode(for: Data(canonical.utf8), using: key)
        return mac.map { String(format: "%02x", $0) }.joined()
    }
}

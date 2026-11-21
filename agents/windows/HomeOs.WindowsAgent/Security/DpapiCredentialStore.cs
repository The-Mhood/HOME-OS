using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace HomeOs.WindowsAgent.Security;

public sealed record AgentEnrollment(
    string HubHttpUrl,
    string DeviceId,
    string DeviceToken,
    string HmacSecret,
    int KeyVersion
);

public sealed class DpapiCredentialStore
{
    private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("HOME-OS-WINDOWS-AGENT-V1");
    private readonly string _filePath;

    public DpapiCredentialStore(string stateDirectory)
    {
        Directory.CreateDirectory(stateDirectory);
        _filePath = Path.Combine(stateDirectory, "enrollment.dpapi");
    }

    public void Save(AgentEnrollment enrollment)
    {
        byte[] plain = JsonSerializer.SerializeToUtf8Bytes(enrollment);
        byte[] protectedBytes = ProtectedData.Protect(plain, Entropy, DataProtectionScope.CurrentUser);
        File.WriteAllBytes(_filePath, protectedBytes);
    }

    public AgentEnrollment? Load()
    {
        if (!File.Exists(_filePath)) return null;
        byte[] protectedBytes = File.ReadAllBytes(_filePath);
        byte[] plain = ProtectedData.Unprotect(protectedBytes, Entropy, DataProtectionScope.CurrentUser);
        return JsonSerializer.Deserialize<AgentEnrollment>(plain);
    }

    public static string SignRequest(string secret, string method, string path, long timestamp, string nonce, byte[] body)
    {
        string bodySha = Convert.ToHexString(SHA256.HashData(body)).ToLowerInvariant();
        string canonical = $"{method.ToUpperInvariant()}|{path}|{timestamp}|{nonce}|{bodySha}";
        using var hmac = new HMACSHA256(Encoding.UTF8.GetBytes(secret));
        byte[] sig = hmac.ComputeHash(Encoding.UTF8.GetBytes(canonical));
        return Convert.ToHexString(sig).ToLowerInvariant();
    }

    public static bool VerifyUpdateSha256(string installerPath, string expectedSha256)
    {
        using var stream = File.OpenRead(installerPath);
        string actual = Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
        return CryptographicOperations.FixedTimeEquals(
            Encoding.ASCII.GetBytes(actual),
            Encoding.ASCII.GetBytes(expectedSha256.Trim().ToLowerInvariant())
        );
    }
}

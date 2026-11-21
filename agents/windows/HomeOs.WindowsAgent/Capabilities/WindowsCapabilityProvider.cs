using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Windows.Forms;

namespace HomeOs.WindowsAgent.Capabilities;

public sealed class WindowsCapabilityProvider
{
    private readonly List<string> _authorizedRoots;
    private readonly Dictionary<string, string> _allowedApps;

    public WindowsCapabilityProvider(IEnumerable<string> authorizedRoots)
    {
        _authorizedRoots = authorizedRoots.Select(Path.GetFullPath).ToList();
        foreach (var root in _authorizedRoots)
        {
            Directory.CreateDirectory(root);
        }
        _allowedApps = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["notepad"] = "notepad.exe",
            ["explorer"] = "explorer.exe",
            ["calc"] = "calc.exe"
        };
    }

    [DllImport("user32.dll")]
    private static extern IntPtr OpenInputDesktop(uint dwFlags, bool fInherit, uint dwDesiredAccess);

    [DllImport("user32.dll")]
    private static extern bool CloseDesktop(IntPtr hDesktop);

    private static bool IsInteractiveDesktopAccessible()
    {
        IntPtr desk = OpenInputDesktop(0, false, 0x0100);
        if (desk == IntPtr.Zero) return false;
        CloseDesktop(desk);
        return true;
    }

    public object CollectTelemetry()
    {
        var power = SystemInformation.PowerStatus;
        int? batteryPct = power.BatteryChargeStatus == BatteryChargeStatus.NoSystemBattery
            ? null
            : (int)Math.Round(power.BatteryLifePercent * 100);

        var sysDrive = new DriveInfo(Path.GetPathRoot(Environment.SystemDirectory) ?? "C:\\");
        double totalGb = Math.Round(sysDrive.TotalSize / (1024.0 * 1024.0 * 1024.0), 1);
        double freeGb = Math.Round(sysDrive.AvailableFreeSpace / (1024.0 * 1024.0 * 1024.0), 1);

        return new
        {
            battery_level = batteryPct,
            is_charging = power.PowerLineStatus == PowerLineStatus.Online,
            health_score = 100,
            attributes = new
            {
                hostname = Environment.MachineName,
                os_version = Environment.OSVersion.VersionString,
                cpu_cores = Environment.ProcessorCount,
                disk_total_gb = totalGb,
                disk_free_gb = freeGb,
                interactive_desktop = IsInteractiveDesktopAccessible(),
                uac_boundary_note = "UIPI blocks input injection into elevated UAC prompts or Secure Desktop."
            }
        };
    }

    private string ResolveJailedPath(string requestedPath)
    {
        if (_authorizedRoots.Count == 0)
            throw new UnauthorizedAccessException("No authorized roots configured on Windows agent.");

        string candidate = string.IsNullOrWhiteSpace(requestedPath)
            ? _authorizedRoots[0]
            : Path.GetFullPath(Path.IsPathRooted(requestedPath) ? requestedPath : Path.Combine(_authorizedRoots[0], requestedPath));

        foreach (var root in _authorizedRoots)
        {
            string normalizedRoot = root.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            if (candidate.Equals(root, StringComparison.OrdinalIgnoreCase) ||
                candidate.StartsWith(normalizedRoot, StringComparison.OrdinalIgnoreCase))
            {
                return candidate;
            }
        }
        throw new UnauthorizedAccessException($"Path '{requestedPath}' is outside authorized roots.");
    }

    public object BrowseDirectory(string path)
    {
        string target = ResolveJailedPath(path);
        var entries = new DirectoryInfo(target).GetFileSystemInfos()
            .Select(info => new
            {
                name = info.Name,
                path = info.FullName,
                is_dir = (info.Attributes & FileAttributes.Directory) != 0,
                size = info is FileInfo fi ? fi.Length : 0L
            })
            .ToList();

        return new { current_path = target, entries };
    }

    public object WriteVerifiedFile(string targetDir, string fileName, string contentB64, string expectedSha256)
    {
        string dir = ResolveJailedPath(targetDir);
        string safeName = Path.GetFileName(fileName);
        string dest = ResolveJailedPath(Path.Combine(dir, safeName));
        byte[] raw = Convert.FromBase64String(contentB64);
        string actualSha = Convert.ToHexString(SHA256.HashData(raw)).ToLowerInvariant();
        if (!string.IsNullOrEmpty(expectedSha256) && !actualSha.Equals(expectedSha256, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException("SHA-256 checksum mismatch on incoming file transfer.");
        }
        File.WriteAllBytes(dest, raw);
        return new { written_path = dest, size = raw.Length, sha256 = actualSha };
    }

    public object CaptureAuthorizedScreenJpeg()
    {
        if (!IsInteractiveDesktopAccessible())
            throw new InvalidOperationException("Interactive desktop unavailable (locked or UAC Secure Desktop active).");

        Rectangle bounds = Screen.PrimaryScreen?.Bounds ?? new Rectangle(0, 0, 1280, 720);
        using var bitmap = new Bitmap(bounds.Width, bounds.Height);
        using (var g = Graphics.FromImage(bitmap))
        {
            g.CopyFromScreen(Point.Empty, Point.Empty, bounds.Size);
        }
        using var ms = new MemoryStream();
        bitmap.Save(ms, ImageFormat.Jpeg);
        return new { mime_type = "image/jpeg", frame_b64 = Convert.ToBase64String(ms.ToArray()) };
    }

    public object ListUserProcesses(int limit = 30)
    {
        var list = Process.GetProcesses()
            .Take(Math.Clamp(limit, 1, 100))
            .Select(p => new { pid = p.Id, name = p.ProcessName })
            .ToList();
        return new { count = list.Count, processes = list };
    }

    public object LaunchAllowedApp(string appId)
    {
        if (!_allowedApps.TryGetValue(appId, out var exe))
            throw new UnauthorizedAccessException($"Application '{appId}' is not in Windows agent allowlist.");
        var proc = Process.Start(new ProcessStartInfo(exe) { UseShellExecute = true });
        return new { app_id = appId, pid = proc?.Id };
    }
}

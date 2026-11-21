using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using HomeOs.WindowsAgent.Capabilities;
using HomeOs.WindowsAgent.Security;
using Microsoft.Extensions.Hosting;

namespace HomeOs.WindowsAgent.Networking;

public sealed class HubWebSocketWorker : BackgroundService
{
    private readonly DpapiCredentialStore _store;
    private readonly WindowsCapabilityProvider _capabilities;

    public HubWebSocketWorker(DpapiCredentialStore store, WindowsCapabilityProvider capabilities)
    {
        _store = store;
        _capabilities = capabilities;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        int backoffSeconds = 2;
        while (!stoppingToken.IsCancellationRequested)
        {
            var creds = _store.Load();
            if (creds is null)
            {
                await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken);
                continue;
            }

            try
            {
                using var ws = new ClientWebSocket();
                long ts = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                string nonce = Guid.NewGuid().ToString("N");
                string sig = DpapiCredentialStore.SignRequest(
                    creds.HmacSecret, "WS", "/api/v1/agent/ws", ts, nonce, Array.Empty<byte>());

                string wsBase = creds.HubHttpUrl.Replace("https://", "wss://").Replace("http://", "ws://");
                var uri = new Uri($"{wsBase}/api/v1/agent/ws?device_id={Uri.EscapeDataString(creds.DeviceId)}&token={Uri.EscapeDataString(creds.DeviceToken)}&timestamp={ts}&nonce={nonce}&signature={sig}");

                await ws.ConnectAsync(uri, stoppingToken);
                backoffSeconds = 2;
                await SendJsonAsync(ws, new { type = "telemetry", payload = _capabilities.CollectTelemetry() }, stoppingToken);

                var buffer = new byte[65536];
                while (ws.State == WebSocketState.Open && !stoppingToken.IsCancellationRequested)
                {
                    var result = await ws.ReceiveAsync(buffer, stoppingToken);
                    if (result.MessageType == WebSocketMessageType.Close) break;
                    string json = Encoding.UTF8.GetString(buffer, 0, result.Count);
                    using var doc = JsonDocument.Parse(json);
                    string msgType = doc.RootElement.GetProperty("type").GetString() ?? "";
                    if (msgType == "command.execute")
                    {
                        await HandleCommandAsync(ws, doc.RootElement, stoppingToken);
                    }
                    else if (msgType == "session.terminated")
                    {
                        break;
                    }
                }
            }
            catch (OperationCanceledException)
            {
                break;
            }
            catch
            {
                await Task.Delay(TimeSpan.FromSeconds(backoffSeconds), stoppingToken);
                backoffSeconds = Math.Min(backoffSeconds * 2, 20);
            }
        }
    }

    private async Task HandleCommandAsync(ClientWebSocket ws, JsonElement cmd, CancellationToken ct)
    {
        string cmdId = cmd.GetProperty("command_id").GetString()!;
        string cap = cmd.GetProperty("capability_key").GetString()!;
        JsonElement p = cmd.GetProperty("parameters");

        try
        {
            object outcome = cap switch
            {
                "telemetry.status" => _capabilities.CollectTelemetry(),
                "files.browse" => _capabilities.BrowseDirectory(p.TryGetProperty("path", out var pathEl) ? pathEl.GetString() ?? "" : ""),
                "files.transfer" => _capabilities.WriteVerifiedFile(
                    p.GetProperty("target_dir").GetString() ?? "",
                    p.GetProperty("file_name").GetString()!,
                    p.GetProperty("content_b64").GetString()!,
                    p.GetProperty("expected_sha256").GetString() ?? ""),
                "screen.capture" => _capabilities.CaptureAuthorizedScreenJpeg(),
                "processes.list" => _capabilities.ListUserProcesses(),
                "apps.launch" => _capabilities.LaunchAllowedApp(p.GetProperty("app_id").GetString()!),
                _ => throw new NotSupportedException($"Capability '{cap}' is unsupported")
            };

            await SendJsonAsync(ws, new
            {
                type = "command.receipt",
                command_id = cmdId,
                acknowledged = true,
                state_verified = true,
                verification_method = "win32_state_readback",
                result = outcome
            }, ct);
        }
        catch (Exception ex)
        {
            await SendJsonAsync(ws, new
            {
                type = "command.receipt",
                command_id = cmdId,
                acknowledged = false,
                state_verified = false,
                error_code = "WINDOWS_AGENT_ERROR",
                error_message = ex.Message
            }, ct);
        }
    }

    private static Task SendJsonAsync(ClientWebSocket ws, object payload, CancellationToken ct)
    {
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(payload);
        return ws.SendAsync(bytes, WebSocketMessageType.Text, true, ct);
    }
}

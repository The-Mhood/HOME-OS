using HomeOs.WindowsAgent.Capabilities;
using HomeOs.WindowsAgent.Networking;
using HomeOs.WindowsAgent.Security;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

string appData = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "HomeOS");
string sharedRoot = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "HomeOS-Shared");

var builder = Host.CreateApplicationBuilder(args);
builder.Services.AddWindowsService(options => options.ServiceName = "HOME OS Windows Agent");
builder.Services.AddSingleton(new DpapiCredentialStore(appData));
builder.Services.AddSingleton(new WindowsCapabilityProvider(new[] { sharedRoot }));
builder.Services.AddHostedService<HubWebSocketWorker>();

await builder.Build().RunAsync();

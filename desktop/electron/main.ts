import { app, BrowserWindow, ipcMain } from 'electron';
import { spawn, ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';

let backendProcess: ChildProcess | null = null;
let mainWindow: BrowserWindow | null = null;
let lastSpawnError: string | null = null;

// Prevent unhandled main-process errors from triggering macOS native crash dialogs
process.on('uncaughtException', (err) => {
  console.error('[HOME OS Electron] Uncaught exception:', err);
});
process.on('unhandledRejection', (reason) => {
  console.error('[HOME OS Electron] Unhandled rejection:', reason);
});

function checkBackendHealth(port: string): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get(
      {
        hostname: '127.0.0.1',
        port: Number(port),
        path: '/api/v1/health',
        timeout: 1200,
      },
      (res) => {
        res.resume();
        resolve(res.statusCode === 200);
      }
    );
    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
  });
}

function resolveHomeOsBinary(): string | null {
  const home = os.homedir();
  const candidates = [
    process.env.HOMEOS_PYTHON_BIN,
    path.join(home, '.local', 'bin', 'homeos'),
    path.join(home, '.local', 'share', 'home-os', 'venv', 'bin', 'homeos'),
    '/opt/homebrew/bin/homeos',
    '/usr/local/bin/homeos',
    '/usr/bin/homeos',
  ].filter((c): c is string => Boolean(c));

  for (const bin of candidates) {
    try {
      if (fs.existsSync(bin)) {
        return bin;
      }
    } catch {
      // ignore fs errors
    }
  }
  return null;
}

function resolveBundledUiDir(): string | undefined {
  const home = os.homedir();
  const resPath = (process as NodeJS.Process & { resourcesPath?: string }).resourcesPath || '';
  const candidates = [
    path.join(resPath, 'ui-dist'),
    path.resolve(__dirname, '..', 'dist'),
    path.join(home, '.local', 'share', 'home-os', 'ui-dist'),
  ];
  for (const dir of candidates) {
    try {
      if (fs.existsSync(path.join(dir, 'index.html'))) {
        return dir;
      }
    } catch {
      // ignore
    }
  }
  return undefined;
}

async function ensureLocalBackendRunning(port: string): Promise<boolean> {
  if (await checkBackendHealth(port)) {
    return true;
  }
  if (process.env.HOMEOS_EXTERNAL_BACKEND === '1') {
    return false;
  }

  const binPath = resolveHomeOsBinary();
  if (!binPath) {
    lastSpawnError =
      'Could not locate ~/.local/bin/homeos. Run `bash scripts/install-macos.sh` in your HOME-OS folder first, or start `homeos serve` in Terminal.';
    return false;
  }

  const home = os.homedir();
  const dataDir = path.join(home, '.local', 'share', 'home-os', 'data');
  try {
    fs.mkdirSync(dataDir, { recursive: true });
  } catch {
    // ignore
  }

  const staticDir = resolveBundledUiDir();
  const augmentedPath = [
    path.join(home, '.local', 'bin'),
    '/opt/homebrew/bin',
    '/usr/local/bin',
    process.env.PATH || '/usr/bin:/bin:/usr/sbin:/sbin',
  ].join(':');

  try {
    backendProcess = spawn(binPath, ['serve', '--host', '0.0.0.0', '--port', port], {
      cwd: dataDir,
      stdio: 'ignore',
      env: {
        ...process.env,
        PATH: augmentedPath,
        HOMEOS_BIND_HOST: '0.0.0.0',
        HOMEOS_DATA_DIR: dataDir,
        ...(staticDir ? { HOMEOS_STATIC_DIR: staticDir } : {}),
      },
    });
    backendProcess.on('error', (err) => {
      lastSpawnError = err.message;
      backendProcess = null;
    });
  } catch (err) {
    lastSpawnError = err instanceof Error ? err.message : String(err);
    return false;
  }

  for (let attempt = 0; attempt < 45; attempt++) {
    if (await checkBackendHealth(port)) {
      return true;
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  return false;
}

function buildDiagnosticHtml(targetUrl: string, detail: string): string {
  const escaped = detail.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const html = `<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>HOME OS — Starting Core Engine</title>
<style>
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:#090B10; color:#F3F4F6; display:flex; align-items:center; justify-content:center; height:100vh; }
  .box { max-width:540px; background:#111622; border:1px solid #1F2937; border-radius:14px; padding:28px; box-shadow:0 20px 50px rgba(0,0,0,.5); }
  h2 { margin:0 0 10px; font-size:18px; }
  p { color:#9CA3AF; font-size:13.5px; line-height:1.55; }
  code { display:block; background:#090B10; border:1px solid #1F2937; padding:10px 12px; border-radius:8px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; color:#38BDF8; margin:12px 0; white-space:pre-wrap; }
  button { background:#0284C7; color:#fff; border:none; border-radius:8px; padding:10px 16px; font-weight:600; cursor:pointer; font-size:13px; }
  button:hover { background:#0369A1; }
</style>
</head>
<body>
  <div class="box">
    <h2>HOME OS Local Core Not Reachable Yet</h2>
    <p>${escaped}</p>
    <code>bash scripts/install-macos.sh
~/.local/bin/homeos serve --host 127.0.0.1 --port 8420</code>
    <button onclick="window.location.href='${targetUrl}'">Connect to ${targetUrl}</button>
  </div>
</body>
</html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

async function createCommandWindow(): Promise<void> {
  const port = process.env.HOMEOS_BIND_PORT || '8420';
  const targetUrl = process.env.HOMEOS_DESKTOP_URL || `http://127.0.0.1:${port}`;

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1024,
    minHeight: 700,
    title: 'HOME OS',
    backgroundColor: '#090B10',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));

  const bootHtml = `data:text/html;charset=utf-8,${encodeURIComponent(
    '<!doctype html><html><body style="margin:0;background:#090B10;color:#9CA3AF;font-family:-apple-system,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;font-size:14px;">Starting HOME OS Local Core Engine...</body></html>'
  )}`;
  await mainWindow.loadURL(bootHtml).catch(() => {});

  const ready = await ensureLocalBackendRunning(port);
  if (ready && mainWindow && !mainWindow.isDestroyed()) {
    try {
      await mainWindow.loadURL(targetUrl);
      return;
    } catch (err) {
      lastSpawnError = err instanceof Error ? err.message : String(err);
    }
  }

  if (mainWindow && !mainWindow.isDestroyed()) {
    const msg =
      lastSpawnError ||
      `Timed out waiting for local Core Engine on ${targetUrl}. Start 'homeos serve' in Terminal and click Connect.`;
    await mainWindow.loadURL(buildDiagnosticHtml(targetUrl, msg)).catch(() => {});
  }
}

ipcMain.handle('homeos:platform-info', () => ({
  shell: 'electron',
  platform: process.platform,
  arch: process.arch,
  version: '1.0.0',
}));

app.whenReady().then(async () => {
  await createCommandWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      void createCommandWindow();
    }
  });
});

app.on('before-quit', () => {
  if (backendProcess) {
    try {
      backendProcess.kill('SIGTERM');
    } catch {
      // ignore
    }
    backendProcess = null;
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

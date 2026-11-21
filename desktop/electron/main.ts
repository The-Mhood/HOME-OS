import { app, BrowserWindow, ipcMain } from 'electron';
import { spawn, ChildProcess } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let backendProcess: ChildProcess | null = null;
let mainWindow: BrowserWindow | null = null;

function startLocalBackendIfNeeded(): void {
  const port = process.env.HOMEOS_BIND_PORT || '8420';
  if (process.env.HOMEOS_EXTERNAL_BACKEND === '1') {
    return;
  }
  const pythonBin = process.env.HOMEOS_PYTHON_BIN || 'homeos';
  backendProcess = spawn(pythonBin, ['serve', '--host', '127.0.0.1', '--port', port], {
    stdio: 'ignore',
    env: { ...process.env, HOMEOS_BIND_HOST: '127.0.0.1' },
  });
}

async function createCommandWindow(): Promise<void> {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1080,
    minHeight: 720,
    title: 'HOME OS',
    backgroundColor: '#090B10',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  // Deny arbitrary popups from desktop UI
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));

  const targetUrl = process.env.HOMEOS_DESKTOP_URL || 'http://127.0.0.1:8420';
  await mainWindow.loadURL(targetUrl);
}

ipcMain.handle('homeos:platform-info', () => ({
  shell: 'electron',
  platform: process.platform,
  arch: process.arch,
  version: '1.0.0',
}));

app.whenReady().then(async () => {
  startLocalBackendIfNeeded();
  await createCommandWindow();
});

app.on('window-all-closed', () => {
  if (backendProcess) {
    backendProcess.kill('SIGTERM');
    backendProcess = null;
  }
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

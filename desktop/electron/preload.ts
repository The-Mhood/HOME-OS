import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('homeOsDesktop', {
  getPlatformInfo: () => ipcRenderer.invoke('homeos:platform-info'),
});

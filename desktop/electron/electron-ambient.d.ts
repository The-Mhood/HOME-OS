declare module 'electron' {
  export interface BrowserWindowConstructorOptions {
    width?: number;
    height?: number;
    minWidth?: number;
    minHeight?: number;
    title?: string;
    backgroundColor?: string;
    webPreferences?: {
      preload?: string;
      contextIsolation?: boolean;
      nodeIntegration?: boolean;
      sandbox?: boolean;
    };
  }

  export class BrowserWindow {
    constructor(options?: BrowserWindowConstructorOptions);
    loadURL(url: string): Promise<void>;
    on(event: string, listener: (...args: unknown[]) => void): this;
    webContents: {
      setWindowOpenHandler(handler: (details: { url: string }) => { action: 'deny' | 'allow' }): void;
      session: {
        webRequest: {
          onHeadersReceived(
            listener: (
              details: { responseHeaders?: Record<string, string[]> },
              callback: (resp: { responseHeaders?: Record<string, string[]> }) => void
            ) => void
          ): void;
        };
      };
    };
  }

  export const app: {
    whenReady(): Promise<void>;
    on(event: string, listener: (...args: unknown[]) => void): void;
    quit(): void;
    isPackaged: boolean;
  };

  export const contextBridge: {
    exposeInMainWorld(apiKey: string, api: Record<string, unknown>): void;
  };

  export const ipcRenderer: {
    invoke(channel: string, ...args: unknown[]): Promise<unknown>;
  };

  export const ipcMain: {
    handle(channel: string, listener: (...args: unknown[]) => unknown): void;
  };

  export const shell: {
    openExternal(url: string): Promise<void>;
  };
}

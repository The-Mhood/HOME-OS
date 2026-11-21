const TOKEN_KEY = 'homeos_session_token';

export function getStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setStoredToken(token: string | null): void {
  try {
    if (token) {
      localStorage.setItem(TOKEN_KEY, token);
    } else {
      localStorage.removeItem(TOKEN_KEY);
    }
  } catch {
    // ignore storage errors
  }
}

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const token = getStoredToken();
  const headers = new Headers(options.headers || {});
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
    headers.set('X-HomeOS-Token', token);
  }
  if (!(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const resp = await fetch(`/api/v1${path}`, {
    ...options,
    headers,
  });

  if (!resp.ok) {
    let msg = `Request failed (${resp.status})`;
    let code: string | undefined;
    try {
      const errJson = await resp.json();
      if (typeof errJson.detail === 'string') {
        msg = errJson.detail;
      } else if (errJson.detail && typeof errJson.detail === 'object') {
        msg = errJson.detail.message || msg;
        code = errJson.detail.code;
      }
    } catch {
      // ignore non-json error body
    }
    if (resp.status === 401 && token && !path.startsWith('/auth/login') && !path.startsWith('/auth/setup')) {
      setStoredToken(null);
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('homeos:unauthorized'));
      }
    }
    throw new ApiError(msg, resp.status, code);
  }

  return (await resp.json()) as T;
}

export async function fetchAuthorizedBlob(path: string): Promise<{ blob: Blob; sha256: string | null }> {
  const token = getStoredToken();
  const resp = await fetch(`/api/v1${path}`, {
    headers: token
      ? {
          Authorization: `Bearer ${token}`,
          'X-HomeOS-Token': token,
        }
      : {},
  });
  if (!resp.ok) {
    throw new ApiError(`Failed to fetch binary stream (${resp.status})`, resp.status);
  }
  const sha = resp.headers.get('X-SHA256-Checksum');
  const blob = await resp.blob();
  return { blob, sha256: sha };
}

export function connectOperatorWebSocket(
  onEvent: (envelope: { event_type: string; payload: Record<string, unknown> }) => void,
  onStateChange: (connected: boolean) => void
): () => void {
  const token = getStoredToken();
  if (!token || typeof WebSocket === 'undefined') {
    onStateChange(false);
    return () => {};
  }

  let closedByCaller = false;
  let ws: WebSocket | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;

  const connect = () => {
    if (closedByCaller) return;
    try {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      ws = new WebSocket(`${proto}//${window.location.host}/api/v1/ws?token=${encodeURIComponent(token)}`);
      ws.onopen = () => onStateChange(true);
      ws.onmessage = (ev) => {
        try {
          const parsed = JSON.parse(ev.data);
          if (parsed && parsed.event_type) {
            onEvent(parsed);
          }
        } catch {
          // ignore malformed frame
        }
      };
      ws.onerror = () => {
        // handled by onclose
      };
      ws.onclose = () => {
        onStateChange(false);
        if (!closedByCaller) {
          retryTimer = setTimeout(connect, 3000);
        }
      };
    } catch {
      onStateChange(false);
    }
  };

  connect();

  return () => {
    closedByCaller = true;
    if (retryTimer) clearTimeout(retryTimer);
    if (ws) {
      try {
        ws.close();
      } catch {
        // ignore
      }
    }
  };
}

import html
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from home_os.api.deps import get_db
from home_os.db.models import PairingSession
from home_os.security.crypto import hash_token

router = APIRouter()


def _ensure_utc(dt_val: datetime) -> datetime:
    if dt_val.tzinfo is None:
        return dt_val.replace(tzinfo=timezone.utc)
    return dt_val.astimezone(timezone.utc)


def _build_mobile_companion_html(*, session_id: str, qr_token: str, adapter_type: str, error_msg: str | None = None) -> str:
    esc_session = html.escape(session_id)
    esc_token = html.escape(qr_token)
    esc_adapter = html.escape(adapter_type)
    esc_err = html.escape(error_msg) if error_msg else ""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
  <title>HOME OS — Device Pairing &amp; Companion</title>
  <style>
    :root {{
      --bg: #090b10;
      --card: #111622;
      --border: #1f2937;
      --text: #f3f4f6;
      --muted: #9ca3af;
      --accent: #0284c7;
      --green: #10b981;
      --red: #ef4444;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 18px;
      background: radial-gradient(circle at 50% 0%, #111c2d 0%, #090b10 65%);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      min-height: 100vh;
    }}
    .wrap {{ max-width: 460px; margin: 0 auto; display: flex; flex-direction: column; gap: 14px; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 20px;
      box-shadow: 0 16px 40px rgba(0,0,0,0.45);
    }}
    .brand {{ display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }}
    .badge-dot {{ width: 10px; height: 10px; border-radius: 50%; background: var(--green); display: inline-block; }}
    h1 {{ font-size: 19px; margin: 0; }}
    p {{ font-size: 13.5px; color: var(--muted); line-height: 1.5; margin: 6px 0 0; }}
    label {{ display: block; font-size: 12px; color: var(--muted); margin: 12px 0 5px; }}
    input, textarea {{
      width: 100%;
      background: #090b10;
      border: 1px solid var(--border);
      color: var(--text);
      border-radius: 10px;
      padding: 11px 12px;
      font-size: 15px;
    }}
    .btn {{
      width: 100%;
      border: none;
      border-radius: 10px;
      padding: 13px 16px;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      margin-top: 14px;
    }}
    .btn-primary {{ background: var(--accent); color: #fff; }}
    .btn-green {{ background: var(--green); color: #052e16; }}
    .btn-outline {{ background: transparent; border: 1px solid var(--border); color: var(--text); margin-top: 8px; }}
    .err {{ background: rgba(239,68,68,0.16); border: 1px solid rgba(239,68,68,0.4); color: #fca5a5; padding: 10px 12px; border-radius: 10px; font-size: 13px; margin-top: 10px; }}
    .stat-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }}
    .stat {{ background: #090b10; border: 1px solid var(--border); border-radius: 10px; padding: 10px; }}
    .stat-k {{ font-size: 11px; color: var(--muted); }}
    .stat-v {{ font-size: 15px; font-weight: 700; margin-top: 2px; font-family: ui-monospace, SFMono-Regular, monospace; }}
    .log {{ font-family: ui-monospace, SFMono-Regular, monospace; font-size: 11.5px; color: var(--muted); max-height: 130px; overflow-y: auto; background: #090b10; border-radius: 8px; padding: 8px; margin-top: 10px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card" id="pair-card" style="display: {'none' if not esc_session and not esc_err else 'block'};">
      <div class="brand">
        <div style="width:34px;height:34px;border-radius:9px;background:#0284c7;display:flex;align-items:center;justify-content:center;font-weight:800;">H</div>
        <div>
          <h1>HOME OS Companion Pairing</h1>
          <div style="font-size:12px;color:var(--muted);">Local Wi-Fi Cryptographic Enrollment</div>
        </div>
      </div>

      {"<div class='err'>" + esc_err + "</div>" if esc_err else ""}

      <div id="pair-form-box" style="display: {'none' if esc_err else 'block'};">
        <p>Approve pairing to connect this device to your HOME OS Hub over your local Wi-Fi network.</p>

        <label>Device Display Name</label>
        <input id="dev-name" value="" placeholder="e.g. My iPhone / Android Phone" />

        <div id="err-box" class="err" style="display:none;"></div>

        <button class="btn btn-primary" id="pair-btn" onclick="completePairing()">
          Approve &amp; Connect This Device
        </button>
      </div>
    </div>

    <div class="card" id="live-card" style="display: none;">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div>
          <h1 id="live-title">Connected Companion</h1>
          <div style="font-size:12px;color:var(--muted);" id="live-sub">Authenticated via HMAC-SHA256</div>
        </div>
        <span id="ws-pill" style="font-size:12px;padding:4px 10px;border-radius:99px;background:rgba(16,185,129,0.18);color:#34d399;">
          <span class="badge-dot"></span> Online
        </span>
      </div>

      <div class="stat-grid">
        <div class="stat">
          <div class="stat-k">Platform</div>
          <div class="stat-v" id="st-os">—</div>
        </div>
        <div class="stat">
          <div class="stat-k">Battery</div>
          <div class="stat-v" id="st-batt">OS Protected</div>
        </div>
      </div>

      <label>Send Quick Message / Note to Command Centre</label>
      <textarea id="msg-body" rows="2" placeholder="Type a note or link to send to your Mac..."></textarea>
      <button class="btn btn-green" onclick="sendCompanionMessage()">Send to Command Centre</button>

      <button class="btn btn-outline" onclick="sendTelemetryNow()">Sync Live Status Now</button>
      <button class="btn btn-outline" style="color:#fca5a5;" onclick="unpairLocal()">Disconnect Local Session</button>

      <div class="log" id="event-log"></div>
    </div>
  </div>

<script>
// Pure-JS SHA-256 + HMAC-SHA256 so signing works on plain HTTP LAN URLs (where crypto.subtle is unavailable)
function rightRotate(value, amount) {{ return (value >>> amount) | (value << (32 - amount)); }}
function sha256Bytes(bytes) {{
  const K = [
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
  ];
  let H = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
  const bitLen = bytes.length * 8;
  const paddedLen = (((bytes.length + 9 + 63) >> 6) << 6);
  const buf = new Uint8Array(paddedLen);
  buf.set(bytes);
  buf[bytes.length] = 0x80;
  const dv = new DataView(buf.buffer);
  dv.setUint32(paddedLen - 8, Math.floor(bitLen / 0x100000000), false);
  dv.setUint32(paddedLen - 4, bitLen >>> 0, false);
  const W = new Uint32Array(64);
  for (let offset = 0; offset < paddedLen; offset += 64) {{
    for (let i = 0; i < 16; i++) W[i] = dv.getUint32(offset + i * 4, false);
    for (let i = 16; i < 64; i++) {{
      const s0 = rightRotate(W[i-15], 7) ^ rightRotate(W[i-15], 18) ^ (W[i-15] >>> 3);
      const s1 = rightRotate(W[i-2], 17) ^ rightRotate(W[i-2], 19) ^ (W[i-2] >>> 10);
      W[i] = (W[i-16] + s0 + W[i-7] + s1) >>> 0;
    }}
    let [a,b,c,d,e,f,g,h] = H;
    for (let i = 0; i < 64; i++) {{
      const S1 = rightRotate(e, 6) ^ rightRotate(e, 11) ^ rightRotate(e, 25);
      const ch = (e & f) ^ ((~e) & g);
      const temp1 = (h + S1 + ch + K[i] + W[i]) >>> 0;
      const S0 = rightRotate(a, 2) ^ rightRotate(a, 13) ^ rightRotate(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (S0 + maj) >>> 0;
      h = g; g = f; f = e; e = (d + temp1) >>> 0; d = c; c = b; b = a; a = (temp1 + temp2) >>> 0;
    }}
    H = [(H[0]+a)>>>0,(H[1]+b)>>>0,(H[2]+c)>>>0,(H[3]+d)>>>0,(H[4]+e)>>>0,(H[5]+f)>>>0,(H[6]+g)>>>0,(H[7]+h)>>>0];
  }}
  const out = new Uint8Array(32);
  const outDv = new DataView(out.buffer);
  for (let i = 0; i < 8; i++) outDv.setUint32(i * 4, H[i], false);
  return out;
}}
function toHex(u8) {{ return Array.from(u8).map(b => b.toString(16).padStart(2, '0')).join(''); }}
function utf8Bytes(str) {{ return new TextEncoder().encode(str); }}
function hmacSha256Hex(keyStr, msgStr) {{
  let key = utf8Bytes(keyStr);
  if (key.length > 64) key = sha256Bytes(key);
  const blockKey = new Uint8Array(64);
  blockKey.set(key);
  const oPad = new Uint8Array(64), iPad = new Uint8Array(64);
  for (let i = 0; i < 64; i++) {{ oPad[i] = blockKey[i] ^ 0x5c; iPad[i] = blockKey[i] ^ 0x36; }}
  const msg = utf8Bytes(msgStr);
  const inner = new Uint8Array(64 + msg.length);
  inner.set(iPad); inner.set(msg, 64);
  const innerHash = sha256Bytes(inner);
  const outer = new Uint8Array(64 + 32);
  outer.set(oPad); outer.set(innerHash, 64);
  return toHex(sha256Bytes(outer));
}}
function signAgentRequest(secret, method, path, ts, nonce, bodyStr) {{
  const bodySha = toHex(sha256Bytes(utf8Bytes(bodyStr || '')));
  const canonical = `${{method.toUpperCase()}}|${{path}}|${{ts}}|${{nonce}}|${{bodySha}}`;
  return hmacSha256Hex(secret, canonical);
}}

const SESSION_ID = "{esc_session}";
const QR_TOKEN = "{esc_token}";
const STORAGE_KEY = "homeos_mobile_companion_creds";
let wsConn = null;
let telemetryTimer = null;

function detectDeviceInfo() {{
  const ua = navigator.userAgent || "";
  if (/iPhone/i.test(ua)) return {{ name: "iPhone", category: "smartphone", vendor: "Apple", os: "iOS", adapter: "ios_companion" }};
  if (/iPad/i.test(ua)) return {{ name: "iPad", category: "tablet", vendor: "Apple", os: "iPadOS", adapter: "ios_companion" }};
  if (/Android/i.test(ua)) return {{ name: "Android Phone", category: "smartphone", vendor: "Android", os: "Android", adapter: "android_companion" }};
  if (/Macintosh/i.test(ua)) return {{ name: "Mac Companion", category: "laptop", vendor: "Apple", os: "macOS", adapter: "macos_agent" }};
  if (/Windows/i.test(ua)) return {{ name: "Windows PC", category: "desktop", vendor: "Microsoft", os: "Windows", adapter: "windows_agent" }};
  return {{ name: "Mobile Companion", category: "smartphone", vendor: "Web Companion", os: navigator.platform || "Mobile", adapter: "{esc_adapter}" || "android_companion" }};
}}

const detected = detectDeviceInfo();
const nameInput = document.getElementById("dev-name");
if (nameInput) nameInput.value = detected.name;
document.getElementById("st-os").textContent = detected.os;

function logMsg(txt) {{
  const el = document.getElementById("event-log");
  const t = new Date().toLocaleTimeString();
  el.innerHTML = `<div>[${{t}}] ${{txt}}</div>` + el.innerHTML;
}}

function getFingerprint() {{
  let fp = localStorage.getItem("homeos_companion_hw_fp");
  if (!fp) {{
    fp = "comp-" + Math.random().toString(36).slice(2, 12) + Date.now().toString(36);
    localStorage.setItem("homeos_companion_hw_fp", fp);
  }}
  return fp;
}}

async function collectPayload() {{
  let battLevel = null, charging = null;
  if (navigator.getBattery) {{
    try {{
      const b = await navigator.getBattery();
      battLevel = Math.round(b.level * 100);
      charging = Boolean(b.charging);
      document.getElementById("st-batt").textContent = `${{battLevel}}% ${{charging ? '⚡' : ''}}`;
    }} catch (e) {{}}
  }}
  const payload = {{
    health_score: 100,
    attributes: {{
      platform: detected.os,
      screen: `${{window.screen.width}}x${{window.screen.height}}`,
      language: navigator.language,
      online: navigator.onLine,
      companion_mode: "mobile_web_ws"
    }}
  }};
  if (battLevel !== null) payload.battery_level = battLevel;
  if (charging !== null) payload.is_charging = charging;
  return payload;
}}

async function completePairing() {{
  const btn = document.getElementById("pair-btn");
  const errBox = document.getElementById("err-box");
  errBox.style.display = "none";
  btn.disabled = true;
  btn.textContent = "Encrypting & Pairing...";
  try {{
    const resp = await fetch("/api/v1/pairing/complete", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{
        pairing_session_id: SESSION_ID,
        pin_or_token: QR_TOKEN,
        hardware_fingerprint: getFingerprint(),
        name: (document.getElementById("dev-name").value || detected.name).trim(),
        category: detected.category,
        manufacturer: detected.vendor,
        os_name: detected.os
      }})
    }});
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Pairing rejected");
    const saved = {{
      device_id: data.credentials.device_id,
      device_token: data.credentials.device_token,
      hmac_secret: data.credentials.hmac_secret,
      name: data.device.name
    }};
    localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
    startLiveCompanion(saved);
  }} catch (err) {{
    errBox.textContent = err.message || String(err);
    errBox.style.display = "block";
    btn.disabled = false;
    btn.textContent = "Approve & Connect This Device";
  }}
}}

async function sendTelemetryNow() {{
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return;
  const creds = JSON.parse(raw);
  const bodyObj = await collectPayload();
  const bodyStr = JSON.stringify(bodyObj);
  const ts = Math.floor(Date.now() / 1000);
  const nonce = Math.random().toString(36).slice(2) + Date.now().toString(36);
  const path = "/api/v1/agent/telemetry";
  const sig = signAgentRequest(creds.hmac_secret, "POST", path, ts, nonce, bodyStr);
  const r = await fetch(path, {{
    method: "POST",
    headers: {{
      "Content-Type": "application/json",
      "X-Device-Id": creds.device_id,
      "X-Device-Token": creds.device_token,
      "X-Timestamp": String(ts),
      "X-Nonce": nonce,
      "X-Signature": sig
    }},
    body: bodyStr
  }});
  if (r.ok) logMsg("Synced telemetry with HOME OS Hub.");
}}

async function sendCompanionMessage() {{
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return;
  const creds = JSON.parse(raw);
  const msgEl = document.getElementById("msg-body");
  const text = (msgEl.value || "").trim();
  if (!text) return;
  const bodyObj = {{
    source_type: "messaging_notification",
    app_name: "HOME OS Companion",
    sender: creds.name || detected.name,
    title: "Companion Message",
    body_preview: text,
    can_reply: false
  }};
  const bodyStr = JSON.stringify(bodyObj);
  const ts = Math.floor(Date.now() / 1000);
  const nonce = Math.random().toString(36).slice(2) + Date.now().toString(36);
  const path = "/api/v1/agent/communications";
  const sig = signAgentRequest(creds.hmac_secret, "POST", path, ts, nonce, bodyStr);
  const r = await fetch(path, {{
    method: "POST",
    headers: {{
      "Content-Type": "application/json",
      "X-Device-Id": creds.device_id,
      "X-Device-Token": creds.device_token,
      "X-Timestamp": String(ts),
      "X-Nonce": nonce,
      "X-Signature": sig
    }},
    body: bodyStr
  }});
  if (r.ok) {{
    msgEl.value = "";
    logMsg("Delivered message to Command Centre.");
  }} else {{
    const j = await r.json().catch(() => ({{}}));
    logMsg("Message blocked: " + (j.detail || "Grant notifications:read permission on Mac"));
  }}
}}

function startLiveCompanion(creds) {{
  document.getElementById("pair-card").style.display = "none";
  document.getElementById("live-card").style.display = "block";
  document.getElementById("live-title").textContent = creds.name || "Connected Companion";

  const connectWs = async () => {{
    const ts = Math.floor(Date.now() / 1000);
    const nonce = Math.random().toString(36).slice(2) + Date.now().toString(36);
    const path = "/api/v1/agent/ws";
    const sig = signAgentRequest(creds.hmac_secret, "WS", path, ts, nonce, "");
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${{proto}}//${{location.host}}${{path}}?device_id=${{encodeURIComponent(creds.device_id)}}&token=${{encodeURIComponent(creds.device_token)}}&timestamp=${{ts}}&nonce=${{nonce}}&signature=${{sig}}`;
    wsConn = new WebSocket(url);
    wsConn.onopen = async () => {{
      document.getElementById("ws-pill").textContent = "● Online";
      logMsg("Live encrypted session active with HOME OS.");
      const t = await collectPayload();
      wsConn.send(JSON.stringify({{ type: "telemetry", payload: t }}));
    }};
    wsConn.onmessage = async (ev) => {{
      try {{
        const msg = JSON.parse(ev.data);
        if (msg.type === "command.execute") {{
          logMsg(`Command received: ${{msg.capability_key}}`);
          if (navigator.vibrate) navigator.vibrate(120);
          const t = await collectPayload();
          wsConn.send(JSON.stringify({{
            type: "command.receipt",
            command_id: msg.command_id,
            acknowledged: true,
            state_verified: true,
            verification_method: "companion_web_ack",
            result: t
          }}));
        }}
      }} catch (e) {{}}
    }};
    wsConn.onclose = () => {{
      document.getElementById("ws-pill").textContent = "Reconnecting...";
      setTimeout(connectWs, 3500);
    }};
  }};

  connectWs();
  if (telemetryTimer) clearInterval(telemetryTimer);
  telemetryTimer = setInterval(sendTelemetryNow, 15000);
}}

function unpairLocal() {{
  localStorage.removeItem(STORAGE_KEY);
  if (wsConn) wsConn.close();
  location.href = "/companion";
}}

// Auto-resume if already paired and no fresh QR session token is being claimed
const existingRaw = localStorage.getItem(STORAGE_KEY);
if (!SESSION_ID && existingRaw) {{
  try {{ startLiveCompanion(JSON.parse(existingRaw)); }} catch (e) {{}}
}}
</script>
</body>
</html>"""


@router.get("/p/{qr_token}", response_class=HTMLResponse)
def mobile_qr_pairing_landing(
    qr_token: str,
    db: Session = Depends(get_db),
):
    token_hash = hash_token(qr_token.strip())
    session = (
        db.execute(
            select(PairingSession).where(PairingSession.qr_token_hash == token_hash)
        )
        .scalars()
        .first()
    )

    if session is None:
        return HTMLResponse(
            _build_mobile_companion_html(
                session_id="",
                qr_token="",
                adapter_type="",
                error_msg="Invalid or unknown QR pairing link. Generate a fresh QR code in HOME OS -> Devices.",
            ),
            status_code=404,
        )

    now = datetime.now(timezone.utc)
    if session.status != "pending" or _ensure_utc(session.expires_at) <= now:
        return HTMLResponse(
            _build_mobile_companion_html(
                session_id="",
                qr_token="",
                adapter_type=session.adapter_type,
                error_msg="This single-use QR pairing code has expired or was already used. Generate a new QR code on your Mac.",
            ),
            status_code=410,
        )

    return HTMLResponse(
        _build_mobile_companion_html(
            session_id=session.id,
            qr_token=qr_token.strip(),
            adapter_type=session.adapter_type,
        )
    )


@router.get("/companion", response_class=HTMLResponse)
def mobile_companion_console(request: Request):
    return HTMLResponse(
        _build_mobile_companion_html(
            session_id="",
            qr_token="",
            adapter_type="android_companion",
            error_msg=None,
        )
    )

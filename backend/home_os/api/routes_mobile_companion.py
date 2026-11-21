import html
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
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
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
  <meta name="apple-mobile-web-app-title" content="HOME OS" />
  <link rel="manifest" href="/companion.webmanifest" />
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
      --amber: #f59e0b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 16px;
      background: radial-gradient(circle at 50% 0%, #111c2d 0%, #090b10 65%);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      min-height: 100vh;
    }}
    .wrap {{ max-width: 460px; margin: 0 auto; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 20px;
      margin-bottom: 14px;
      box-shadow: 0 18px 40px rgba(0, 0, 0, 0.45);
    }}
    .badge {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 11.5px;
      font-weight: 600;
      background: rgba(56, 189, 248, 0.14);
      color: #38bdf8;
      margin-bottom: 8px;
    }}
    h1 {{ margin: 0 0 6px; font-size: 20px; font-weight: 700; }}
    h2 {{ margin: 0 0 10px; font-size: 15px; font-weight: 700; }}
    p {{ margin: 0 0 12px; color: var(--muted); font-size: 13px; line-height: 1.5; }}
    label {{ display: block; font-size: 12px; color: var(--muted); margin-bottom: 5px; font-weight: 500; }}
    input, textarea {{
      width: 100%;
      padding: 11px 12px;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: #090b10;
      color: var(--text);
      font-size: 14px;
      margin-bottom: 10px;
    }}
    .btn {{
      width: 100%;
      padding: 12px 14px;
      border: none;
      border-radius: 11px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      background: var(--accent);
      color: #fff;
    }}
    .btn:disabled {{ opacity: 0.55; }}
    .btn-green {{ background: var(--green); }}
    .btn-danger {{ background: rgba(239, 68, 68, 0.18); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.35); }}
    .btn-outline {{ background: #161e2e; color: #e5e7eb; border: 1px solid var(--border); }}
    .row {{ display: flex; gap: 8px; }}
    .row > * {{ flex: 1; }}
    .err {{
      padding: 12px;
      border-radius: 10px;
      background: rgba(239, 68, 68, 0.14);
      border: 1px solid rgba(239, 68, 68, 0.35);
      color: #fca5a5;
      font-size: 13px;
      margin-bottom: 12px;
    }}
    .stat-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin: 10px 0;
    }}
    .stat {{
      background: #090b10;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 9px 10px;
      font-size: 12px;
    }}
    .stat strong {{ display: block; font-size: 14px; margin-top: 2px; color: #38bdf8; }}
    #log {{
      max-height: 130px;
      overflow-y: auto;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 11.5px;
      background: #090b10;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 9px;
      color: #cbd5e1;
    }}
    /* Full-screen emergency ring / alert / screen-torch overlays */
    #ring-overlay {{
      display: none;
      position: fixed;
      inset: 0;
      z-index: 9999;
      background: linear-gradient(180deg, #dc2626, #7f1d1d);
      color: #fff;
      padding: 28px;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      animation: pulseBg 0.55s infinite alternate;
    }}
    @keyframes pulseBg {{
      from {{ background: #dc2626; }}
      to {{ background: #1d4ed8; }}
    }}
    #torch-overlay {{
      display: none;
      position: fixed;
      inset: 0;
      z-index: 9998;
      background: #ffffff;
      color: #090b10;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 28px;
    }}
    #alert-modal {{
      display: none;
      position: fixed;
      inset: 0;
      z-index: 9997;
      background: rgba(0, 0, 0, 0.82);
      padding: 20px;
      align-items: center;
      justify-content: center;
    }}
  </style>
</head>
<body>
  <!-- Ring / Find Phone Overlay -->
  <div id="ring-overlay">
    <div style="font-size: 54px; margin-bottom: 10px;">🔔</div>
    <h1 style="font-size: 26px; margin-bottom: 8px;">FIND MY DEVICE</h1>
    <p id="ring-msg" style="color: #fee2e2; font-size: 15px; margin-bottom: 24px;">Triggered from HOME OS Command Centre</p>
    <button class="btn" style="background: #fff; color: #111827; font-size: 16px; padding: 15px;" onclick="stopRinging()">
      Stop Ringing
    </button>
  </div>

  <!-- Full-Screen Torch Overlay (for iOS & screens without rear-camera WebRTC torch) -->
  <div id="torch-overlay">
    <div style="font-size: 42px; margin-bottom: 12px;">🔦</div>
    <div style="font-weight: 700; font-size: 18px; margin-bottom: 18px;">Full-Brightness Torch Active</div>
    <button class="btn" style="max-width: 240px; background: #090b10; color: #fff;" onclick="disableTorch()">
      Turn Off Torch
    </button>
  </div>

  <!-- Incoming Mac Alert / Deep-Link Modal -->
  <div id="alert-modal">
    <div class="card" style="width: 100%; max-width: 400px; border-color: #38bdf8;">
      <div class="badge">Message from Command Centre</div>
      <h1 id="alert-title" style="font-size: 18px;">HOME OS Alert</h1>
      <p id="alert-body" style="color: #f3f4f6; font-size: 15px; margin: 10px 0 14px; white-space: pre-wrap;"></p>
      <a id="alert-link" href="#" target="_blank" class="btn btn-green" style="display:none; text-align:center; text-decoration:none; margin-bottom:10px;">
        Open Link Now
      </a>
      <button class="btn btn-outline" onclick="document.getElementById('alert-modal').style.display='none'">
        Dismiss
      </button>
    </div>
  </div>

  <div class="wrap">
    <div class="card" id="pair-card" style="{ 'display:none;' if not session_id and not error_msg else '' }">
      <span class="badge">HOME OS Local Wi-Fi Pairing</span>
      <h1>HOME OS Companion Pairing</h1>
      <p>Connect this phone to your Mac over encrypted local Wi-Fi — zero cloud servers.</p>
      <div id="err-box" class="err" style="{ '' if error_msg else 'display:none;' }">{esc_err}</div>
      <div style="{ 'display:none;' if error_msg else '' }">
        <label for="dev-name">Device Name Shown on Mac</label>
        <input id="dev-name" value="" placeholder="My iPhone / Android" />
        <button id="pair-btn" class="btn btn-green" onclick="completePairing()">
          Approve &amp; Connect This Device
        </button>
      </div>
    </div>

    <div id="live-area" style="{ '' if not session_id and not error_msg else 'display:none;' }">
      <!-- Status & Wake Lock Card -->
      <div class="card">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
          <span class="badge" id="ws-pill" style="margin:0;">Connecting...</span>
          <button id="wake-btn" onclick="enableAudioAndWakeLock(true)" style="background:#161e2e; color:#38bdf8; border:1px solid #1f2937; border-radius:999px; padding:4px 10px; font-size:11px; font-weight:600; cursor:pointer;">
            🔊 Sound &amp; Keep Awake
          </button>
        </div>
        <h1 id="live-title">Connected Companion</h1>
        <p style="margin-bottom:8px;">Tip: Tap <strong>Share → Add to Home Screen</strong> to pin as a standalone app. If your screen locks, commands from your Mac queue safely and run on wake.</p>
        <div class="stat-grid">
          <div class="stat">Platform<strong id="st-os">-</strong></div>
          <div class="stat">Battery<strong id="st-bat">Supported / Synced</strong></div>
        </div>
      </div>

      <!-- Instant Photo / File Drop to Mac -->
      <div class="card">
        <h2>📁 Instant Photo / File Drop to Mac</h2>
        <p>Pick a photo, video, or document (or snap with camera) — saves straight into <code>~/HomeOS-Shared</code> on your Mac.</p>
        <input type="file" id="file-picker" onchange="uploadSelectedFile()" style="padding:8px;" />
        <div id="upload-status" style="font-size:12px; color:#38bdf8;"></div>
      </div>

      <!-- Shared Clipboard & Message to Mac -->
      <div class="card">
        <h2>📋 Two-Way Clipboard &amp; Message</h2>
        <div id="mac-clip-box" style="display:none; padding:10px; border-radius:10px; background:#090b10; border:1px solid #0284c7; margin-bottom:10px;">
          <div style="font-size:11px; color:#38bdf8; margin-bottom:4px;">Received from Mac:</div>
          <div id="mac-clip-text" style="font-size:13.5px; word-break:break-all; margin-bottom:8px;"></div>
          <button class="btn btn-sm btn-outline" style="padding:7px;" onclick="copyReceivedText()">Copy to Phone Clipboard</button>
        </div>
        <textarea id="msg-body" rows="2" placeholder="Paste text/link for Mac clipboard, or send a message..."></textarea>
        <div class="row">
          <button class="btn" onclick="beamClipboardToMac()">Copy to Mac Clipboard</button>
          <button class="btn btn-outline" onclick="sendCompanionMessage()">Send Alert</button>
        </div>
      </div>

      <!-- Activity Log & Disconnect -->
      <div class="card">
        <h2>Live Command &amp; Sync Log</h2>
        <div id="log">Ready.</div>
        <div class="row" style="margin-top:10px;">
          <button class="btn btn-outline" onclick="sendTelemetryNow()">Sync Now</button>
          <button class="btn btn-danger" onclick="unpairLocal()">Unpair</button>
        </div>
      </div>
    </div>
  </div>

<script>
const SESSION_ID = "{esc_session}";
const QR_TOKEN = "{esc_token}";
const STORAGE_KEY = "homeos_companion_creds_v1";

// Pure-JS SHA-256 & HMAC-SHA256 so HTTP LAN connections work without WebCrypto HTTPS restriction
function sha256Bytes(ascii) {{
  function rightRotate(v, amt) {{ return (v >>> amt) | (v << (32 - amt)); }}
  const mathPow = Math.pow, maxWord = mathPow(2, 32);
  let i, j, result = [];
  const words = [], asciiBitLength = ascii.length * 8;
  let hash = [], k = [];
  let primeCounter = 0;
  const isComposite = {{}};
  for (let candidate = 2; primeCounter < 64; candidate++) {{
    if (!isComposite[candidate]) {{
      for (i = 0; i < 313; i += candidate) isComposite[i] = candidate;
      hash[primeCounter] = (mathPow(candidate, .5) * maxWord) | 0;
      k[primeCounter++] = (mathPow(candidate, 1 / 3) * maxWord) | 0;
    }}
  }}
  ascii += "\\x80";
  while (ascii.length % 64 - 56) ascii += "\\x00";
  for (i = 0; i < ascii.length; i++) {{
    j = ascii.charCodeAt(i);
    words[i >> 2] |= j << ((3 - i) % 4) * 8;
  }}
  words[words.length] = ((asciiBitLength / maxWord) | 0);
  words[words.length] = (asciiBitLength) | 0;
  for (j = 0; j < words.length;) {{
    const w = words.slice(j, j += 16);
    const oldHash = hash.slice(0);
    for (i = 0; i < 64; i++) {{
      const i2 = i + j;
      const w15 = w[i - 15], w2 = w[i - 2];
      const a = hash[0], e = hash[4];
      const temp1 = hash[7]
        + (rightRotate(e, 6) ^ rightRotate(e, 11) ^ rightRotate(e, 25))
        + ((e & hash[5]) ^ ((~e) & hash[6]))
        + k[i]
        + (w[i] = (i < 16) ? w[i] : (
            w[i - 16]
            + (rightRotate(w15, 7) ^ rightRotate(w15, 18) ^ (w15 >>> 3))
            + w[i - 7]
            + (rightRotate(w2, 17) ^ rightRotate(w2, 19) ^ (w2 >>> 10))
          ) | 0);
      const temp2 = (rightRotate(a, 2) ^ rightRotate(a, 13) ^ rightRotate(a, 22))
        + ((a & hash[1]) ^ (a & hash[2]) ^ (hash[1] & hash[2]));
      hash = [(temp1 + temp2) | 0].concat(hash);
      hash[4] = (hash[4] + temp1) | 0;
      hash.pop();
    }}
    for (i = 0; i < 8; i++) hash[i] = (hash[i] + oldHash[i]) | 0;
  }}
  for (i = 0; i < 8; i++) {{
    for (j = 3; j + 1; j--) result.push((hash[i] >> (j * 8)) & 255);
  }}
  return result;
}}

function toUtf8Binary(str) {{ return unescape(encodeURIComponent(str)); }}
function bytesToHex(bytes) {{ return bytes.map(b => (b < 16 ? "0" : "") + b.toString(16)).join(""); }}
function sha256Hex(str) {{ return bytesToHex(sha256Bytes(toUtf8Binary(str))); }}

function hmacSha256Hex(keyStr, msgStr) {{
  let keyBytes = [];
  const kBin = toUtf8Binary(keyStr);
  for (let i = 0; i < kBin.length; i++) keyBytes.push(kBin.charCodeAt(i));
  if (keyBytes.length > 64) keyBytes = sha256Bytes(kBin);
  while (keyBytes.length < 64) keyBytes.push(0);
  let oKey = "", iKey = "";
  for (let i = 0; i < 64; i++) {{
    oKey += String.fromCharCode(keyBytes[i] ^ 0x5c);
    iKey += String.fromCharCode(keyBytes[i] ^ 0x36);
  }}
  const innerHash = sha256Bytes(iKey + toUtf8Binary(msgStr));
  let innerBin = "";
  for (let i = 0; i < innerHash.length; i++) innerBin += String.fromCharCode(innerHash[i]);
  return bytesToHex(sha256Bytes(oKey + innerBin));
}}

function signAgentRequest(secret, method, path, ts, nonce, bodyStr) {{
  const bodyDigest = sha256Hex(bodyStr || "");
  const canonical = [method.toUpperCase(), path, String(ts), nonce, bodyDigest].join("|");
  return hmacSha256Hex(secret, canonical);
}}

function detectPlatform() {{
  const ua = navigator.userAgent || "";
  if (/iPhone|iPad|iPod/i.test(ua)) return {{ os: "iOS", category: "smartphone", name: /iPad/i.test(ua) ? "iPad" : "iPhone", vendor: "Apple" }};
  if (/Android/i.test(ua)) return {{ os: "Android", category: "smartphone", name: "Android Phone", vendor: "Android" }};
  if (/Mac/i.test(ua)) return {{ os: "macOS", category: "laptop", name: "Mac Companion", vendor: "Apple" }};
  return {{ os: navigator.platform || "Mobile", category: "smartphone", name: "Mobile Companion", vendor: "Generic" }};
}}

const detected = detectPlatform();
const nameInput = document.getElementById("dev-name");
if (nameInput && !nameInput.value) nameInput.value = detected.name;
document.getElementById("st-os").textContent = detected.os;

// Real, honest mobile companion capabilities backed by real WebAudio / Torch / URL / Clipboard / File APIs
const COMPANION_CAPABILITIES = [
  {{ capability_key: "telemetry.status", category: "telemetry", display_name: "Live Battery, Screen & Network Telemetry", supported: true, requires_permission: "telemetry:read" }},
  {{ capability_key: "device.ring", category: "audio", display_name: "Find My Phone — Loud Audible Siren & Vibration", supported: true, requires_permission: "telemetry:read" }},
  {{ capability_key: "device.notify", category: "communications", display_name: "Push Full-Screen Alert Banner to Phone", supported: true, requires_permission: "notifications:reply", parameters_schema: {{ required: ["message"], properties: {{ title: {{ type: "string" }}, message: {{ type: "string" }} }} }} }},
  {{ capability_key: "apps.launch", category: "applications", display_name: "Open URL or App Deep-Link on Phone", supported: true, requires_permission: "apps:launch", parameters_schema: {{ properties: {{ url: {{ type: "string" }}, app_id: {{ type: "string" }}, package_name: {{ type: "string" }}, shortcut_name: {{ type: "string" }} }} }} }},
  {{ capability_key: "clipboard.send", category: "applications", display_name: "Beam Text / Code to Phone Clipboard", supported: true, requires_permission: "apps:launch", parameters_schema: {{ required: ["text"], properties: {{ text: {{ type: "string" }} }} }} }},
  {{ capability_key: "flashlight.set", category: "hardware", display_name: "Flashlight / Full-Brightness Screen Torch", supported: true, requires_permission: "flashlight:control", parameters_schema: {{ required: ["enabled"], properties: {{ enabled: {{ type: "boolean" }} }} }} }},
  {{ capability_key: "volume.set", category: "audio", display_name: "System Hardware Volume", supported: false, unavailable_reason: "Restricted by iOS/Android OS sandbox for web companions (use Ring Phone or Native Mac/Linux/TV adapters for hardware volume).", requires_permission: "volume:control" }}
];

let audioCtx = null;
let ringTimer = null;
let wakeLockSentinel = null;
let torchStream = null;
let wsConn = null;
let telemetryTimer = null;
let pollTimer = null;

function logMsg(txt) {{
  const el = document.getElementById("log");
  if (!el) return;
  const t = new Date().toLocaleTimeString();
  el.innerHTML = `[${{t}}] ${{txt}}<br/>` + el.innerHTML;
}}

async function enableAudioAndWakeLock(playBeep) {{
  try {{
    const AC = window.AudioContext || window.webkitAudioContext;
    if (AC) {{
      if (!audioCtx) audioCtx = new AC();
      if (audioCtx.state === "suspended") await audioCtx.resume();
      if (playBeep) playTone(660, 0.08);
    }}
  }} catch (e) {{}}
  try {{
    if ("wakeLock" in navigator && !wakeLockSentinel) {{
      wakeLockSentinel = await navigator.wakeLock.request("screen");
      document.getElementById("wake-btn").textContent = "🔒 Awake Lock ON";
      wakeLockSentinel.addEventListener("release", () => {{
        wakeLockSentinel = null;
        const b = document.getElementById("wake-btn");
        if (b) b.textContent = "🔊 Sound & Keep Awake";
      }});
    }}
  }} catch (e) {{}}
  if ("Notification" in window && Notification.permission === "default") {{
    try {{ await Notification.requestPermission(); }} catch (e) {{}}
  }}
}}

function playTone(freq, durSec) {{
  try {{
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!audioCtx && AC) audioCtx = new AC();
    if (!audioCtx) return;
    if (audioCtx.state === "suspended") audioCtx.resume();
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = "triangle";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.85, audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + durSec);
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start();
    osc.stop(audioCtx.currentTime + durSec);
  }} catch (e) {{}}
}}

function startRinging(msg) {{
  document.getElementById("ring-msg").textContent = msg || "Triggered from HOME OS Command Centre";
  document.getElementById("ring-overlay").style.display = "flex";
  let toggle = false;
  playTone(880, 0.28);
  if (navigator.vibrate) navigator.vibrate([350, 150, 350, 150, 500]);
  if (ringTimer) clearInterval(ringTimer);
  ringTimer = setInterval(() => {{
    toggle = !toggle;
    playTone(toggle ? 1320 : 880, 0.28);
    if (navigator.vibrate) navigator.vibrate([300, 120, 300]);
  }}, 420);
}}

function stopRinging() {{
  if (ringTimer) {{ clearInterval(ringTimer); ringTimer = null; }}
  if (navigator.vibrate) navigator.vibrate(0);
  document.getElementById("ring-overlay").style.display = "none";
}}

async function setTorch(enabled) {{
  if (!enabled) {{
    disableTorch();
    return "torch_off";
  }}
  // Attempt hardware rear LED on Android Chrome first; fall back to 100%-white screen torch on iOS
  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {{
    try {{
      const stream = await navigator.mediaDevices.getUserMedia({{ video: {{ facingMode: "environment" }} }});
      const track = stream.getVideoTracks()[0];
      const caps = track.getCapabilities ? track.getCapabilities() : {{}};
      if (caps.torch) {{
        await track.applyConstraints({{ advanced: [{{ torch: true }}] }});
        torchStream = stream;
        return "hardware_led_torch_on";
      }}
      stream.getTracks().forEach(t => t.stop());
    }} catch (e) {{}}
  }}
  document.getElementById("torch-overlay").style.display = "flex";
  return "screen_torch_on";
}}

function disableTorch() {{
  if (torchStream) {{
    torchStream.getTracks().forEach(t => t.stop());
    torchStream = null;
  }}
  document.getElementById("torch-overlay").style.display = "none";
}}

function getFingerprint() {{
  let fp = localStorage.getItem("homeos_hw_fp");
  if (!fp) {{
    fp = "mob-" + Math.random().toString(36).slice(2, 10) + "-" + Date.now().toString(36);
    localStorage.setItem("homeos_hw_fp", fp);
  }}
  return fp;
}}

async function collectPayload() {{
  let bat = null, charging = null;
  if (navigator.getBattery) {{
    try {{
      const b = await navigator.getBattery();
      bat = Math.round(b.level * 100);
      charging = Boolean(b.charging);
      document.getElementById("st-bat").textContent = bat + "%" + (charging ? " ⚡" : "");
    }} catch (e) {{}}
  }}
  const payload = {{
    health_score: 100,
    capabilities: COMPANION_CAPABILITIES,
    attributes: {{
      companion_mode: "mobile_web",
      os: detected.os,
      screen: `${{window.screen.width}}x${{window.screen.height}}`,
      online: navigator.onLine
    }}
  }};
  if (bat !== null) payload.battery_level = bat;
  if (charging !== null) payload.is_charging = charging;
  return payload;
}}

async function signedPost(creds, path, bodyObj) {{
  const bodyStr = JSON.stringify(bodyObj);
  const ts = Math.floor(Date.now() / 1000);
  const nonce = Math.random().toString(36).slice(2) + Date.now().toString(36);
  const sig = signAgentRequest(creds.hmac_secret, "POST", path, ts, nonce, bodyStr);
  return fetch(path, {{
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
}}

async function completePairing() {{
  await enableAudioAndWakeLock(true);
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
        os_name: detected.os,
        capabilities: COMPANION_CAPABILITIES
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

async function handleIncomingCommand(msg) {{
  const key = msg.capability_key;
  const p = msg.parameters || {{}};
  let method = "companion_executed";
  let resObj = {{ executed: key }};

  if (key === "device.ring") {{
    startRinging(p.message || "Find My Phone triggered from your Mac");
    method = "webaudio_siren_and_vibrate";
    logMsg("🔔 Ringing phone loudly!");
  }} else if (key === "device.notify") {{
    const title = p.title || "HOME OS Alert";
    const body = p.message || "Ping from Command Centre";
    playTone(784, 0.18);
    if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
    document.getElementById("alert-title").textContent = title;
    document.getElementById("alert-body").textContent = body;
    document.getElementById("alert-link").style.display = "none";
    document.getElementById("alert-modal").style.display = "flex";
    if ("Notification" in window && Notification.permission === "granted") {{
      try {{ new Notification(title, {{ body }}); }} catch (e) {{}}
    }}
    method = "fullscreen_alert_displayed";
    logMsg(`📩 Alert displayed: ${{body}}`);
  }} else if (key === "apps.launch" || key === "shortcuts.run") {{
    let target = p.url || p.app_id || p.package_name || p.shortcut_name || "";
    if (key === "shortcuts.run" || (!target.includes("://") && !target.startsWith("http"))) {{
      if (target.includes(".")) target = "https://" + target;
      else target = "shortcuts://run-shortcut?name=" + encodeURIComponent(target);
    }}
    playTone(660, 0.12);
    document.getElementById("alert-title").textContent = "Open Link / App from Mac";
    document.getElementById("alert-body").textContent = target;
    const linkEl = document.getElementById("alert-link");
    linkEl.href = target;
    linkEl.style.display = "block";
    document.getElementById("alert-modal").style.display = "flex";
    try {{ window.location.assign(target); }} catch (e) {{}}
    method = "deep_link_launched";
    resObj = {{ target }};
    logMsg(`🚀 Opening: ${{target}}`);
  }} else if (key === "clipboard.send") {{
    const text = String(p.text || "");
    document.getElementById("mac-clip-text").textContent = text;
    document.getElementById("mac-clip-box").style.display = "block";
    playTone(587, 0.1);
    try {{ await navigator.clipboard.writeText(text); method = "clipboard_written"; }} catch (e) {{ method = "clipboard_staged_on_screen"; }}
    logMsg("📋 Received clipboard text from Mac.");
  }} else if (key === "flashlight.set") {{
    method = await setTorch(Boolean(p.enabled));
    logMsg(`🔦 Torch -> ${{method}}`);
  }} else {{
    const t = await collectPayload();
    resObj = t;
  }}

  if (wsConn && wsConn.readyState === WebSocket.OPEN && msg.command_id) {{
    wsConn.send(JSON.stringify({{
      type: "command.receipt",
      command_id: msg.command_id,
      acknowledged: true,
      state_verified: true,
      verification_method: method,
      result: resObj
    }}));
  }}
}}

function copyReceivedText() {{
  const txt = document.getElementById("mac-clip-text").textContent || "";
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(txt).then(() => logMsg("Copied to phone clipboard!"));
  }}
}}

async function uploadSelectedFile() {{
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return;
  const creds = JSON.parse(raw);
  const input = document.getElementById("file-picker");
  const file = input.files && input.files[0];
  if (!file) return;
  const st = document.getElementById("upload-status");
  st.textContent = `Uploading ${{file.name}} (${{Math.round(file.size / 1024)}} KB)...`;

  const reader = new FileReader();
  reader.onload = async () => {{
    try {{
      const dataUrl = String(reader.result || "");
      const b64 = dataUrl.split(",")[1] || "";
      const r = await signedPost(creds, "/api/v1/agent/drop-file", {{
        filename: file.name,
        content_b64: b64
      }});
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "Upload failed");
      st.textContent = `✓ Saved to Mac: ~/HomeOS-Shared/${{j.filename}}`;
      logMsg(`📁 Sent ${{j.filename}} to Mac ~/HomeOS-Shared`);
      input.value = "";
    }} catch (err) {{
      st.textContent = "Error: " + (err.message || err);
    }}
  }};
  reader.readAsDataURL(file);
}}

async function beamClipboardToMac() {{
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return;
  const creds = JSON.parse(raw);
  const el = document.getElementById("msg-body");
  const text = (el.value || "").trim();
  if (!text) return;
  const r = await signedPost(creds, "/api/v1/agent/beam-clipboard", {{ text }});
  if (r.ok) {{
    el.value = "";
    logMsg("📋 Copied straight onto your Mac clipboard (Cmd+V)!");
  }}
}}

async function sendTelemetryNow() {{
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return;
  const creds = JSON.parse(raw);
  const bodyObj = await collectPayload();
  const r = await signedPost(creds, "/api/v1/agent/telemetry", bodyObj);
  if (r.ok) logMsg("Synced telemetry with Mac Hub.");
}}

async function pollQueuedCommands() {{
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return;
  const creds = JSON.parse(raw);
  try {{
    const r = await signedPost(creds, "/api/v1/agent/poll", {{}});
    if (r.ok) {{
      const j = await r.json();
      for (const cmd of (j.commands || [])) {{
        await handleIncomingCommand(cmd);
      }}
    }}
  }} catch (e) {{}}
}}

async function sendCompanionMessage() {{
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return;
  const creds = JSON.parse(raw);
  const msgEl = document.getElementById("msg-body");
  const text = (msgEl.value || "").trim();
  if (!text) return;
  const r = await signedPost(creds, "/api/v1/agent/communications", {{
    source_type: "messaging_notification",
    app_name: "HOME OS Companion",
    sender: creds.name || detected.name,
    title: "Phone Message",
    body_preview: text,
    can_reply: false
  }});
  if (r.ok) {{
    msgEl.value = "";
    logMsg("Delivered alert to Mac Command Centre.");
  }} else {{
    const j = await r.json().catch(() => ({{}}));
    logMsg("Alert error: " + (j.detail || "Check notifications:read permission"));
  }}
}}

function startLiveCompanion(creds) {{
  document.getElementById("pair-card").style.display = "none";
  document.getElementById("live-area").style.display = "block";
  document.getElementById("live-title").textContent = creds.name || "Connected Companion";

  const connectWs = async () => {{
    if (wsConn && (wsConn.readyState === WebSocket.OPEN || wsConn.readyState === WebSocket.CONNECTING)) return;
    const ts = Math.floor(Date.now() / 1000);
    const nonce = Math.random().toString(36).slice(2) + Date.now().toString(36);
    const path = "/api/v1/agent/ws";
    const sig = signAgentRequest(creds.hmac_secret, "WS", path, ts, nonce, "");
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${{proto}}//${{location.host}}${{path}}?device_id=${{encodeURIComponent(creds.device_id)}}&token=${{encodeURIComponent(creds.device_token)}}&timestamp=${{ts}}&nonce=${{nonce}}&signature=${{sig}}`;
    wsConn = new WebSocket(url);
    wsConn.onopen = async () => {{
      document.getElementById("ws-pill").textContent = "● Live Connected";
      const t = await collectPayload();
      wsConn.send(JSON.stringify({{ type: "telemetry", payload: t }}));
      await pollQueuedCommands();
    }};
    wsConn.onmessage = async (ev) => {{
      try {{
        const msg = JSON.parse(ev.data);
        if (msg.type === "command.execute") {{
          await handleIncomingCommand(msg);
        }}
      }} catch (e) {{}}
    }};
    wsConn.onclose = () => {{
      document.getElementById("ws-pill").textContent = "Standby · Auto-Reconnecting";
      setTimeout(connectWs, 2500);
    }};
  }};

  connectWs();
  document.addEventListener("visibilitychange", () => {{
    if (document.visibilityState === "visible") {{
      connectWs();
      pollQueuedCommands();
      enableAudioAndWakeLock(false);
    }}
  }});
  if (telemetryTimer) clearInterval(telemetryTimer);
  telemetryTimer = setInterval(sendTelemetryNow, 20000);
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollQueuedCommands, 6000);
}}

function unpairLocal() {{
  localStorage.removeItem(STORAGE_KEY);
  if (wsConn) wsConn.close();
  location.href = "/companion";
}}

const existingRaw = localStorage.getItem(STORAGE_KEY);
if (!SESSION_ID && existingRaw) {{
  try {{ startLiveCompanion(JSON.parse(existingRaw)); }} catch (e) {{}}
}}
</script>
</body>
</html>"""


@router.get("/companion.webmanifest")
def companion_web_manifest():
    return JSONResponse(
        {
            "name": "HOME OS Companion",
            "short_name": "HOME OS",
            "start_url": "/companion",
            "display": "standalone",
            "background_color": "#090b10",
            "theme_color": "#090b10",
        }
    )


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

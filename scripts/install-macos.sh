#!/usr/bin/env bash
unset CDPATH
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
INSTALL_DIR="${HOME}/.local/share/home-os"
VENV_DIR="${INSTALL_DIR}/venv"
BIN_DIR="${HOME}/.local/bin"
MODE="${1:-}"

echo "============================================================"
echo "  HOME OS v1.0.0 — macOS Installer (Core + UI + Mac Agent)"
echo "============================================================"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Note: This script is tailored for macOS (Darwin). For Linux, run scripts/install-local.sh."
fi

# 1. Check prerequisites (Xcode Command Line Tools, Python 3.11+, Node 20+)
if ! command -v swift >/dev/null 2>&1; then
  echo "Error: Swift toolchain not found. Install Xcode Command Line Tools first:"
  echo "  xcode-select --install"
  exit 1
fi

PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "${candidate}" >/dev/null 2>&1; then
    if "${candidate}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
      PYTHON_BIN="$(command -v "${candidate}")"
      break
    fi
  fi
done

if [[ -z "${PYTHON_BIN}" ]]; then
  echo "Error: Python 3.11+ is required."
  echo "Install via Homebrew:  brew install python@3.12"
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "Error: Node.js / npm is required to build the Desktop Command Environment."
  echo "Install via Homebrew:  brew install node"
  exit 1
fi

mkdir -p "${INSTALL_DIR}" "${BIN_DIR}" "${HOME}/HomeOS-Shared"

# Gracefully close running HOME OS app & stale port-8420 hub before updating
if [[ "$(uname -s)" == "Darwin" ]]; then
  osascript -e 'quit app "HOME OS"' >/dev/null 2>&1 || true
  pkill -x "HOME OS" >/dev/null 2>&1 || true
fi
if command -v lsof >/dev/null 2>&1; then
  STALE_PIDS="$(/usr/sbin/lsof -ti tcp:8420 2>/dev/null || lsof -ti tcp:8420 2>/dev/null || true)"
  if [[ -n "${STALE_PIDS}" ]]; then
    echo "Stopping previous HOME OS server on port 8420 (${STALE_PIDS})..."
    kill -TERM ${STALE_PIDS} 2>/dev/null || true
    sleep 0.5
    REMAINING_PIDS="$(/usr/sbin/lsof -ti tcp:8420 2>/dev/null || lsof -ti tcp:8420 2>/dev/null || true)"
    if [[ -n "${REMAINING_PIDS}" ]]; then
      kill -9 ${REMAINING_PIDS} 2>/dev/null || true
    fi
  fi
fi

# 2. Install Core Hub Engine
echo ""
echo "[1/4] Installing HOME OS Core Engine into ${VENV_DIR}..."
"${PYTHON_BIN}" -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --upgrade pip -q
if compgen -G "${ROOT_DIR}/releases/home_os-*.whl" > /dev/null; then
  "${VENV_DIR}/bin/pip" install -q "${ROOT_DIR}"/releases/home_os-*.whl
  "${VENV_DIR}/bin/pip" install -q --force-reinstall --no-deps "${ROOT_DIR}"/releases/home_os-*.whl
else
  "${VENV_DIR}/bin/pip" install --upgrade "${ROOT_DIR}"
fi
ln -sf "${VENV_DIR}/bin/homeos" "${BIN_DIR}/homeos"

# 3. Build Desktop Command Environment (Web SPA + native macOS .app / .dmg)
echo ""
echo "[2/4] Building HOME OS Desktop Command Environment..."
(
  cd "${ROOT_DIR}/desktop"
  npm install --no-audit --no-fund

  # Compile TypeScript & build Vite directly into local ~/.local/share/home-os/ui-dist
  # (avoids macOS iCloud Drive Desktop/Documents eviction races on relative ./dist)
  ./node_modules/.bin/tsc -b
  rm -rf "${INSTALL_DIR}/ui-dist"
  mkdir -p "${INSTALL_DIR}/ui-dist"
  ./node_modules/.bin/vite build "${ROOT_DIR}/desktop" --outDir "${INSTALL_DIR}/ui-dist" --emptyOutDir

  if [[ ! -f "${INSTALL_DIR}/ui-dist/index.html" ]] && [[ -f "${ROOT_DIR}/releases/home-os-desktop-bundle-1.0.0.tar.gz" ]]; then
    TMP_BUNDLE="$(mktemp -d)"
    tar -xzf "${ROOT_DIR}/releases/home-os-desktop-bundle-1.0.0.tar.gz" -C "${TMP_BUNDLE}"
    tar -cf - -C "${TMP_BUNDLE}/dist" . | tar -xf - -C "${INSTALL_DIR}/ui-dist"
    rm -rf "${TMP_BUNDLE}"
  fi

  # Mirror fresh UI build into desktop/dist for Electron / electron-builder
  rm -rf "${ROOT_DIR}/desktop/dist"
  mkdir -p "${ROOT_DIR}/desktop/dist"
  tar -cf - -C "${INSTALL_DIR}/ui-dist" . | tar -xf - -C "${ROOT_DIR}/desktop/dist"

  # Compile Electron main & preload CommonJS bundle
  ./node_modules/.bin/tsc -p "${ROOT_DIR}/desktop/tsconfig.electron.json"
  node -e 'import("fs").then(f => f.writeFileSync(process.argv[1], JSON.stringify({type:"commonjs"}, null, 2)))' "${ROOT_DIR}/desktop/dist-electron/package.json"

  # Build / refresh HOME OS.app automatically if --build-dmg, --app, or /Applications/HOME OS.app exists
  if [[ "${MODE}" == "--build-dmg" ]] || [[ "${MODE}" == "--app" ]] || [[ -d "/Applications/HOME OS.app" ]]; then
    if [[ ! -x "./node_modules/.bin/electron-builder" ]]; then
      echo "Installing Electron packager tools..."
      npm install --no-save --no-audit --no-fund electron@^33.2.0 electron-builder@^25.1.8
    fi
    rm -rf "${ROOT_DIR}/desktop/release-builds/mac"*
    if [[ "${MODE}" == "--build-dmg" ]]; then
      echo "Packaging native macOS Electron .dmg & .app..."
      CSC_IDENTITY_AUTO_DISCOVERY=false ./node_modules/.bin/electron-builder --mac dmg zip --publish never
    else
      echo "Updating native macOS HOME OS.app bundle..."
      CSC_IDENTITY_AUTO_DISCOVERY=false ./node_modules/.bin/electron-builder --mac dir --publish never
    fi

    BUILT_APP=""
    for candidate_app in \
      "${ROOT_DIR}/desktop/release-builds/mac-arm64/HOME OS.app" \
      "${ROOT_DIR}/desktop/release-builds/mac/HOME OS.app" \
      "${ROOT_DIR}/desktop/release-builds/mac-universal/HOME OS.app"; do
      if [[ -d "${candidate_app}" ]]; then
        BUILT_APP="${candidate_app}"
        break
      fi
    done

    if [[ -n "${BUILT_APP}" ]]; then
      if command -v xattr >/dev/null 2>&1; then
        xattr -cr "${BUILT_APP}" 2>/dev/null || true
      fi
      if command -v codesign >/dev/null 2>&1; then
        codesign --force --deep --entitlements "${ROOT_DIR}/desktop/entitlements.mac.plist" --sign - "${BUILT_APP}" 2>/dev/null || true
      fi
      if [[ "$(uname -s)" == "Darwin" ]] && [[ -w "/Applications" ]]; then
        rm -rf "/Applications/HOME OS.app"
        /usr/bin/ditto "${BUILT_APP}" "/Applications/HOME OS.app"
        xattr -cr "/Applications/HOME OS.app" 2>/dev/null || true
        codesign --force --deep --entitlements "${ROOT_DIR}/desktop/entitlements.mac.plist" --sign - "/Applications/HOME OS.app" 2>/dev/null || true
        echo "Installed updated bundle to: /Applications/HOME OS.app"
      else
        echo "macOS app packaged in: ${BUILT_APP}"
      fi
    fi
  fi
)

# 4. Compile Swift Native macOS Agent (Keychain + TCC + CoreAudio + File Jail)
echo ""
echo "[3/4] Compiling Swift Native macOS Agent (homeos-mac-agent)..."
(
  cd "${ROOT_DIR}/agents/macos"
  swift build -c release
  cp .build/release/homeos-mac-agent "${BIN_DIR}/homeos-mac-agent"
)

echo ""
echo "[4/4] Verifying installed binaries..."
"${BIN_DIR}/homeos" version
"${BIN_DIR}/homeos-mac-agent" status || true

echo ""
echo "============================================================"
echo "  HOME OS Installed Successfully on macOS!"
echo "============================================================"
if [[ -d "/Applications/HOME OS.app" ]]; then
  echo "Launch the updated macOS app:"
  echo "  open \"/Applications/HOME OS.app\""
  echo ""
fi
echo "Or start the HOME OS Hub & Web Console from Terminal (accessible on Mac + Wi-Fi LAN):"
echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
echo "  homeos serve --host 0.0.0.0 --port 8420"
echo "  Then open: http://127.0.0.1:8420"
echo "============================================================"

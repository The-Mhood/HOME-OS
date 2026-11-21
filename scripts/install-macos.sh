#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${HOME}/.local/share/home-os"
VENV_DIR="${INSTALL_DIR}/venv"
BIN_DIR="${HOME}/.local/bin"

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

# 2. Install Core Hub Engine
echo ""
echo "[1/4] Installing HOME OS Core Engine into ${VENV_DIR}..."
"${PYTHON_BIN}" -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --upgrade pip -q
if compgen -G "${ROOT_DIR}/releases/home_os-*.whl" > /dev/null; then
  "${VENV_DIR}/bin/pip" install --force-reinstall "${ROOT_DIR}"/releases/home_os-*.whl
else
  "${VENV_DIR}/bin/pip" install "${ROOT_DIR}"
fi
ln -sf "${VENV_DIR}/bin/homeos" "${BIN_DIR}/homeos"

# 3. Build Desktop Command Environment (Web SPA + optional native macOS .dmg/.app)
echo ""
echo "[2/4] Building HOME OS Desktop Command Environment..."
(
  cd "${ROOT_DIR}/desktop"
  npm install
  npm run build
  rm -rf "${INSTALL_DIR}/ui-dist"
  cp -R dist "${INSTALL_DIR}/ui-dist"
  if [[ "${1:-}" == "--build-dmg" ]]; then
    echo "Packaging native macOS Electron .dmg & .app..."
    npm install --no-save electron@^33.2.0 electron-builder@^25.1.8
    CSC_IDENTITY_AUTO_DISCOVERY=false npx electron-builder --mac dmg zip --publish never
    if command -v xattr >/dev/null 2>&1; then
      xattr -cr release-builds/mac*/"HOME OS.app" 2>/dev/null || true
    fi
    echo "macOS app packaged in: ${ROOT_DIR}/desktop/release-builds/"
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
echo "Ensure ~/.local/bin is on your PATH:"
echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
echo ""
echo "Start the HOME OS Hub & Web/Desktop Console (accessible on this Mac + your Wi-Fi LAN):"
echo "  homeos serve --host 0.0.0.0 --port 8420"
echo "  Then open: http://127.0.0.1:8420"
echo ""
echo "(Or launch the native Electron window during development):"
echo "  cd ${ROOT_DIR}/desktop && npx electron@^33.2.0 ."
echo "============================================================"

#!/usr/bin/env bash
# Installs HOME OS from the CURRENT source tree.
#
# Historically this script silently installed stale wheels from releases/ (which
# were built once and do not reflect the active branch). The default path now
# always builds from source. If you explicitly want the committed release
# artifacts for downstream verification, pass --use-release-artifacts.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_VENV="${HOME}/.local/share/home-os/venv"

USE_RELEASE_ARTIFACTS=0
for arg in "$@"; do
  if [[ "${arg}" == "--use-release-artifacts" ]]; then
    USE_RELEASE_ARTIFACTS=1
  fi
done

echo "Installing HOME OS (source) into ${INSTALL_VENV}..."
mkdir -p "$(dirname "${INSTALL_VENV}")"
python3 -m venv "${INSTALL_VENV}"
"${INSTALL_VENV}/bin/pip" install --upgrade pip

if [[ "${USE_RELEASE_ARTIFACTS}" == "1" ]] && compgen -G "${ROOT_DIR}/releases/home_os-*.whl" > /dev/null; then
  echo "Installing committed release artifacts (verification mode)..."
  "${INSTALL_VENV}/bin/pip" install --force-reinstall \
    "${ROOT_DIR}"/releases/home_os-*.whl \
    "${ROOT_DIR}"/releases/homeos_linux_agent-*.whl
else
  echo "Building and installing from source tree..."
  "${INSTALL_VENV}/bin/pip" install "${ROOT_DIR}" "${ROOT_DIR}/agents/linux"
fi

# Install the compiled Desktop SPA alongside the hub so `homeos serve` can
# serve the UI at http://127.0.0.1:8420/ (candidate dist dirs in backend/home_os/main.py).
UI_DIST="${HOME}/.local/share/home-os/ui-dist"
if [[ -f "${ROOT_DIR}/desktop/dist/index.html" ]]; then
  mkdir -p "${UI_DIST}"
  cp -R "${ROOT_DIR}/desktop/dist/." "${UI_DIST}/"
  echo "Installed desktop SPA into ${UI_DIST}"
elif [[ -x "${INSTALL_VENV}/bin/node" ]] || command -v npm >/dev/null 2>&1; then
  echo "Note: desktop/dist not built — building from source..."
  (cd "${ROOT_DIR}/desktop" && npm ci && npm run build)
  mkdir -p "${UI_DIST}"
  cp -R "${ROOT_DIR}/desktop/dist/." "${UI_DIST}/"
  echo "Installed desktop SPA into ${UI_DIST}"
else
  echo "Note: desktop/dist not built and npm unavailable — install the desktop UI separately (see docs/05-installation-and-setup.md)."
fi

mkdir -p "${HOME}/.local/bin"
ln -sf "${INSTALL_VENV}/bin/homeos" "${HOME}/.local/bin/homeos"
ln -sf "${INSTALL_VENV}/bin/homeos-linux-agent" "${HOME}/.local/bin/homeos-linux-agent"

echo "Installed successfully!"
echo "  Core CLI:        ${HOME}/.local/bin/homeos --help"
echo "  Linux Agent CLI: ${HOME}/.local/bin/homeos-linux-agent --help"

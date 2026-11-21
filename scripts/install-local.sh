#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_VENV="${HOME}/.local/share/home-os/venv"

echo "Installing HOME OS v1.0.0 into ${INSTALL_VENV}..."
mkdir -p "$(dirname "${INSTALL_VENV}")"
python3 -m venv "${INSTALL_VENV}"
"${INSTALL_VENV}/bin/pip" install --upgrade pip

if compgen -G "${ROOT_DIR}/releases/home_os-*.whl" > /dev/null; then
  "${INSTALL_VENV}/bin/pip" install --force-reinstall \
    "${ROOT_DIR}"/releases/home_os-*.whl \
    "${ROOT_DIR}"/releases/homeos_linux_agent-*.whl
else
  "${INSTALL_VENV}/bin/pip" install "${ROOT_DIR}" "${ROOT_DIR}/agents/linux"
fi

mkdir -p "${HOME}/.local/bin"
ln -sf "${INSTALL_VENV}/bin/homeos" "${HOME}/.local/bin/homeos"
ln -sf "${INSTALL_VENV}/bin/homeos-linux-agent" "${HOME}/.local/bin/homeos-linux-agent"

echo "Installed successfully!"
echo "  Core CLI:        ${HOME}/.local/bin/homeos --help"
echo "  Linux Agent CLI: ${HOME}/.local/bin/homeos-linux-agent --help"

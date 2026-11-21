#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASE_DIR="${ROOT_DIR}/releases"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

rm -rf "${RELEASE_DIR}"
mkdir -p "${RELEASE_DIR}"

echo "[1/4] Building HOME OS Core Engine wheel & sdist..."
"${PYTHON_BIN}" -m build --outdir "${RELEASE_DIR}" "${ROOT_DIR}"
rm -rf "${ROOT_DIR}/build" "${ROOT_DIR}/backend/home_os.egg-info"

echo "[2/4] Building HOME OS Linux Native Agent wheel & sdist..."
"${PYTHON_BIN}" -m build --outdir "${RELEASE_DIR}" "${ROOT_DIR}/agents/linux"
rm -rf "${ROOT_DIR}/agents/linux/build" "${ROOT_DIR}/agents/linux/src/homeos_linux_agent.egg-info"

echo "[3/4] Building HOME OS Desktop Production Bundle..."
if [[ -d "${ROOT_DIR}/desktop/node_modules" ]]; then
  (cd "${ROOT_DIR}/desktop" && npm run build)
  tar -czf "${RELEASE_DIR}/home-os-desktop-bundle-1.0.0.tar.gz" \
    -C "${ROOT_DIR}/desktop" dist dist-electron package.json electron-builder.yml entitlements.mac.plist
fi

echo "[4/4] Generating cryptographic SHA-256 release manifest..."
(
  cd "${RELEASE_DIR}"
  sha256sum *.whl *.tar.gz > SHA256SUMS.txt
)

"${PYTHON_BIN}" - <<'PY'
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

rel_dir = Path("releases")
artifacts = []
for p in sorted(rel_dir.iterdir()):
    if p.name in {"manifest.json", "SHA256SUMS.txt"} or not p.is_file():
        continue
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    artifacts.append(
        {
            "filename": p.name,
            "size_bytes": p.stat().st_size,
            "sha256": digest,
        }
    )

manifest = {
    "product": "HOME OS",
    "version": "1.0.0",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "artifacts": artifacts,
}
(rel_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(f"Wrote releases/manifest.json ({len(artifacts)} verified artifacts)")
PY

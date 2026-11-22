# 09 — Home-Alpha Release Checklist (verified 2026-11-22)

This is the release gate for the intended home-alpha. Every row marked **VERIFIED** was exercised on the active branch `arena/7bd92795-home-os` (commit `7359f0b` + release rebuilds) on Debian 12 x86_64 / Python 3.11.2.

## 1. Artifact integrity

| Artifact | SHA-256 (manifest) | Verified |
|---|---|---|
| `home_os-1.0.0-py3-none-any.whl` | `53aed038…` | yes (checksums re-checked) |
| `home_os-1.0.0.tar.gz` | `43b4009a…` | yes |
| `homeos_linux_agent-1.0.0-py3-none-any.whl` | `15e7aef0…` | yes |
| `homeos_linux_agent-1.0.0.tar.gz` | `783917a0…` | yes |
| `home-os-desktop-bundle-1.0.0.tar.gz` | `cc699f35…` | yes |

- Manifest and `SHA256SUMS.txt` regenerate from the current source (`bash scripts/build-release.sh`).
- The rebuilt wheels carry the 2026-11-22 fixes (verified by inspektion of installed artifacts, not just hashes).

## 2. Clean install from artifacts

- `bash scripts/install-local.sh --use-release-artifacts` → installs 2 wheels + dependencies into `~/.local/share/home-os/venv`, links into `~/.local/bin/`
- `homeos migrate` → `0001_initial_schema` applied
- `homeos serve --host 127.0.0.1 --port 18423` → `GET /api/v1/health` returns **200** (`{"status":"ok","version":"1.0.0",...}`)
- `GET /` serves the compiled SPA (HTTP 200, text/html)
- `homeos verify-audit` → `{"valid": true, "checked_entries": 1, "head_hash": "77b148…"}`

## 3. Upgrade path

- Reinstall from the same release artifacts (idempotent) → `~/.local/share/home-os/venv` updated
- Rerun `homeos migrate` → no-op (`0001_initial_schema` already at head) → `Database migrated successfully`

## 4. Regression gates (re-run 2026-11-22)

| Gate | Command | Result |
|---|---|---|
| Backend + security + perf | `.venv/bin/pytest` | **27/27 passed** |
| Desktop UI | `cd desktop && npm test` | **2/2 passed** |
| Desktop build | `npm run build` | **clean** (tsc + vite + electron tsc) |
| Alembic round-trip | `alembic upgrade head` → `downgrade base` → `upgrade head` | **OK** (env.py now honors `HOMEOS_DATABASE_URL`) |
| Release packaging | `bash scripts/build-release.sh` | **OK** (5 artifacts) |

## 5. Known limitations (not blocking the home-alpha gates, must be documented at install)

- Physical-device evidence is not available in the build sandbox: Home Assistant, Google TV, Android, iOS, macOS/Windows native agents are source-verified + mock/simulated only. Do not claim hardware control until deploy-time verification is recorded.
- `releases/` wheels are built by CI per push; the committed copies are regenerated locally and re-checked.
- Version-label inconsistency carried from the handoff: `pyproject.toml`/manifest/`health.version` = `1.0.0`; handoff docs and `health.api_revision` = `1.4.0`. `api_revision` is load-bearing for the Electron stale-instance guard (`desktop/electron/main.ts`) — do not change without a release decision.
- Unpaired device staleness (`stale` badge) requires a discovery scan in progress to appear; agent-connected enrolled devices follow the WS state machine, not the unpaired staleness pass.

## 6. Release gates to close before any external publication

- [ ] Owner accepts the 1.0.0 vs 1.4.0 version-label policy (pending decision)
- [ ] Physical-device evidence attached for Android/iOS/TV/Home Assistant/Linux hosts
- [ ] CI green on the release-tagged commit
- [ ] No external publication without explicit owner approval

# 09 — Home-Alpha Release Checklist (verified 2026-11-22)

This is the release gate for the intended home-alpha. Every row marked **VERIFIED** was exercised on the active branch `arena/7bd92795-home-os` (2.0.0 artifacts rebuilt 2026-11-22) on Debian 12 x86_64 / Python 3.11.2.

## 1. Artifact integrity

| Artifact | SHA-256 (manifest) | Verified |
|---|---|---|
| `home_os-2.0.0-py3-none-any.whl` | `38aa3544…` | yes (checksums re-checked) |
| `home_os-2.0.0.tar.gz` | `4117467c…` | yes |
| `homeos_linux_agent-2.0.0-py3-none-any.whl` | `0368c39c…` | yes |
| `homeos_linux_agent-2.0.0.tar.gz` | `df326603…` | yes |
| `home-os-desktop-bundle-2.0.0.tar.gz` | `de231def…` | yes |

- Manifest and `SHA256SUMS.txt` regenerate from the current source (`bash scripts/build-release.sh`).
- The rebuilt wheels carry the 2026-11-22 fixes (verified by inspektion of installed artifacts, not just hashes).

## 2. Clean install from artifacts

- `bash scripts/install-local.sh --use-release-artifacts` → installs 2 wheels + dependencies into `~/.local/share/home-os/venv`, links into `~/.local/bin/`
- `homeos migrate` → `0001_initial_schema` applied
- `homeos serve --host 127.0.0.1 --port 18423` → `GET /api/v1/health` returns **200** (`{"status":"ok","version":"2.0.0","api_revision":"2.0.0",...}`)
- `GET /` serves the compiled SPA (HTTP 200, text/html)
- `homeos verify-audit` → `{"valid": true, "checked_entries": 1, "head_hash": "c213b362…"}`
- Desktop SPA installed into `~/.local/share/home-os/ui-dist` by the installer; `GET /` serves the SPA (200, text/html) and `GET /assets/*.js` serves the compiled bundle (200, text/javascript)

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
- **Resolved (2026-11-22, owner-directed override)**: the former `1.0.0`/`1.4.0` split was overridden to `2.0.0` across packages, health `version` + `api_revision`, desktop (`package.json`, `main.ts` platform-info + Electron stale-instance guard trusts only `2.0.0`), agents (Linux/macOS/Windows), companions (Android/iOS), scripts, and artifacts. The Electron stale-instance guard (`desktop/electron/main.ts`) now trusts only the current revision — stale `1.x` instances are replaced.
- Unpaired device staleness (`stale` badge) requires a discovery scan in progress to appear; agent-connected enrolled devices follow the WS state machine, not the unpaired staleness pass.

## 6. Release gates to close before any external publication

- [x] Version-label policy: owner directed the override to `2.0.0` (2026-11-22) — applied and rebuilt
- [ ] Physical-device evidence attached for Android/iOS/TV/Home Assistant/Linux hosts
- [ ] CI green on the release-tagged commit
- [ ] No external publication without explicit owner approval

## 7. CI status (recorded 2026-11-22, runs 45020053975 + 45020345845 on the macos-14→macos-15 fix commit)

Release-track gate — **not green**:

| Job | Result | Note |
|---|---|---|
| Core Engine & Linux Agent (Python 3.11) | pass (42s) | 27/27 + wheels built |
| Desktop UI & Typecheck | pass (18s) | vitest 2/2 + vite build |
| Windows Native Agent (.NET 8) | pass (1m4s) | self-contained win-x64 publish |
| macOS Native Agent & iOS Companion (Swift 5.9) | **fail** | `swift build -c release` step; runner label since fixed (macos-14→macos-15, macOS 14 retired 2026-11-02); job logs not downloadable from the build sandbox (results-receiver endpoint unreachable) |
| Android Companion (Kotlin / Compose) | **fail** | `gradle testPlayStoreDebugUnitTest assemblePlayStoreDebug assembleDirectCompanionDebug` step; logs not downloadable from the build sandbox |
| Electron Native Installers (matrix) | skipped | tag-gated (`if: startsWith(github.ref, 'refs/tags/v')`); also requires a green Release-Track gate |

The 2.0.0 version-override change set has not yet had a green CI pass (it is part of this release candidate); native-compiling targets are NOT verified on this commit.

Swift source statically audited (`agents/macos`): API usage checked against macOS 13+ (`ProcessInfo.thermalState.rawValue`, `NSWorkspace.openApplication`, Keychain/SecItemAdd patterns, `@main` struct with static async main). No certain defect found; actual build error — including the iOS `xcodegen`/`xcodebuild` step — is not visible in this sandbox. Treat these two jobs as **unverified, reported as red**, not as green. Honest closure requires the runner logs (owner-side GitHub access) or a self-hosted/local build evidence.

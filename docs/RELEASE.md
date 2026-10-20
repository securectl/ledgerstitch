# Releasing LedgerStitch (desktop builds)

LedgerStitch ships as a self-contained desktop app built with PyInstaller (onedir, windowed).
Users need no Python. Double-clicking the app starts the local server on 127.0.0.1 (free
port, fresh random token, data in `~/LedgerStitch`), opens the default browser and shows a
small window: "LedgerStitch is running - Open in browser - Quit". Quitting locks the vault.

| Piece | File |
|---|---|
| Desktop entry point | `ledgerstitch/desktop.py` (also `ledgerstitch-desktop` console script) |
| PyInstaller entry script | `packaging/launcher.py` (no args = desktop app; `import`/`export`/`vaults`/… = CLI) |
| PyInstaller spec | `packaging/ledgerstitch.spec` |
| Icon generator | `packaging/make_icon.py` (renders `logo-mark.svg` with Pillow; writes `.icns` + `.ico`) |
| Hardened-runtime entitlements | `packaging/entitlements.plist` |
| macOS build | `scripts/build_macos.sh` |
| Smoke test | `packaging/smoke_test.sh [app-or-dmg]` |
| Windows build (UNTESTED) | `scripts/build_windows.ps1` + `packaging/ledgerstitch.iss` (Inno Setup 6) |

Build outputs go to `dist/` and `build/` (both git-ignored). Builds use a dedicated
release venv (`.venv-release`), never the dev `.venv`.

---------------------------------------------------------------------------------------------

## 1. Versioning

The single source of truth is `ledgerstitch/__init__.py`:

    __version__ = "1.0.1"

Also bump `version` in `pyproject.toml` to match. The spec reads `__version__` for the
bundle's `CFBundleShortVersionString` / `CFBundleVersion` and the build scripts use it in the
DMG/installer filename. Use semantic versioning: patch = fixes, minor = features, major =
vault format changes (anything that a previous version can't open).

Release checklist before building:
1. `.venv/bin/python -m pytest` is green.
2. Version bumped in both places; CHANGELOG/release notes written.
3. Tag after the artifacts pass the smoke tests: `git tag v1.0.1 && git push --tags`.

## 2. Build (macOS)

Requirements: a Mac (Apple Silicon builds arm64; an Intel Mac builds x86_64), Xcode command
line tools (`xcode-select --install`, for `codesign`, `iconutil`, `hdiutil`), and `uv`
(`curl -LsSf https://astral.sh/uv/install.sh | sh`). Network is needed the first time (wheels).

    scripts/build_macos.sh

What it does:
1. Creates/updates `.venv-release` with `uv`, resolving **binary wheels compatible with
   macOS 12** (`MACOSX_DEPLOYMENT_TARGET=12.0`, `--python-platform`, `--only-binary :all:`).
   This matters: some of the newest wheels (for example pypdfium2) require macOS 13 or
   later, so a build from the dev `.venv` would silently crash on older Macs.
2. Cleans `build/` and `dist/`, renders the icon.
3. Runs PyInstaller with `packaging/ledgerstitch.spec`.
4. Fails the build if any bundled Mach-O needs a newer macOS than `MACOS_MIN`, or links a
   library outside the bundle.
5. Ad-hoc signs (`codesign --force --deep -s -`) - required for Apple Silicon to run it.
6. Runs `LedgerStitch --self-test` from the bundle (imports duckdb, cryptography/argon2
   vault round-trip, pdfplumber + pypdfium2 render, openpyxl, httpx, fastapi/uvicorn). Installed
   add-ons can add their own checks by providing a `selftest` service (docs/PLUGINS.md §2.2).
7. Builds `dist/LedgerStitch-<version>-macOS-<arch>.dmg` (app + Applications symlink, UDZO).
8. Writes `<dmg>.sha256` and prints sizes.

Options: `SKIP_SMOKE=1`, `MACOS_MIN=13.0` (raise the floor), `REFRESH_VENV=1` (rebuild the
release venv after dependency changes in `pyproject.toml`).

Then run the full headless smoke test against both the app and the DMG:

    packaging/smoke_test.sh dist/LedgerStitch.app
    packaging/smoke_test.sh dist/LedgerStitch-1.0.0-macOS-arm64.dmg

### Intel Macs / universal2

Build on each architecture separately and ship two DMGs (`-arm64`, `-x86_64`). A universal2
build is not practical: some wheels (for example duckdb) aren't published as universal2. On an Apple Silicon Mac you can build x86_64 under Rosetta with an x86_64 uv
Python: `arch -x86_64 /bin/bash scripts/build_macos.sh` (untested).

### What's bundled

Static UI (`ledgerstitch/web/static/*`, incl. `disclaimer.txt`), duckdb, cryptography,
argon2-cffi, pdfplumber + pdfminer.six + pypdfium2, openpyxl, fastapi/uvicorn/starlette/
pydantic/python-multipart, httpx, certifi, Tcl/Tk (for the control window). Excluded: pytest,
reportlab, sympy, matplotlib, Qt, etc. A Community build contains no add-ons; the build
script checks the bundle for that.

Check the app and DMG sizes the build prints, and note them in the release notes. duckdb
(~43 MB) is one of the biggest items.

## 3. Signing and notarization (macOS) - founder action required

Ad-hoc-signed builds run on the build Mac, but on any other Mac Gatekeeper says the app
"can't be opened because Apple cannot check it for malicious software". Testers can
right-click > Open (macOS 12-14) or, on macOS 15+, go to System Settings > Privacy & Security
> "Open Anyway", or run `xattr -dr com.apple.quarantine /Applications/LedgerStitch.app`.
Do not ship that to customers - sign and notarize.

One-time setup:
1. Enroll in the Apple Developer Program (developer.apple.com/programs, $99/yr; an
   organization enrollment needs a D-U-N-S number and takes days - start now).
2. Create a **Developer ID Application** certificate: Xcode > Settings > Accounts > (team) >
   Manage Certificates > + > Developer ID Application. Back up the private key (.p12) in a
   password manager.
3. Find the team ID (10 chars) at developer.apple.com/account > Membership.
4. Create an app-specific password at appleid.apple.com > Sign-In and Security.
5. Store notary credentials in the keychain:

       xcrun notarytool store-credentials ledgerstitch-notary \
           --apple-id "you@example.com" --team-id "TEAMID1234" --password "abcd-efgh-ijkl-mnop"

6. In `scripts/build_macos.sh`, uncomment sections **5b** (sign) and **7b** (notarize +
   staple) and set `IDENTITY="Developer ID Application: Your Name (TEAMID1234)"`.

What the real signing does: sign every nested `.so`/`.dylib`/executable inside-out with
`codesign --force --options runtime --timestamp --entitlements packaging/entitlements.plist`,
then the `.app`, then the DMG; then

    xcrun notarytool submit dist/LedgerStitch-<v>-macOS-arm64.dmg --keychain-profile ledgerstitch-notary --wait
    xcrun stapler staple dist/LedgerStitch-<v>-macOS-arm64.dmg
    spctl --assess --type open --context context:primary-signature -v dist/LedgerStitch-<v>-macOS-arm64.dmg

If notarization is rejected: `xcrun notarytool log <submission-id> --keychain-profile ledgerstitch-notary`
lists every offending file (typically an unsigned nested binary or a missing `--timestamp`).

Entitlements (`packaging/entitlements.plist`): `allow-jit` and
`allow-unsigned-executable-memory` (cffi callbacks need executable memory under the hardened
runtime) and `disable-library-validation` (safety net for third-party dylibs). After the first
notarized build is verified on a clean Mac, try removing `disable-library-validation` and
re-test vault unlock and PDF import.

CI later: export the certificate as .p12, store it + the notary API key (App Store Connect
API key is preferred over an Apple ID password: `notarytool store-credentials --key ... --key-id ... --issuer ...`)
as CI secrets, and import into a temporary keychain on a macOS runner.

## 4. Checksums

The build writes `dist/<dmg>.sha256`. Publish the hash next to the download link and in the
release notes. Users/support verify with:

    shasum -a 256 ~/Downloads/LedgerStitch-1.0.0-macOS-arm64.dmg

(Windows: `Get-FileHash -Algorithm SHA256 .\LedgerStitch-1.0.0-Windows-x64-Setup.exe`.)
Recompute after stapling - stapling changes the DMG, so the hash must be taken last
(the script already does this if you uncomment 7b, since checksumming is step 8).

## 5. Clean-Mac smoke-test checklist

Do this on a Mac (or a fresh macOS VM via UTM / a new user account) that has **no Python,
no Homebrew, no developer tools**, ideally the oldest supported macOS (12 Monterey) and the
newest. Download the DMG from the real distribution URL (so it gets the quarantine flag).

- [ ] `shasum -a 256` of the download matches the published hash.
- [ ] DMG opens, shows LedgerStitch + Applications; drag-install works.
- [ ] First launch from /Applications: no Gatekeeper warning (notarized build) - or the
      expected "unidentified developer" flow for ad-hoc test builds.
- [ ] Dock icon and app name are "LedgerStitch" with the green mark; About shows the version.
- [ ] Browser opens automatically to `http://127.0.0.1:<port>/`; the welcome screen loads
      with styles (not a bare HTML page).
- [ ] Control window shows "LedgerStitch is running"; "Open in browser" opens a tab.
- [ ] Create a vault, import a CSV and a PDF statement, export Excel - files open in Excel/Numbers.
- [ ] Settings > Privacy & AI: set up local AI and run a categorization (the Ollama download
      proves TLS/certifi in the bundle).
- [ ] Double-click the app again while running: no second copy; the browser opens to the
      same session. Clicking the Dock icon also opens the browser.
- [ ] Quit (window button, Cmd-Q, Dock > Quit): the process exits (Activity Monitor) and the
      browser tab reports the session is gone; relaunch and unlock works.
- [ ] `~/LedgerStitch` is mode 700, `~/LedgerStitch/ledgerstitch.lock` is 600 and empty after quit.
- [ ] `~/LedgerStitch/logs/desktop.log` contains only start/stop lines (no financial data).
- [ ] Turn Wi-Fi off and relaunch: everything still works (local AI too, once the model is downloaded).
- [ ] Startup feels instant (< 2 s to the browser tab).

Automated subset (on the build Mac): `packaging/smoke_test.sh <app-or-dmg>` and
`LedgerStitch.app/Contents/MacOS/LedgerStitch --self-test`.

## 6. Command-line flags of the desktop executable

    LedgerStitch.app/Contents/MacOS/LedgerStitch [--no-browser] [--no-window] [--port N]
                                                 [--data-dir DIR] [--self-test] [--version]
    LedgerStitch.app/Contents/MacOS/LedgerStitch import|export|vaults ...   # the CLI

`--no-window` runs headless until SIGTERM/SIGINT. If Tk ever fails to start, the app logs
it and continues headless (browser only); the user then quits via Dock > Quit or Activity
Monitor. The single-instance lock lives at `<data-dir>/ledgerstitch.lock` (pid, port,
token, url; mode 0600; held with `flock` for the process lifetime, so a crash frees it).

## 7. Windows (UNTESTED)

On Windows 10/11 x64 with uv and Inno Setup 6:

    powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1

Produces `dist\LedgerStitch\` (onedir) and `dist\LedgerStitch-<v>-Windows-x64-Setup.exe`
(per-user install, no admin; uninstaller never touches `%USERPROFILE%\LedgerStitch`).
Signing needs an OV/EV code-signing certificate (`signtool` lines are commented in both
files); unsigned installers trigger SmartScreen "Windows protected your PC". This path has
never been run - expect fixes on the first attempt (likely areas: Tcl/Tk data collection,
native DLLs, the msvcrt single-instance lock).

## 7a. Installers, Docker and CI (added 2026-10-19)

| Piece | File | Status |
|---|---|---|
| One-line installer, macOS/Linux | `install.sh` | tested on macOS (install, upgrade, uninstall) |
| One-line installer, Windows | `install.ps1` | UNTESTED (parsed only) |
| Inno Setup script | `packaging/ledgerstitch.iss` | UNTESTED: per-user, Start Menu + desktop shortcut, version info, "Launch" checkbox, uninstall keeps data (asks, default No) |
| DMG background + layout | `scripts/build_macos.sh` section 7a, `packaging/make_dmg_background.py` | falls back to the plain DMG without a Finder session; `DMG_FANCY=0` skips |
| CI signing hooks | `build_macos.sh` 5c/7c (`IDENTITY`, `NOTARIZE=1`), `build_windows.ps1` 5a | UNTESTED |
| Release workflow | `.github/workflows/release-community.yml` | UNTESTED; signs only when the secrets exist |
| Docker self-host | `docker/Dockerfile`, `docker/docker-compose.yml`, `docker/Caddyfile` | image built and run here; server-mode auth exercised end-to-end through Caddy |

User-facing steps live in `docs/INSTALL.md`.

## 8. Manual steps still open for the founder

1. Apple Developer Program enrollment + Developer ID Application certificate.
2. Notary credentials (`notarytool store-credentials`), then uncomment 5b/7b in the script.
3. First notarized build: run the clean-Mac checklist on macOS 12 and the latest macOS.
4. Decide on Intel Mac support (separate x86_64 build) - check analytics/customer base.
5. Windows: a Windows machine/VM to run the script once, plus a code-signing certificate.
6. Hosting for the DMG + published SHA-256 (and later an update check / Sparkle).

# Installing LedgerStitch (Community edition)

There are three ways to run LedgerStitch:

| | Who it's for | What you get |
|---|---|---|
| [Desktop app](#1-desktop-app-macos-and-windows) | Most people | A normal app. No Python, no terminal. |
| [One-line installer](#2-one-line-installer-from-source) | Developers, Linux users, people who want the newest code | LedgerStitch from source in its own isolated folder, plus a `ledgerstitch` command. |
| [Docker (self-hosted)](#3-self-host-with-docker) | A small office, or a home server | LedgerStitch on a server, with a sign-in page and HTTPS. |

All three keep your data in the same place: `~/LedgerStitch` (on Windows,
`%USERPROFILE%\LedgerStitch`), or the `/data` volume in Docker. Every workspace in it is
encrypted with its own passphrase.

On first launch a short **setup wizard** opens: privacy promise, a system check, an optional
local AI install, then your first encrypted vault. See [First-run setup](#4-first-run-setup).
Already installed? See [Updating](#6-updating): it changes only what changed.

---

## 1. Desktop app (macOS and Windows)

### macOS

1. Download `LedgerStitch-<version>-macOS-arm64.dmg` (Apple silicon) or `-x86_64.dmg`
   (Intel Macs).
2. Check the download (optional):

       shasum -a 256 ~/Downloads/LedgerStitch-*.dmg

   Compare it with the SHA-256 published next to the download.
3. Open the DMG and drag **LedgerStitch** onto **Applications**.
4. Open LedgerStitch from Applications. Your browser opens at `http://127.0.0.1:<port>/`.
   That address is your own computer. Nothing is on the internet.

### Windows

1. Download `LedgerStitch-<version>-Windows-x64-Setup.exe`.
2. Check the download (optional), in PowerShell:

       Get-FileHash -Algorithm SHA256 $HOME\Downloads\LedgerStitch-*-Setup.exe

3. Run the installer. It installs for your user only and doesn't ask for admin rights.
   It adds a Start Menu entry and, if you leave the box ticked, a desktop shortcut. Leave
   "Launch LedgerStitch" ticked on the last page to start it right away.

### Until the apps are signed: Gatekeeper and SmartScreen

Signed and notarized builds are coming. Until then your computer warns you the first time.

**macOS: "LedgerStitch can't be opened because Apple cannot check it for malicious software"**

- macOS 14 and earlier: right-click (or Control-click) LedgerStitch in Applications, choose
  **Open**, then **Open** again.
- macOS 15 and later: try to open it once, then go to **System Settings → Privacy & Security**,
  scroll down to "LedgerStitch was blocked…" and click **Open Anyway**.
- Or in Terminal: `xattr -dr com.apple.quarantine /Applications/LedgerStitch.app`

**Windows: "Windows protected your PC" (SmartScreen)**

Click **More info**, check that the file is the one you downloaded, then **Run anyway**.

Only do this for a file whose SHA-256 matches the published checksum.

### Uninstall the desktop app

- **macOS:** quit LedgerStitch and drag `/Applications/LedgerStitch.app` to the Trash.
- **Windows:** Settings → Apps → LedgerStitch → Uninstall. The uninstaller asks whether to
  delete your data too. The answer is **No** unless you change it.

Either way your workspaces stay in `~/LedgerStitch`. Delete that folder yourself only if you
are sure, and keep a backup first (Settings → Your data → Back up now).

---

## 2. One-line installer (from source)

The installers need no Python and no admin rights. They:

1. install [uv](https://docs.astral.sh/uv/) if it's missing, from its official HTTPS installer;
2. let uv fetch its own Python 3.12;
3. install LedgerStitch into an isolated folder;
4. add a `ledgerstitch` command and an app launcher.

Your system Python is never touched.

**macOS / Linux**, from a checkout of this repository:

    sh install.sh                      # installs this checkout

or without a checkout:

    curl --proto '=https' --tlsv1.2 -fsSL <repo-raw-url>/install.sh | sh -s -- git+https://github.com/<org>/ledgerstitch.git

**Windows** (PowerShell, no admin):

    powershell -ExecutionPolicy Bypass -File install.ps1
    # or: irm <repo-raw-url>/install.ps1 | iex

Then start it with `ledgerstitch`. You can also use **~/Applications/LedgerStitch.app**
(macOS), the **LedgerStitch** desktop entry (Linux) or the Start Menu shortcut (Windows).

| | App folder (venv + manifest) | Command | Launcher |
|---|---|---|---|
| macOS | `~/Library/Application Support/LedgerStitch/app` | `~/.local/bin/ledgerstitch` | `~/Applications/LedgerStitch.app` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/ledgerstitch/app` | `~/.local/bin/ledgerstitch` | `~/.local/share/applications/ledgerstitch.desktop` |
| Windows | `%LOCALAPPDATA%\LedgerStitch\app` | `%LOCALAPPDATA%\LedgerStitch\bin\ledgerstitch.cmd` (added to your user PATH) | Start Menu → LedgerStitch |

**Options:** `SOURCE` (a local checkout, `ledgerstitch==X.Y.Z` from PyPI, or `git+https://…`; plain
`http://` is refused), `--extra SPEC` (also install SPEC, e.g. an add-on, into the same venv; repeatable), `--editable`, `--prefix DIR`, `--bin-dir DIR`,
`--apps-dir DIR`, `--no-app`, `--uninstall`, `--purge-data`, `--yes`, `--help`.
The matching environment variables are `LEDGERSTITCH_PREFIX`, `LEDGERSTITCH_BIN_DIR`,
`LEDGERSTITCH_APPS_DIR`, `LEDGERSTITCH_NO_APP`, `LEDGERSTITCH_SOURCE`, `LEDGERSTITCH_PYTHON`
(default 3.12) and `LEDGERSTITCH_DATA_DIR`.

- **Upgrade:** `sh scripts/update.sh` from the checkout reinstalls only LedgerStitch when the
  dependencies haven't changed (see [Updating](#6-updating)). Running the install command again
  also works: the venv is reused and everything is re-resolved. Your data folder is never touched.
- **Uninstall:** `sh install.sh --uninstall` (Windows: `install.ps1 -Uninstall`). This
  removes the venv, the command and the launcher, and **keeps** `~/LedgerStitch`. To delete
  your data as well, add `--purge-data` (it asks first; `--yes` skips the question). uv and its
  Pythons are left in place for other tools.

Notes:

- The `~/Applications/LedgerStitch.app` launcher is a small script created on your Mac, so
  Gatekeeper doesn't block it. While it runs, the Dock may show "Python". It never replaces a
  LedgerStitch.app you installed from the DMG.
- If you delete uv's Pythons (`uv python uninstall`), the LedgerStitch venv stops working. Run
  the installer again to repair it.
- `install.sh` was tested on macOS (fresh install, re-run/upgrade, `--extra`, broken-venv
  repair, uninstall with and without `--purge-data`). Linux and `install.ps1` have not been run
  yet.

---

## 3. Self-host with Docker

Server mode is for running LedgerStitch on a machine that other devices reach over the
network. It is **not** the desktop app on a public port. It adds:

- **An admin password.** You set it on first run, and the server stores only an Argon2id
  hash of it, in `/data/server/admin.json`. Setting it the first time needs a one-time
  **setup code** printed in the server log, so whoever reaches a fresh server first can't
  claim it.
- **Session security.** Sessions use HttpOnly, SameSite=Strict, Secure cookies, every API
  call needs a per-session CSRF token, and sign-in is rate-limited (5 failures per client,
  30 in total, per 15 minutes).
- **Separate vault passphrases.** Signing in opens the app, not your data. Each workspace is
  still encrypted at rest and needs its own passphrase. Signing out locks it.
- **No plain HTTP.** The server refuses to start without TLS, unless you put it behind an
  HTTPS reverse proxy and set `LEDGERSTITCH_ALLOW_HTTP=1`.

### Quick start (with Caddy for automatic HTTPS)

**Try it on this computer:**

    cd docker
    docker compose up -d
    docker compose logs ledgerstitch | grep "Setup code"

The site runs on `https://localhost` with Caddy's own local certificate authority. Your
browser warns once about the certificate until you trust Caddy's root certificate.

**On a server with your own domain:**

    cd docker
    LEDGERSTITCH_DOMAIN=books.yourdomain.com docker compose up -d
    docker compose logs ledgerstitch | grep "Setup code"

1. Point the domain's DNS at the server first, and open ports 80 and 443. Caddy then gets a
   Let's Encrypt certificate automatically. Use your real domain: a made-up name (like
   `ledger.example.com`) never gets a certificate, and the site won't load.
2. Switching between localhost and a domain: `docker compose down`, then `up -d` with the new
   setting. Your data volume is kept.
3. Open `https://localhost/` or `https://<your domain>/`. Enter the setup code and choose an admin password of at
   least 12 characters. The setup wizard opens next.
   - The setup code stays the same across restarts until the admin password is set, then it's
     deleted. Dashes, spaces and case don't matter.
   - Lost it? Run `docker compose logs ledgerstitch | grep "Setup code"` again from the
     `docker` folder.
   - Already set the password and forgot it? `docker compose exec -it ledgerstitch ledgerstitch admin-password`.
4. Create a vault with its own passphrase.

The compose file runs LedgerStitch as a non-root user, with a read-only root filesystem, no
Linux capabilities and a health check (`/healthz`). Only Caddy publishes ports (80 and 443).
LedgerStitch's port 8080 stays inside the compose network.

### Local AI (included)

`docker compose up -d` also starts an **`ollama`** service for local AI. LedgerStitch reaches it at
`http://ollama:11434` (`LEDGERSTITCH_OLLAMA_URL`); it is not published to the host, and LedgerStitch
never gets the Docker socket.

- **Nothing is downloaded until you agree.** In the setup wizard, **Set up local AI** opens a consent
  dialog. After **Agree and set up**, the model (GLM-4.7-Flash, 19 GB) downloads into the
  `ollama-models` volume in the background while you create your vault.
- **Memory: about 24 GB for Docker.** On Docker Desktop, raise **Settings → Resources → Memory**
  to 24 GB or more (the wizard warns if Docker has less). Disk: 25 GB free.
- **On a Mac, use Ollama natively instead (recommended).** Docker Desktop can't use the Apple
  GPU and gives its VM only a few GB by default, so the model is slow there or gets killed while
  loading (`llama-server process has terminated: signal: killed`). Install Ollama from ollama.com,
  run `ollama pull glm-4.7-flash:latest`, then start LedgerStitch pointed at it:

      LEDGERSTITCH_OLLAMA_URL=http://host.docker.internal:11434 docker compose up -d ledgerstitch caddy

  It runs on the GPU with your Mac's unified memory. The in-Docker `ollama` service isn't needed.
- **CPU-only inside Docker on a Mac.** If you do run the `ollama` service there, it's much slower
  than the desktop app. The service caps the context at 8K tokens (merchant names don't need
  more), which keeps GLM-4.7-Flash at about 21 GB instead of the model's much larger default. On Linux with an NVIDIA GPU, uncomment the GPU block in
  `docker/docker-compose.yml` (needs the NVIDIA Container Toolkit).
- **Skip local AI:** start only the app and Caddy:

      docker compose up -d ledgerstitch caddy

  The wizard then hides the Local AI step (or shows `docker compose up -d ollama` if the URL is
  still configured).
- **Remove the model:** **Settings → Privacy & AI → Local AI → Remove model**, or delete the whole
  volume:

      docker compose stop ollama && docker compose rm -f ollama
      docker volume rm ledgerstitch_ollama-models

### Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `LEDGERSTITCH_SERVER` | `1` in the image | Server mode (same as `ledgerstitch --server`). |
| `LEDGERSTITCH_HOST` / `LEDGERSTITCH_PORT` | `0.0.0.0` / `8080` | Bind address and port. |
| `LEDGERSTITCH_TLS_CERT` / `LEDGERSTITCH_TLS_KEY` | – | Serve HTTPS directly with this certificate and key (PEM). |
| `LEDGERSTITCH_ALLOW_HTTP` | – | `1` = allow plain HTTP. Only behind an HTTPS reverse proxy. |
| `LEDGERSTITCH_TRUST_PROXY` | – | `1` = trust `X-Forwarded-Proto` / `X-Forwarded-For` (set with Caddy). Never set it when the port is reachable without the proxy. |
| `LEDGERSTITCH_ALLOWED_HOSTS` | any | Comma-separated host names the server answers to (loopback always works, for the health check). |
| `LEDGERSTITCH_ADMIN_PASSWORD_FILE` | – | Set the first admin password from a file (Docker secret) instead of the setup page. |
| `LEDGERSTITCH_OLLAMA_URL` | `http://ollama:11434` in the compose file | The Ollama service for local AI. Server mode only; exactly this URL is allowed besides loopback. |
| `LEDGERSTITCH_LOCAL_MODEL` | – | Development and tests only: use another model tag instead of GLM-4.7-Flash. Not for production. |

### Your own certificate instead of Caddy

1. Delete the `caddy` service from `docker/docker-compose.yml`.
2. Mount the certificate and key, for example `./certs:/certs:ro`.
3. Set `LEDGERSTITCH_TLS_CERT=/certs/fullchain.pem` and `LEDGERSTITCH_TLS_KEY=/certs/privkey.pem`.
4. Remove `LEDGERSTITCH_ALLOW_HTTP` and `LEDGERSTITCH_TRUST_PROXY`.
5. Publish `"8443:8080"`.

Without Docker it's the same: `ledgerstitch --server --tls-cert C --tls-key K`.

### Administration

- **Reset the admin password:**

      docker compose exec -it ledgerstitch ledgerstitch admin-password

  Then restart the container to sign out every session.
- **Back up:** each workspace has **Settings → Your data → Back up now**, which gives you an
  encrypted `.lsbackup`. Or snapshot the `ledgerstitch-data` volume. It holds the encrypted
  vaults, `app.json` and `server/admin.json`.
- **Local AI** in server mode uses the compose `ollama` service (see *Local AI* above). The setup
  wizard never installs software on a server; it only asks the service to download the model.
- **Update:** `sh scripts/update.sh --check`, then `sh scripts/update.sh` from the checkout. It
  rebuilds only what changed and backs up the data volume first. See [Updating](#6-updating).
- **Uninstall:** `docker compose down` keeps the volumes. `docker compose down -v` deletes them,
  **including your encrypted vaults** and the downloaded model.

---

## 4. First-run setup

The wizard opens on first launch, before any vault exists. Existing users never see it
unless they choose **Settings → Setup → Run setup again**.

1. **Welcome.** Three promises: on your computer · encrypted · you approve every change.
2. **Setup check.** An automatic, local check of your system, free disk space and the private
   port. It also shows the **Local AI** card:
   - **Local AI (recommended).** Two chips show whether Ollama is reachable and the model is
     installed. **Set up local AI** opens a consent dialog (nothing is pre-ticked): what is
     downloaded (GLM-4.7-Flash, 19 GB, from ollama.com), where it runs, what it sees (merchant
     names only), what it needs (25 GB disk, 24 GB RAM, shown next to what this machine has) and
     that you can remove it later. **Agree and set up** records your OK and starts one background
     job that installs or starts [Ollama](https://ollama.com) if needed, then downloads the model.
     **Not now** does nothing. With too little disk or memory the dialog warns and needs a second
     click (**Download anyway**). The wizard moves straight on; progress shows as a pill in the
     top bar and in **Settings → Privacy & AI**. When it's ready you get a toast, and
     categorization switches to local AI. If you quit mid-download, it picks up again at the next
     launch. Install details:
     - macOS: uses `brew install ollama` if Homebrew is present. Otherwise it downloads the
       official `Ollama-darwin.zip` and checks Ollama's Developer ID signature before opening it.
     - Windows: downloads the official `OllamaSetup.exe`, checks its Authenticode signature,
       then starts it.
     - Linux: the wizard only *shows* the official command
       (`curl -fsSL https://ollama.com/install.sh | sh`), because it needs your admin password.
       You run it in a terminal.
     - Docker / server mode: nothing is installed; the model downloads into the `ollama` service.

     Ollama pulls the model from its own registry. Installer downloads only ever go to
     `ollama.com` and the GitHub release storage it redirects to, over HTTPS. See docs/AI.md for
     why GLM-4.7-Flash.
3. **Create your vault.** Choose a passphrase and, if you want, a recovery key. Then pick
   **Try with sample data** (a separate, clearly labelled vault with a made-up business) or
   **Import my first statement**.

Whether setup is done is stored in `~/LedgerStitch/app.json`, a small file outside every
vault that holds no secrets and no financial data.

---

## 5. Troubleshooting

| Problem | Try this |
|---|---|
| macOS says the app "can't be opened" | See [Gatekeeper](#until-the-apps-are-signed-gatekeeper-and-smartscreen). |
| Windows "protected your PC" | **More info → Run anyway**, after checking the SHA-256. |
| Browser didn't open | Copy the `http://127.0.0.1:…` address from the LedgerStitch window. |
| `ledgerstitch: command not found` after the installer | Open a new terminal, or add the bin folder it printed to your PATH. |
| Installer: "uv not found" behind a proxy | Install uv yourself (`brew install uv`, or see docs.astral.sh/uv) and re-run. You can point to it with `UV=/path/to/uv`. |
| Setup: "Install Ollama" failed with a signature error | Nothing was installed. Download Ollama from ollama.com yourself, then click **Check again**. |
| Setup: the model download stalls | Make sure the Ollama app (or the `ollama` service) is still running. Click **Set up local AI** again in Settings → Privacy & AI; Ollama resumes. |
| Docker: "Refusing to start server mode without TLS" | Use the compose file (Caddy), or set `LEDGERSTITCH_TLS_CERT`/`_KEY`. Set `LEDGERSTITCH_ALLOW_HTTP=1` only behind an HTTPS proxy. |
| Docker: "Too many attempts" on sign-in | Wait 15 minutes, or restart the container. |
| Docker: lost the admin password | `docker compose exec -it ledgerstitch ledgerstitch admin-password`. Your vaults are unaffected. |
| Docker: "Invalid Host header" | Add your host name to `LEDGERSTITCH_ALLOWED_HOSTS` (or `LEDGERSTITCH_DOMAIN`). |
| `update.sh`: "local changes to tracked files" | Commit or `git stash` them. Put your own compose settings in `docker/docker-compose.override.yml` instead; `--force` skips the check. |
| `update.sh`: "can't fast-forward" | You have local commits on the branch. Rebase them onto the upstream, or pass `--ref <tag>`. |

---

## 6. Updating

**Quick reference** (your data folder and Docker volume are never removed):

```bash
# Docker: update to the latest (rebuilds only what changed, backs up the volume first)
git pull && sh scripts/update.sh
# Docker: full refresh, also picking up a patched Python base image
sh scripts/update.sh --refresh-base
# ...or by hand, with no cache at all
cd docker && docker compose build --pull --no-cache && docker compose up -d

# One-line installer: update (reinstalls only LedgerStitch if the dependencies are unchanged)
sh scripts/update.sh
# One-line installer: full refresh (re-resolves every dependency)
sh install.sh

# Any install: what would change, without changing anything
sh scripts/update.sh --check
ledgerstitch update            # Docker: docker compose exec ledgerstitch ledgerstitch update
```

After an update, reload the browser tab. New screens show up in every existing workspace; there
is nothing to migrate.

**Check first:** `ledgerstitch update` says which version you have, whether a newer release is
out and how to update this kind of install. It changes nothing. The release check is a single
HTTPS request to the public GitHub releases API, made only when you run the command. It sends no
version, install ID or data. LedgerStitch never checks for updates by itself. With `--offline`
it skips the request and only prints the instructions; `--json` is for scripts.

**Apply:** from a LedgerStitch checkout, `sh scripts/update.sh --check` shows the plan (exit
status 10 when an update is available, 0 when everything is current), and `sh scripts/update.sh`
applies it. It detects the install kind; pass `--native`, `--docker` or `--dev` when more than one
is present.

### Desktop app

Download the new DMG or Setup.exe from the
[releases page](https://github.com/securectl/ledgerstitch/releases/latest) and install it over the
old one. Your data folder is kept.

### One-line installer (`--native`)

1. The checkout the installer used (`source=` in `install-manifest.txt`) is fast-forwarded to its
   upstream branch, or to `--ref <tag>`.
2. The installed package's dependencies (`Requires-Dist`) are compared with the checkout's
   `pyproject.toml`:
   - **unchanged:** only LedgerStitch is reinstalled (`uv pip install --no-deps`). This takes
     seconds and downloads nothing;
   - **changed:** `install.sh` runs again with the same prefix, launchers and `--extra` add-ons.
3. The manifest records the installed source tree (`src_sha=`), so the next run says "up to date"
   without reinstalling.

An install from PyPI or a git URL has no checkout to compare with. Re-run the original install
command; it re-resolves everything.

### Docker (`--docker`)

The image built by `update.sh` carries labels with what it was built from:

| Label | Value |
|---|---|
| `io.ledgerstitch.src-sha` | git tree hash of `ledgerstitch/` |
| `io.ledgerstitch.deps-sha` | SHA-256 of the `[project]` dependency list plus the Dockerfile |
| `io.ledgerstitch.base-pin` / `org.opencontainers.image.base.name` | the pinned Python base, and the base actually used |
| `org.opencontainers.image.revision` / `.version` | the commit and the version |

`update.sh` computes the same values for the target commit **without checking it out** and
compares them with `docker image inspect`:

| Result | What happens |
|---|---|
| Labels match | No build. A version-only change also lands here. |
| Only the app code changed | Rebuild. The dependency layer comes from the cache, so only LedgerStitch is reinstalled (seconds). |
| Dependencies or the Dockerfile changed | Rebuild including the dependency layer. |
| The image predates these labels | One full rebuild; later updates are incremental. |

- **Base image.** The Python base is pinned by digest in `docker/Dockerfile`. When Docker Hub
  has a newer digest for the same tag (security patches), `update.sh` reports it, and
  `--refresh-base` rebuilds on it with `--pull`. A refreshed base is kept on later rebuilds until
  the Dockerfile pins a different one.
- **Pulled images** (`caddy`, `ollama`): the local `RepoDigests` are compared with the
  registry's current digest (`docker buildx imagetools inspect`). Only images that differ are
  pulled. Images you never pulled (for example `ollama` when you run without local AI) are skipped.
- **Backup first.** Before the LedgerStitch container is recreated, the `ledgerstitch-data` volume
  is copied, read-only and with no network, to
  `~/LedgerStitch-docker-backups/ledgerstitch-data-<UTC time>.tar.gz` (mode 600; set
  `LEDGERSTITCH_BACKUP_DIR` to change the folder). The vaults inside stay encrypted with their
  passphrases. `--no-backup` skips it. If the backup fails, nothing is changed.
- **Only running services are recreated** (`docker compose up -d --no-deps <running services>`).
  Volumes are never removed.
- **Local changes:** the update refuses to run while tracked files have local edits, except
  `docker/docker-compose.yml`, `docker/Caddyfile` and `docker/docker-compose.override.yml`. Keep
  your own settings in the override file, which `docker compose` reads automatically. `--force`
  skips the check.

Built by hand (`docker compose build`) instead? That works too. The image then has no labels,
and the next `update.sh` does one full rebuild.

### Dev checkout (`--dev`)

`git pull --ff-only`. The dependencies are reinstalled (`pip install -e ".[dev]"`, into `.venv` or
`LEDGERSTITCH_DEV_PYTHON`) only if `pyproject.toml` changed them.

### Proposed: data-only updates

Some updates change only data: statement formats (`ingest/data/known_formats.json`), payroll
limits and tax-year constants. A future **data pack** channel could deliver those files without
a code update: a versioned bundle on the release, signed, checked against a key that ships in
the app, and stored next to the vaults. It isn't built yet, because signed releases (and so a
signing key) come first. Until then, data changes arrive with a normal update.

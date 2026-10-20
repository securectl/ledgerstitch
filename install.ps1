# SPDX-License-Identifier: AGPL-3.0-only
# =====================================================================================
#  install.ps1 - LedgerStitch installer for Windows (run from source; no system Python)
#
#  *** UNTESTED: written on macOS without a Windows machine. Syntax-checked only. ***
#
#  Usage (Windows PowerShell 5.1+ or PowerShell 7, as a normal user - never "Run as admin"):
#     powershell -ExecutionPolicy Bypass -File install.ps1                 # this checkout
#     powershell -ExecutionPolicy Bypass -File install.ps1 ledgerstitch==1.0.1
#     powershell -ExecutionPolicy Bypass -File install.ps1 git+https://github.com/<org>/ledgerstitch.git
#     powershell -ExecutionPolicy Bypass -File install.ps1 --uninstall [--purge-data [--yes]]
#  One-liner (arguments after the script block):
#     & ([scriptblock]::Create((irm https://<host>/install.ps1))) ledgerstitch
#  or with `irm ... | iex`, pass options via $env:LEDGERSTITCH_SOURCE etc.
#
#  Options (GNU style or PowerShell style, e.g. --editable or -Editable):
#     --extra SPEC  --editable  --prefix DIR  --bin-dir DIR  --apps-dir DIR  --no-app
#     --uninstall  --purge-data  --yes  --allow-admin  --help
#  Env: LEDGERSTITCH_SOURCE, LEDGERSTITCH_PREFIX, LEDGERSTITCH_BIN_DIR, LEDGERSTITCH_APPS_DIR,
#       LEDGERSTITCH_NO_APP=1, LEDGERSTITCH_DATA_DIR, LEDGERSTITCH_PYTHON (default 3.12), UV
#
#  Defaults: venv in %LOCALAPPDATA%\LedgerStitch\app\venv, ledgerstitch.cmd in
#  %LOCALAPPDATA%\LedgerStitch\bin (added to the user PATH), Start Menu shortcut
#  "LedgerStitch". Data in %USERPROFILE%\LedgerStitch is only touched by --purge-data.
# =====================================================================================
# Everything runs inside one function so that `irm | iex` / scriptblock use never calls `exit`
# on the user's shell: errors are thrown (exit code 1 under -File), normal ends `return`.
function Invoke-LedgerStitchInstall {
$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$Marker = "created by the LedgerStitch installer"
$UvInstallUrl = "https://astral.sh/uv/install.ps1"

function Say([string]$m)  { Write-Host "==> $m" }
function Warn([string]$m) { Write-Warning $m }
function Die([string]$m)  { throw "error: $m" }
function EnvOr([string]$name, [string]$default) {
    $v = [Environment]::GetEnvironmentVariable($name)
    if ([string]::IsNullOrEmpty($v)) { return $default } else { return $v }
}

function Show-Usage {
@"
LedgerStitch installer (Windows)

Usage: install.ps1 [options] [SOURCE]

SOURCE (default: the LedgerStitch checkout this script is in, if any)
  .\path\to\checkout           a local LedgerStitch source tree
  ledgerstitch[==X.Y.Z]        a release from PyPI
  git+https://...ledgerstitch.git[@ref]   a git URL (never plain http)

Options
  --extra SPEC     also install SPEC into the same venv (a pip requirement or wheel path,
                   e.g. an add-on); repeatable
  --editable       editable install of a local checkout (developers)
  --prefix DIR     app dir with the venv + manifest (default %LOCALAPPDATA%\LedgerStitch\app)
  --bin-dir DIR    where ledgerstitch.cmd goes (default %LOCALAPPDATA%\LedgerStitch\bin; added to user PATH)
  --apps-dir DIR   where the shortcut goes (default: your Start Menu Programs folder)
  --no-app         don't create the Start Menu shortcut
  --uninstall      remove the venv, command, shortcut and PATH entry (keeps your data)
  --purge-data     with --uninstall: also delete %USERPROFILE%\LedgerStitch (asks first)
  --yes            don't ask for confirmation
  --allow-admin    allow running elevated (not recommended)
  --help           show this help
"@ | Write-Host
}

# ------------------------------------------------------------------ arguments
$Source    = EnvOr "LEDGERSTITCH_SOURCE" ""
$Prefix    = EnvOr "LEDGERSTITCH_PREFIX" (Join-Path $env:LOCALAPPDATA "LedgerStitch\app")
$BinDir    = EnvOr "LEDGERSTITCH_BIN_DIR" (Join-Path $env:LOCALAPPDATA "LedgerStitch\bin")
$AppsDir   = EnvOr "LEDGERSTITCH_APPS_DIR" ([Environment]::GetFolderPath("Programs"))
$NoApp     = (EnvOr "LEDGERSTITCH_NO_APP" "0") -eq "1"
$PyVersion = EnvOr "LEDGERSTITCH_PYTHON" "3.12"
$Extras = @(); $Editable = $false; $Uninstall = $false; $Purge = $false; $Yes = $false
$AllowAdmin = $false; $GotSource = $false

$argv = @($args)
for ($i = 0; $i -lt $argv.Count; $i++) {
    $a = [string]$argv[$i]
    if ($a.StartsWith("-")) {
        $val = $null
        if ($a.Contains("=")) { $val = $a.Substring($a.IndexOf("=") + 1); $a = $a.Substring(0, $a.IndexOf("=")) }
        $key = $a.TrimStart("-").Replace("-", "").ToLowerInvariant()
        $needsValue = @("prefix", "bindir", "appsdir", "extra") -contains $key
        if ($needsValue -and $null -eq $val) {
            if ($i + 1 -ge $argv.Count) { Die "$a needs a value" }
            $i++; $val = [string]$argv[$i]
        }
        switch ($key) {
            { $_ -in "h", "help", "?" } { Show-Usage; return }
            "extra"      { $Extras += $val }
            "editable"   { $Editable = $true }
            "prefix"     { $Prefix = $val }
            "bindir"     { $BinDir = $val }
            "appsdir"    { $AppsDir = $val }
            "noapp"      { $NoApp = $true }
            "uninstall"  { $Uninstall = $true }
            "purgedata"  { $Purge = $true }
            { $_ -in "y", "yes" } { $Yes = $true }
            { $_ -in "allowadmin", "allowroot" } { $AllowAdmin = $true }
            default      { Die "unknown option: $a (see --help)" }
        }
    } else {
        if ($GotSource) { Die "only one SOURCE allowed" }
        $Source = $a; $GotSource = $true
    }
}
if ($Purge -and -not $Uninstall) { Die "--purge-data only works together with --uninstall" }

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin -and -not $AllowAdmin) {
    Die "don't run this as administrator: LedgerStitch installs per user. Open a normal PowerShell. (Override: --allow-admin)"
}

$Prefix   = [IO.Path]::GetFullPath($Prefix)
$BinDir   = [IO.Path]::GetFullPath($BinDir)
$AppsDir  = [IO.Path]::GetFullPath($AppsDir)
$Venv     = Join-Path $Prefix "venv"
$VPy      = Join-Path $Venv "Scripts\python.exe"
$VPyw     = Join-Path $Venv "Scripts\pythonw.exe"
$Manifest = Join-Path $Prefix "install-manifest.txt"
$DataDir  = EnvOr "LEDGERSTITCH_DATA_DIR" (Join-Path $env:USERPROFILE "LedgerStitch")
$Shim     = Join-Path $BinDir "ledgerstitch.cmd"
$Lnk      = Join-Path $AppsDir "LedgerStitch.lnk"

# ------------------------------------------------------------------ helpers
function Read-Manifest {
    $m = @{ paths = @(); userpath = "" }
    if (Test-Path -LiteralPath $Manifest) {
        foreach ($line in Get-Content -LiteralPath $Manifest) {
            if ($line -like "path=*") { $m.paths += $line.Substring(5) }
            elseif ($line -like "userpath=*") { $m.userpath = $line.Substring(9) }
        }
    }
    return $m
}

function Test-Ours([string]$p) {
    if (-not (Test-Path -LiteralPath $p)) { return $false }
    if ($p -like "*.lnk") {   # a shortcut can't carry our marker: check where it points
        try {
            $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($p)
            return ($sc.TargetPath -like "$Venv\*") -or ($sc.Description -like "*$Marker*")
        } catch { return $false }
    }
    return [bool](Select-String -LiteralPath $p -SimpleMatch $Marker -Quiet)
}

function Remove-Ours([string]$p) {
    if (-not (Test-Path -LiteralPath $p)) { return }
    if (Test-Ours $p) { Remove-Item -LiteralPath $p -Force -Recurse; Say "removed $p" }
    else { Warn "left $p alone (not created by this installer)" }
}

function Get-UserPath { $v = [Environment]::GetEnvironmentVariable("Path", "User"); if ($null -eq $v) { "" } else { $v } }
function Remove-FromUserPath([string]$dir) {
    $parts = (Get-UserPath).Split(";") | Where-Object { $_ -and ($_.TrimEnd("\") -ne $dir.TrimEnd("\")) }
    [Environment]::SetEnvironmentVariable("Path", ($parts -join ";"), "User")
}

function Confirm-Action([string]$question) {
    if ($Yes) { return $true }
    try { $ans = Read-Host "$question [y/N]" } catch { Die "no console to ask for confirmation: re-run with --yes" }
    return @("y", "yes") -contains ([string]$ans).Trim().ToLowerInvariant()
}

# ------------------------------------------------------------------ uninstall
if ($Uninstall) {
    $purgeDir = $null
    if ($Purge -and (Test-Path -LiteralPath $DataDir)) {
        $purgeDir = (Resolve-Path -LiteralPath $DataDir).Path.TrimEnd("\")
        $forbidden = @($env:USERPROFILE.TrimEnd("\"), [IO.Path]::GetPathRoot($purgeDir).TrimEnd("\"))
        if ($forbidden -contains $purgeDir) { Die "refusing to delete $purgeDir" }
        if (-not (Test-Path -LiteralPath (Join-Path $purgeDir "vaults"))) {
            Die "$purgeDir doesn't look like a LedgerStitch data folder (no vaults\); not deleting it"
        }
        if (-not (Confirm-Action "Uninstall AND permanently delete ALL LedgerStitch data (vaults, receipts, backups) in $purgeDir?")) {
            Die "cancelled; nothing was removed"
        }
    }
    Say "uninstalling LedgerStitch from $Prefix"
    $m = Read-Manifest
    $paths = if ($m.paths.Count) { $m.paths } else { @($Shim, $Lnk) }
    foreach ($p in $paths) { Remove-Ours $p }
    if ($m.userpath) { Remove-FromUserPath $m.userpath; Say "removed $($m.userpath) from your user PATH" }
    if (Test-Path -LiteralPath $Prefix) {
        if ((Test-Path -LiteralPath $Manifest) -or (Test-Path -LiteralPath $VPy)) {
            foreach ($p in @($Venv, $Manifest, (Join-Path $Prefix "LedgerStitch.ico"))) {
                if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Recurse -Force }
            }
            Say "removed $Venv"
            foreach ($d in @($Prefix, $BinDir, (Split-Path $Prefix -Parent))) {
                if ((Test-Path -LiteralPath $d) -and -not (Get-ChildItem -LiteralPath $d -Force)) { Remove-Item -LiteralPath $d -Force }
            }
        } else {
            Warn "$Prefix has no LedgerStitch install manifest or venv; left it alone"
        }
    }
    if ($purgeDir) { Remove-Item -LiteralPath $purgeDir -Recurse -Force; Say "deleted data folder $purgeDir" }
    elseif ($Purge) { Say "no data folder at $DataDir" }
    elseif (Test-Path -LiteralPath $DataDir) { Say "kept your data in $DataDir (to delete it too: --uninstall --purge-data)" }
    Say "LedgerStitch uninstalled. (uv and its managed Pythons were left in place.)"
    return
}

# ------------------------------------------------------------------ source
function Test-Checkout([string]$dir) {
    $pp = Join-Path $dir "pyproject.toml"
    return (Test-Path -LiteralPath $pp) -and [bool](Select-String -LiteralPath $pp -Pattern '^name\s*=\s*"ledgerstitch"' -Quiet)
}
$LocalSrc = $null
$ScriptDir = $PSScriptRoot    # empty when run via iex / scriptblock
if (-not $Source) {
    if ($ScriptDir -and (Test-Checkout $ScriptDir)) { $LocalSrc = $ScriptDir }
    else { Die "no SOURCE given and this script isn't inside a LedgerStitch checkout. Try: install.ps1 ledgerstitch  (PyPI)  or  install.ps1 git+https://<host>/<org>/ledgerstitch.git" }
} elseif ($Source -match '^(git\+)?http://') {
    Die "refusing a non-HTTPS source: $Source"
} elseif ($Source -match '^(git\+https://|git\+ssh://|https://)') {
    # remote URL: passed to uv as-is
} elseif ($Source -match '^ledgerstitch([=<>!~\[].*)?$') {
    # PyPI requirement
} elseif ((Test-Path -LiteralPath $Source -PathType Container) -and (Test-Checkout $Source)) {
    $LocalSrc = (Resolve-Path -LiteralPath $Source).Path
} else {
    Die "unrecognised SOURCE: $Source (a LedgerStitch checkout, ledgerstitch[==X.Y.Z] or git+https://...)"
}
if ($Editable -and -not $LocalSrc) { Die "--editable needs a local checkout" }
$SrcDesc = if ($LocalSrc) { $LocalSrc } else { $Source }

# ------------------------------------------------------------------ uv
function Find-Uv {
    $u = EnvOr "UV" ""
    if ($u -and (Test-Path -LiteralPath $u)) { return $u }
    $c = Get-Command uv -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($p in @((Join-Path $env:USERPROFILE ".local\bin\uv.exe"), (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"))) {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}
$Uv = Find-Uv
if (-not $Uv) {
    Say "installing uv (https://docs.astral.sh/uv/) from $UvInstallUrl"
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $env:UV_NO_MODIFY_PATH = "1"; $env:INSTALLER_NO_MODIFY_PATH = "1"   # don't edit the user PATH for uv
    # run the official installer in a child PowerShell so it can't affect this session
    $psExe = (Get-Process -Id $PID).Path
    & $psExe -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; irm -UseBasicParsing '$UvInstallUrl' | iex"
    if ($LASTEXITCODE -ne 0) { Die "uv installation failed (network?)" }
    $Uv = Find-Uv
    if (-not $Uv) { Die "uv was installed but can't be found; open a new PowerShell and re-run" }
}
Say "using uv: $Uv ($(& $Uv --version))"

# ------------------------------------------------------------------ python + venv
Say "fetching a uv-managed Python $PyVersion"
& $Uv python install $PyVersion
if ($LASTEXITCODE -ne 0) { Die "could not install Python $PyVersion (network?)" }
$managed = if ((& $Uv venv --help | Out-String) -match "--managed-python") { "--managed-python" } else { "--python-preference=only-managed" }

function Test-VenvOk {
    if (-not (Test-Path -LiteralPath $VPy)) { return $false }
    try { $have = (& $VPy -c "import sys; print('%d.%d' % sys.version_info[:2])" | Out-String).Trim() } catch { return $false }
    return (($LASTEXITCODE -eq 0) -and ($have -eq $PyVersion))
}
New-Item -ItemType Directory -Force -Path $Prefix | Out-Null
if (Test-VenvOk) {
    Say "upgrading the existing install in $Prefix"
} else {
    if (Test-Path -LiteralPath $Venv) { Say "recreating the venv (broken or not Python $PyVersion)"; Remove-Item -LiteralPath $Venv -Recurse -Force }
    & $Uv venv -q $managed --python $PyVersion $Venv
    if ($LASTEXITCODE -ne 0) { Die "could not create the venv" }
}

# ------------------------------------------------------------------ install
if ($LocalSrc -and $Editable) { $pkgArgs = @("-e", $LocalSrc) }
elseif ($LocalSrc) { $pkgArgs = @($LocalSrc, "--reinstall-package", "ledgerstitch") }
else { $pkgArgs = @($Source) }
if ($Extras.Count) { $pkgArgs += $Extras }
Say "installing LedgerStitch from $SrcDesc"
& $Uv pip install -q --python $VPy --upgrade @pkgArgs
if ($LASTEXITCODE -ne 0) { Die "package installation failed" }
$Exe = Join-Path $Venv "Scripts\ledgerstitch.exe"
if (-not (Test-Path -LiteralPath $Exe)) { Die "ledgerstitch.exe is missing from the venv" }
$Version = (& $VPy -c "import ledgerstitch; print(ledgerstitch.__version__)" | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { Die "ledgerstitch doesn't import" }

# ------------------------------------------------------------------ launchers
$old = Read-Manifest
$newPaths = @()
$UserPathAdded = $old.userpath

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
if ((Test-Path -LiteralPath $Shim) -and -not (Test-Ours $Shim)) {
    Warn "$Shim exists and wasn't created by this installer; not replacing it"
} else {
    $cmd = "@echo off`r`nrem LedgerStitch command ($Marker; removed by install.ps1 --uninstall)`r`n`"$Exe`" %*`r`n"
    [IO.File]::WriteAllText($Shim, $cmd, [Text.Encoding]::Default)   # cmd.exe reads the ANSI code page
    $newPaths += $Shim
}
$userPathParts = (Get-UserPath).Split(";") | ForEach-Object { $_.TrimEnd("\") }
if ($userPathParts -notcontains $BinDir.TrimEnd("\")) {
    $cur = Get-UserPath
    $newUserPath = if ($cur) { "$cur;$BinDir" } else { $BinDir }
    [Environment]::SetEnvironmentVariable("Path", $newUserPath, "User")
    $UserPathAdded = $BinDir
    Say "added $BinDir to your user PATH (new terminals will see it)"
}
if ($UserPathAdded -and ($UserPathAdded -ne $BinDir)) { Remove-FromUserPath $UserPathAdded; $UserPathAdded = $BinDir }
if (($env:Path.Split(";") | ForEach-Object { $_.TrimEnd("\") }) -notcontains $BinDir.TrimEnd("\")) { $env:Path = "$env:Path;$BinDir" }

if (-not $NoApp) {
    if ((Test-Path -LiteralPath $Lnk) -and -not (Test-Ours $Lnk)) {
        Warn "$Lnk exists and wasn't created by this installer; not replacing it"
    } else {
        New-Item -ItemType Directory -Force -Path $AppsDir | Out-Null
        $sh = New-Object -ComObject WScript.Shell
        $sc = $sh.CreateShortcut($Lnk)
        if (Test-Path -LiteralPath $VPyw) { $sc.TargetPath = $VPyw; $sc.Arguments = "-m ledgerstitch.desktop" }   # no console window
        else { $sc.TargetPath = (Join-Path $Venv "Scripts\ledgerstitch-desktop.exe"); $sc.Arguments = "" }
        $sc.WorkingDirectory = $env:USERPROFILE
        $sc.Description = "LedgerStitch ($Marker)"
        if ($LocalSrc -and (Test-Path -LiteralPath (Join-Path $LocalSrc "packaging\LedgerStitch.ico"))) {
            $ico = Join-Path $Prefix "LedgerStitch.ico"
            Copy-Item -LiteralPath (Join-Path $LocalSrc "packaging\LedgerStitch.ico") -Destination $ico -Force
            $sc.IconLocation = "$ico,0"
        }
        $sc.Save()
        $newPaths += $Lnk
    }
}

foreach ($p in $old.paths) { if ($newPaths -notcontains $p) { Remove-Ours $p } }   # moved launchers

# ------------------------------------------------------------------ manifest
$lines = @(
    "# LedgerStitch install manifest (written by install.ps1; used by --uninstall)",
    "version=$Version", "python=$PyVersion", "source=$SrcDesc", "extras=$($Extras -join ' ')",
    "venv=$Venv", "installed_at=$((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))"
)
if ($UserPathAdded) { $lines += "userpath=$UserPathAdded" }
foreach ($p in $newPaths) { $lines += "path=$p" }
Set-Content -LiteralPath $Manifest -Value $lines -Encoding UTF8

# ------------------------------------------------------------------ done
Say "LedgerStitch $Version installed in $Prefix (your data stays in $DataDir)"
if ($NoApp) { Write-Host "Done. Start LedgerStitch: ledgerstitch  (in a new terminal)" }
else { Write-Host "Done. Start LedgerStitch: ledgerstitch  (in a new terminal), or Start menu > LedgerStitch" }
}   # end Invoke-LedgerStitchInstall

Invoke-LedgerStitchInstall @args

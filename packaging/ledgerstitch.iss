; Inno Setup 6 script for LedgerStitch (Windows).  *** UNTESTED ***
; Built by scripts\build_windows.ps1, which passes:
;   /DAppVersion=1.0.0  /DAppVersionNum=1.0.0.0  /DEditionSuffix=  (or -Pro)
;   /DSourceDir=<repo>\dist\LedgerStitch  /O<repo>\dist
;   and, only when a code-signing certificate is configured:
;   /DUseSignTool "/Ssigntool=<signtool.exe> sign ... $f"
; Per-user install by default (no admin prompt) into %LOCALAPPDATA%\Programs\LedgerStitch.
; IT admins can install for all users with:  Setup.exe /ALLUSERS  (needs elevation).
; User data lives in %USERPROFILE%\LedgerStitch (or %LEDGERSTITCH_DATA_DIR%). The uninstaller
; keeps it unless the user explicitly answers "Yes" to the delete question (default: No);
; silent uninstalls (/SILENT, /VERYSILENT) never delete it.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppVersionNum
  ; numeric a.b.c.d for the Windows version resource (build_windows.ps1 normalises
  ; e.g. "1.2.0rc1" -> "1.2.0.0")
  #define AppVersionNum "0.0.0.0"
#endif
#ifndef EditionSuffix
  #define EditionSuffix ""
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\LedgerStitch"
#endif
; placeholder until the public site exists
#define AppURL "https://ledgerstitch.example"

[Setup]
; the same AppId for Community and Pro: installing one upgrades/replaces the other in place
AppId={{6C1E4E9B-3F7A-4C55-9B0E-1F6F5C1E2D01}
AppName=LedgerStitch
AppVersion={#AppVersion}
AppVerName=LedgerStitch {#AppVersion}
AppPublisher=LedgerStitch
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/support
AppUpdatesURL={#AppURL}/download
VersionInfoVersion={#AppVersionNum}
VersionInfoProductVersion={#AppVersionNum}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoProductName=LedgerStitch
VersionInfoCompany=LedgerStitch
VersionInfoDescription=LedgerStitch Setup
; {autopf} = %LOCALAPPDATA%\Programs in per-user mode (same folder as earlier builds)
DefaultDirName={autopf}\LedgerStitch
DefaultGroupName=LedgerStitch
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
; no elevation dialog for normal users; "/ALLUSERS" on the command line for IT deployments
PrivilegesRequiredOverridesAllowed=commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputBaseFilename=LedgerStitch{#EditionSuffix}-{#AppVersion}-Windows-x64-Setup
SetupIconFile=LedgerStitch.ico
UninstallDisplayIcon={app}\LedgerStitch.exe
UninstallDisplayName=LedgerStitch
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Upgrades: ask (Restart Manager) to close a running LedgerStitch before replacing files.
; The app relocks the vault on next start, so it is not restarted automatically.
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
#ifdef UseSignTool
SignTool=signtool
SignedUninstaller=yes
#endif

[Tasks]
; checked by default (no "unchecked" flag); the user can untick it
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\LedgerStitch"; Filename: "{app}\LedgerStitch.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\LedgerStitch"; Filename: "{app}\LedgerStitch.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; "Launch LedgerStitch" checkbox on the finish page (not shown for silent installs);
; runasoriginaluser: never start the app elevated after an /ALLUSERS install
Filename: "{app}\LedgerStitch.exe"; Description: "Launch LedgerStitch"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
// Optional, explicit data removal at uninstall time. Default answer: No. Never in silent mode.
function DataDir(): String;
begin
  Result := GetEnv('LEDGERSTITCH_DATA_DIR');
  if Result = '' then
    Result := ExpandConstant('{%USERPROFILE}') + '\LedgerStitch';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Dir: String;
begin
  if (CurUninstallStep = usPostUninstall) and (not UninstallSilent) then
  begin
    Dir := DataDir();
    // only offer it for a real LedgerStitch data folder (it always has a vaults\ subfolder)
    if DirExists(Dir + '\vaults') then
    begin
      if MsgBox('LedgerStitch has been removed. Your data (vaults, receipts, backups) is still in:' + #13#10 +
                Dir + #13#10#13#10 +
                'Also delete my LedgerStitch data? This cannot be undone.' + #13#10 +
                'Choose No to keep it (recommended if you may reinstall).',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        if DelTree(Dir, True, True, True) then
          MsgBox('Deleted ' + Dir, mbInformation, MB_OK)
        else
          MsgBox('Could not delete everything in ' + Dir + ' (is LedgerStitch still running?).', mbError, MB_OK);
      end;
    end;
  end;
end;

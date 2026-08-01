; =====================================================================
; installer.iss — the one file a user downloads.
;
; Produces OWCSCompTracker-<version>-Setup.exe from the PyInstaller onedir
; build in dist\OWCSCompTracker. Everything needed to process a broadcast is
; inside it: Python, OpenCV, NumPy, yt-dlp, ffmpeg, ffprobe, the pipeline,
; the calibrated layouts, the hero templates and the control room.
;
; Design decisions worth stating:
;
;   PrivilegesRequired=lowest — installs per user into Local AppData by
;   default. A standard user with no administrator rights can install this,
;   which is the difference between "one click" and "ask your IT department".
;   An admin who wants a machine-wide install still can (the dialog offers
;   it) and the app's own data stays per-user either way.
;
;   No service is registered. The background worker runs as the signed-in
;   user via the HKCU Run key, which the app manages itself and which the
;   user can see and disable in Task Manager. A SYSTEM service would need
;   elevation, could not read the user's credential vault (DPAPI is
;   per-user), and would be harder to stop than to install.
;
;   Uninstall leaves the user's data. Databases, evidence and downloads live
;   in Local AppData and are only removed if the user ticks the box on the
;   uninstall page — deleting someone's processed results because they
;   uninstalled an app is not a decision to make silently.
;
; Build:  iscc packaging\installer.iss /DAppVersion=1.0.0
; =====================================================================

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

#define AppName        "OWCS Comp Tracker"
#define AppPublisher   "OWCS Comp Tracker"
#define AppExe         "OWCSCompTracker.exe"
#define AppUrl         "https://github.com/cvree/owcscomp.tracker"

[Setup]
; A fixed GUID: upgrades must replace the previous install rather than sit
; beside it, and that only happens when the AppId never changes.
AppId={{7A1E4C90-2B6D-4F53-9C11-3E8A5D06B412}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
LicenseFile=..\packaging\LICENSE-INSTALLER.txt
OutputDir=..\dist\installer
OutputBaseFilename=OWCSCompTracker-{#AppVersion}-Setup
SetupIconFile=..\desktop\assets\owcs.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Per-user by default so no administrator is needed; the user may still
; choose an all-users install from the wizard.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; The bundle is 64-bit CPython + OpenCV.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
  GroupDescription: "Shortcuts:"
Name: "startupicon"; \
  Description: "Start &processing automatically when I sign in (recommended)"; \
  GroupDescription: "Background service:"

[Files]
; The entire PyInstaller onedir bundle: the exe, CPython, OpenCV, NumPy,
; yt-dlp, the vendored ffmpeg/ffprobe, and the repository payload.
Source: "..\dist\OWCSCompTracker\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
  Comment: "Open the OWCS Comp Tracker control room"
Name: "{group}\{#AppName} setup and checks"; Filename: "{app}\{#AppExe}"; \
  Parameters: "--setup"; Comment: "Re-run setup, health checks and repairs"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
  Tasks: desktopicon

[Registry]
; Autostart. The application manages this key itself at runtime (Settings ->
; "Start with Windows"); the installer only sets the initial state, and
; uninstalling removes it.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "OWCSCompTracker"; \
  ValueData: """{app}\{#AppExe}"" --tray"; \
  Flags: uninsdeletevalue; Tasks: startupicon

[Run]
; Land the user in the graphical wizard rather than a bare window. This is
; the last step of the installer, so "install and it is set up" is one flow.
Filename: "{app}\{#AppExe}"; Parameters: "--setup"; \
  Description: "Set up {#AppName} now"; \
  Flags: nowait postinstall skipifsilent

[UninstallRun]
; Ask the running service to stop before files are removed, so an upgrade or
; uninstall never fails on a locked ffmpeg.exe.
Filename: "{app}\{#AppExe}"; Parameters: "--stop-service"; \
  Flags: runhidden skipifdoesntexist; RunOnceId: "StopService"

[Code]
var
  DataPage: TInputOptionWizardPage;

procedure InitializeWizard;
begin
  DataPage := CreateInputOptionPage(
    wpSelectTasks,
    'Your processed data',
    'What should happen to results if you uninstall later?',
    'Broadcasts you process, the evidence behind them and your settings are ' +
    'kept in your user folder, separately from the program itself. They are ' +
    'left in place when you uninstall unless you ask otherwise at that time.',
    False, False);
  DataPage.Add('I understand — my results are kept outside the install folder');
  DataPage.Values[0] := True;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\{#AppName}');
    if DirExists(DataDir) then
    begin
      if MsgBox('Also delete your processed results, evidence, downloads and ' +
                'settings?' + #13#10 + #13#10 + DataDir + #13#10 + #13#10 +
                'Choose No to keep them for a future reinstall.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;

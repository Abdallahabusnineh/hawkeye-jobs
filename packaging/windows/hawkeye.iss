; Inno Setup script for the Windows installer (HawkeyeJobs-Windows-Setup.exe).
;
;   iscc /DAppVersion=1.0.0 packaging\windows\hawkeye.iss      (from the repo root, after PyInstaller)
;
; Installs for the current user only (no administrator prompt) into
; %LOCALAPPDATA%\Programs\Hawkeye Jobs, with Start menu and optional desktop shortcuts and an
; uninstaller. The user's data (%LOCALAPPDATA%\hawkeye-jobs) is never touched by install,
; upgrade or uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; Never change AppId: Windows uses it to recognise upgrades of the same app.
AppId={{6F1C2B9E-4D7A-4E31-9B8C-2A5D7E3F1C40}
AppName=Hawkeye Jobs
AppVersion={#AppVersion}
AppVerName=Hawkeye Jobs {#AppVersion}
AppPublisher=Hawkeye Jobs contributors
AppPublisherURL=https://abdallahabusnineh.github.io/hawkeye-jobs/
AppSupportURL=https://github.com/Abdallahabusnineh/hawkeye-jobs/issues
AppUpdatesURL=https://github.com/Abdallahabusnineh/hawkeye-jobs/releases/latest
DefaultDirName={localappdata}\Programs\Hawkeye Jobs
DefaultGroupName=Hawkeye Jobs
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=HawkeyeJobs-Windows-Setup
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\Hawkeye Jobs.exe
UninstallDisplayName=Hawkeye Jobs
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Close a running copy before replacing its files (upgrades).
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=auto

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
; Arabic is not shipped with every Inno Setup version: use it when this compiler has it.
#if FileExists(CompilerPath + "Languages\Arabic.isl")
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\Hawkeye Jobs\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; An upgrade replaces the bundled libraries completely, so files dropped by a newer version do not linger.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\Hawkeye Jobs"; Filename: "{app}\Hawkeye Jobs.exe"
Name: "{autodesktop}\Hawkeye Jobs"; Filename: "{app}\Hawkeye Jobs.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Hawkeye Jobs.exe"; Description: "{cm:LaunchProgram,Hawkeye Jobs}"; Flags: nowait postinstall skipifsilent

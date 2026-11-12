; Inno Setup 6 script for the NSW Windows installer (per-user, no administrator rights).
;
;   scripts\release\build_windows.ps1          builds the application folder and runs this script
;   ISCC.exe /DAppVersion=0.9.0 packaging\windows\installer.iss
;
; Installs build\dist\NSW (PyInstaller one-folder build) into
; %LOCALAPPDATA%\Programs\NSW, the synthetic demonstration spectra into "Demo spectra"
; (read-only, synthetic, labelled as such), Start Menu entries and an optional desktop
; shortcut. User data (%LOCALAPPDATA%\NSW and any project or export the user saved) is
; never touched by setup, upgrade or uninstall. No laboratory data is included.

#ifndef AppVersion
  #error "Pass the version: ISCC /DAppVersion=<version> installer.iss"
#endif
#define AppName "Nuclear Spectroscopy Workbench"
#define AppExe "NSW.exe"
#define Root "..\.."

[Setup]
; fixed identifier: upgrades replace the previous installation in place
AppId={{2D7F25F3-3515-4FDB-99F4-171652E29063}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Aravind Loshana A
AppPublisherURL=https://github.com/aravi076/Nuclear-Spectroscopy-Workbench
AppSupportURL=https://github.com/aravi076/Nuclear-Spectroscopy-Workbench/issues
VersionInfoVersion=0.9.0.0
VersionInfoDescription={#AppName} installer
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\NSW
DefaultGroupName=NSW
DisableProgramGroupPage=yes
LicenseFile={#Root}\LICENSE
OutputDir={#Root}\build\installer
OutputBaseFilename=NSW-Setup-{#AppVersion}-x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#Root}\build\dist\NSW\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Root}\data\synthetic\*"; DestDir: "{app}\Demo spectra (synthetic)"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Root}\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "{#Root}\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\NSW"; Filename: "{app}\{#AppExe}"
Name: "{group}\NSW self-test"; Filename: "{app}\{#AppExe}"; Parameters: "--self-test ""{userdocs}\NSW self-test"""
Name: "{group}\Demo spectra (synthetic)"; Filename: "{app}\Demo spectra (synthetic)"
Name: "{group}\Licences"; Filename: "{app}\_internal\licenses"
Name: "{group}\Uninstall NSW"; Filename: "{uninstallexe}"
Name: "{autodesktop}\NSW"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start NSW now"; Flags: nowait postinstall skipifsilent

; [UninstallDelete] intentionally empty: only files installed by setup are removed;
; %LOCALAPPDATA%\NSW, projects, reports and the self-test folder stay.

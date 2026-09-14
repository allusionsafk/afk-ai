#ifndef SourceRoot
  #define SourceRoot "..\build\payload"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef AppVersion
  #define AppVersion "0.2.0-rc1"
#endif
#ifndef FileVersion
  #define FileVersion "0.2.0.0"
#endif
#ifndef Channel
  #define Channel "prerelease"
#endif
#ifndef SourceCommit
  #define SourceCommit "development"
#endif
#ifndef TestAppId
  #define TestAppId "{{8A8A2D4D-CE75-4A2D-A39B-56B4206F93D0}"
#endif
#ifndef TestDefaultDir
  #define TestDefaultDir "{localappdata}\Programs\AFK LocalAI"
#endif
#ifndef TestGroupName
  #define TestGroupName "AFK LocalAI"
#endif

[Setup]
AppId={#TestAppId}
AppName=AFK LocalAI
AppVersion={#AppVersion}
AppVerName=AFK LocalAI {#AppVersion}
AppPublisher=AFK
AppPublisherURL=https://github.com/allusionsafk/localai-windows-starter
AppSupportURL=https://github.com/allusionsafk/localai-windows-starter/issues/new/choose
AppUpdatesURL=https://github.com/allusionsafk/localai-windows-starter/releases
VersionInfoVersion={#FileVersion}
VersionInfoCompany=AFK
VersionInfoDescription=AFK LocalAI Windows Setup ({#Channel}, {#SourceCommit})
VersionInfoProductName=AFK LocalAI
VersionInfoProductVersion={#FileVersion}
DefaultDirName={#TestDefaultDir}
DefaultGroupName={#TestGroupName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
UsePreviousAppDir=yes
UsePreviousGroup=yes
Uninstallable=yes
CreateUninstallRegKey=yes
UninstallDisplayName=AFK LocalAI {#AppVersion}
UninstallDisplayIcon={app}\AFKLocalAI.exe
OutputDir={#OutputDir}
OutputBaseFilename=AFKLocalAISetup-{#AppVersion}-x64
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
UninstallLogging=yes
CloseApplications=yes
RestartApplications=no
ChangesEnvironment=no
ChangesAssociations=no
DisableWelcomePage=no
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AFK LocalAI"; Filename: "{app}\AFKLocalAI.exe"; WorkingDir: "{app}"
Name: "{group}\Diagnostics"; Filename: "{app}\AFKLocalAI.exe"; Parameters: "--diagnostics"; WorkingDir: "{app}"
Name: "{group}\Data Folder"; Filename: "{app}\AFKLocalAI.exe"; Parameters: "--data-folder"; WorkingDir: "{app}"
Name: "{group}\About AFK LocalAI"; Filename: "{app}\AFKLocalAI.exe"; Parameters: "--about"; WorkingDir: "{app}"
Name: "{group}\Support"; Filename: "https://github.com/allusionsafk/localai-windows-starter/issues/new/choose"
Name: "{group}\Uninstall AFK LocalAI"; Filename: "{uninstallexe}"
Name: "{autodesktop}\AFK LocalAI"; Filename: "{app}\AFKLocalAI.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\AFKLocalAI.exe"; Description: "Launch AFK LocalAI"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{app}\AFKLocalAI.exe"; Parameters: "--stop --silent"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "StopAFKLocalAI"

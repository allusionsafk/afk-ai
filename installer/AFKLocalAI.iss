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

[Code]
// Setup deletes whole folders inside {app} ([InstallDelete]). A folder is
// AFK LocalAI's to clean only when it is:
//   - absent or empty: Setup is creating AFK's own program folder; or
//   - THIS product's registered installation (the uninstall entry for this
//     AppId records exactly this folder) AND it still holds an AFK artifact.
// The folder's NAME is never evidence. Any other non-empty folder is refused
// before installation starts, and nothing in it is touched.
const
  UninstallRoot = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\';

var
  OwnershipDecided: Boolean;
  OwnedDir: Boolean;

function SamePath(const Left, Right: String): Boolean;
begin
  Result := CompareText(RemoveBackslashUnlessRoot(ExpandFileName(Left)),
    RemoveBackslashUnlessRoot(ExpandFileName(Right))) = 0;
end;

function DirIsEmpty(const Dir: String): Boolean;
var
  FindRec: TFindRec;
begin
  Result := True;
  if FindFirst(AddBackslash(Dir) + '*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
        begin
          Result := False;
          Break;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

function IsRegisteredAfkInstallation(const Dir: String): Boolean;
var
  Registered: String;
begin
  Result := RegQueryStringValue(HKA,
      UninstallRoot + ExpandConstant('{#SetupSetting("AppId")}') + '_is1',
      'Inno Setup: App Path', Registered) and
    SamePath(Registered, Dir) and
    (FileExists(AddBackslash(Dir) + 'AFKLocalAI.exe') or
     FileExists(AddBackslash(Dir) + 'unins000.dat'));
end;

function IsAfkOwnedDir(const Dir: String): Boolean;
begin
  if not DirExists(Dir) then
    Result := True
  else if DirIsEmpty(Dir) then
    Result := True
  else
    Result := IsRegisteredAfkInstallation(Dir);
end;

function ForeignDirMessage(const Dir: String): String;
begin
  Result := 'AFK LocalAI will not install into "' + Dir + '" because that folder ' +
    'already contains files that do not belong to an AFK LocalAI installation, ' +
    'and installing replaces some of its folders. Nothing in it was changed.' + #13#10#13#10 +
    'Choose an empty folder, or a new folder name, for AFK LocalAI.';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and not IsAfkOwnedDir(WizardDirValue) then
  begin
    SuppressibleMsgBox(ForeignDirMessage(WizardDirValue), mbError, MB_OK, IDOK);
    Result := False;
  end;
end;

// The decision that authorises [InstallDelete]. Runs for interactive and silent
// installs, once the folder is final and before anything is changed.
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  OwnedDir := IsAfkOwnedDir(ExpandConstant('{app}'));
  OwnershipDecided := True;
  if OwnedDir then
    Result := ''
  else
    Result := ForeignDirMessage(ExpandConstant('{app}'));
end;

function AppDirIsOwned(): Boolean;
begin
  Result := OwnershipDecided and OwnedDir;
end;

[InstallDelete]
; PRODUCT RUNTIME is replaced wholesale on every install. Setup copies new files
; but never removes files a newer version no longer ships, so a stale module
; would stay importable and an old interpreter DLL would sit beside a new one.
; These directories only ever hold shipped product files; user data lives under
; %LOCALAPPDATA%\AFK LocalAI and in AFK's Docker volumes, never here.
;
; ONLY in a folder AFK LocalAI owns (AppDirIsOwned, [Code] above). A person can
; point Setup at any existing folder, whose runtime/src/installer/logs may be
; their own; PrepareToInstall refuses such a folder before anything is deleted.
Type: filesandordirs; Name: "{app}\runtime"; Check: AppDirIsOwned
Type: filesandordirs; Name: "{app}\src"; Check: AppDirIsOwned
Type: filesandordirs; Name: "{app}\installer"; Check: AppDirIsOwned
; Left in the program folder by the pre-runtime provisioning path (pip metadata,
; scout and firewall logs). The engine migrates the old .env secret on first run.
Type: filesandordirs; Name: "{app}\logs"; Check: AppDirIsOwned

; Removed with the program so the folder does not survive an uninstall. Nothing
; here is user content: runtime configuration and chats are elsewhere.
[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: files; Name: "{app}\.env"
Type: dirifempty; Name: "{app}"

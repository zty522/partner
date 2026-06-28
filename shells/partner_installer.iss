; Partner Windows Installer — Inno Setup 6
; Build: ISCC.exe shells/partner_installer.iss
; Output: dist/Partner_Setup.exe

#define MyAppName "Partner"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Nous Research"
#define MyAppURL "https://github.com/zty522/partner"
#define MyAppExeName "Partner.exe"

[Setup]
AppId={{B8A3C9F1-4D2E-4F8A-9B1C-5E6D7F8A9B0C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
PrivilegesRequired=admin
OutputDir=..\dist
OutputBaseFilename=Partner_Setup
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Start {#MyAppName}"; Flags: nowait postinstall skipifsilent shellexec

[Code]
var
  WorkspacePage: TInputDirWizardPage;
  CreateWorkspaceCheck: TCheckBox;

function GetUserProfileDir: string;
begin
  Result := GetEnv('USERPROFILE');
  if Result = '' then
    Result := ExpandConstant('{userappdata}\..\..');
end;

procedure InitializeWizard;
begin
  { Page order:
      1. Welcome
      2. Select Destination Location  (where to install Partner.exe)
      3. Select Tasks                  (shortcut options)
      4. Workspace Directory           (where to store instance data) ← HERE }
  WorkspacePage := CreateInputDirPage(
    wpSelectTasks,
    'Workspace Directory',
    'Where should Partner store instance data and conversation logs?',
    'This is NOT the program installation location (that was on the previous page). '
    + 'Select or create a workspace folder for your instance data.',
    False,
    'New Folder'
  );

  WorkspacePage.Add('');
  WorkspacePage.Values[0] := GetUserProfileDir() + '\partner_workspace';

  { Auto-create checkbox }
  CreateWorkspaceCheck := TCheckBox.Create(WorkspacePage);
  CreateWorkspaceCheck.Caption := 'Auto-create config/ and instances/ subdirectories';
  CreateWorkspaceCheck.Checked := True;
  CreateWorkspaceCheck.Top := 100;
  CreateWorkspaceCheck.Left := 0;
  CreateWorkspaceCheck.Width := WorkspacePage.SurfaceWidth;
  CreateWorkspaceCheck.Parent := WorkspacePage.Surface;
end;

function GetWorkspacePath(): string;
begin
  Result := WorkspacePage.Values[0];
end;

procedure WriteWorkspacePointer(WorkspacePath: string);
var
  PointerFile: string;
begin
  PointerFile := GetUserProfileDir() + '\.partner_workspace';
  SaveStringToFile(PointerFile, WorkspacePath, False);
  Log('Written pointer file: ' + PointerFile + ' -> ' + WorkspacePath);
end;

procedure CreateWorkspaceDirs(WorkspacePath: string);
begin
  if CreateWorkspaceCheck.Checked then
  begin
    ForceDirectories(WorkspacePath + '\config');
    ForceDirectories(WorkspacePath + '\instances');
    Log('Created workspace directories under: ' + WorkspacePath);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  WsPath: string;
begin
  if CurStep = ssPostInstall then
  begin
    WsPath := GetWorkspacePath();
    CreateWorkspaceDirs(WsPath);
    WriteWorkspacePointer(WsPath);
  end;
end;

function HasExistingConfig(WsPath: string): Boolean;
begin
  Result := FileExists(WsPath + '\config\partner_config.json');
end;

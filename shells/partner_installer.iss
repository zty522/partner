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
  WorkspacePage: TWizardPage;
  WsEdit: TEdit;
  WsBrowseBtn: TButton;
  CreateDirsCheck: TCheckBox;

function GetUserProfileDir: string;
begin
  Result := GetEnv('USERPROFILE');
  if Result = '' then
    Result := ExpandConstant('{userappdata}\..\..');
end;

procedure BrowseWsDir(Sender: TObject);
var
  Dir: string;
begin
  Dir := '';
  if BrowseForFolder('Select workspace directory:', Dir, False, WsEdit.Text) then
    WsEdit.Text := Dir;
end;

procedure InitializeWizard;
var
  DescLabel: TLabel;
  EditLabel: TLabel;
begin
  { Page order:
      1. Welcome
      2. Select Destination Location  (app install: C:\Program Files\Partner)
      3. Select Tasks                  (desktop shortcut)
      4. Workspace Directory           (instance data) ← THIS PAGE }
  WorkspacePage := CreateCustomPage(
    wpSelectTasks,
    'Workspace Directory',
    'Where should Partner store instance data and conversation logs?'
  );

  { Description text }
  DescLabel := TLabel.Create(WorkspacePage);
  DescLabel.Parent := WorkspacePage.Surface;
  DescLabel.Left := 0;
  DescLabel.Top := 0;
  DescLabel.Width := WorkspacePage.SurfaceWidth;
  DescLabel.Height := 40;
  DescLabel.WordWrap := True;
  DescLabel.Caption := 'This is where Partner keeps instance data, configs and conversation logs.'
    + #13#10 + 'This is NOT the program installation location (that was on the previous page).';

  { "Workspace path:" label }
  EditLabel := TLabel.Create(WorkspacePage);
  EditLabel.Parent := WorkspacePage.Surface;
  EditLabel.Left := 0;
  EditLabel.Top := 56;
  EditLabel.Width := 100;
  EditLabel.Height := 16;
  EditLabel.Caption := 'Workspace path:';

  { Path edit field }
  WsEdit := TEdit.Create(WorkspacePage);
  WsEdit.Parent := WorkspacePage.Surface;
  WsEdit.Left := 0;
  WsEdit.Top := 74;
  WsEdit.Width := WorkspacePage.SurfaceWidth - 90;
  WsEdit.Height := 23;
  WsEdit.Text := GetUserProfileDir() + '\partner_workspace';

  { Browse button }
  WsBrowseBtn := TButton.Create(WorkspacePage);
  WsBrowseBtn.Parent := WorkspacePage.Surface;
  WsBrowseBtn.Left := WsEdit.Left + WsEdit.Width + 6;
  WsBrowseBtn.Top := WsEdit.Top - 1;
  WsBrowseBtn.Width := 80;
  WsBrowseBtn.Height := 25;
  WsBrowseBtn.Caption := 'Browse...';
  WsBrowseBtn.OnClick := @BrowseWsDir;

  { Auto-create checkbox }
  CreateDirsCheck := TCheckBox.Create(WorkspacePage);
  CreateDirsCheck.Parent := WorkspacePage.Surface;
  CreateDirsCheck.Left := 0;
  CreateDirsCheck.Top := 112;
  CreateDirsCheck.Width := WorkspacePage.SurfaceWidth;
  CreateDirsCheck.Height := 20;
  CreateDirsCheck.Caption := 'Auto-create config/ and instances/ subdirectories';
  CreateDirsCheck.Checked := True;
end;

function GetWorkspacePath(): string;
begin
  Result := Trim(WsEdit.Text);
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
  if CreateDirsCheck.Checked then
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

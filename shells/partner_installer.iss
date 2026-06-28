; Partner Windows Installer — Inno Setup 6
; Build: ISCC.exe scripts\partner_installer.iss
; Output: dist\Partner_Setup.exe

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
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
LicenseFile=
PrivilegesRequired=admin
OutputDir=..\dist
OutputBaseFilename=Partner_Setup
SetupIconFile=..\partner\assets\partner_app_v2.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
; Force close running app before install
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: checkedonce
Name: "desktopicon\common"; Description: "为所有用户"; GroupDescription: "快捷方式："; Flags: exclusive
Name: "desktopicon\user"; Description: "仅为当前用户"; GroupDescription: "快捷方式："; Flags: exclusive unchecked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; No other files needed — Partner.exe is a self-contained PyInstaller build

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Launch Partner after install (user checkbox)
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent shellexec

; ── Custom wizard page: workspace directory selection ──

[Code]
var
  WorkspacePage: TInputDirWizardPage;
  CreateWorkspaceCheck: TCheckBox;

procedure InitializeWizard;
begin
  { Create a custom page for workspace selection }
  WorkspacePage := CreateInputDirPage(
    wpSelectDir,
    '选择工作区目录',
    'Partner 将在此目录中存储实例数据、配置文件和对话记录。',
    '请选择或创建一个目录，然后点击下一步。' + #13#10 +
    '推荐：C:\Users\' + GetUserNameString + '\partner_workspace',
    False,
    '新建文件夹'
  );

  { Add the default workspace path }
  WorkspacePage.Add('工作区路径:');
  WorkspacePage.Values[0] := ExpandConstant('{userdocs}\..\partner_workspace');

  { Add checkbox to auto-create directory structure }
  CreateWorkspaceCheck := TCheckBox.Create(WizardForm);
  CreateWorkspaceCheck.Caption := '自动创建 config/ 和 instances/ 子目录';
  CreateWorkspaceCheck.Checked := True;
  CreateWorkspaceCheck.Top := WorkspacePage.Surface.Top + 80;
  CreateWorkspaceCheck.Left := 0;
  CreateWorkspaceCheck.Width := WorkspacePage.Surface.Width;
  CreateWorkspaceCheck.Parent := WorkspacePage.Surface;
end;

function GetWorkspacePath(): string;
begin
  Result := WorkspacePage.Values[0];
end;

{ Write .partner_workspace pointer file }
procedure WriteWorkspacePointer(WorkspacePath: string);
var
  PointerFile: string;
begin
  PointerFile := ExpandConstant('{userappdata}\..\.partner_workspace');
  SaveStringToFile(PointerFile, WorkspacePath, False);
  Log('Written pointer file: ' + PointerFile + ' -> ' + WorkspacePath);
end;

{ Create workspace directory structure }
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

{ Check if workspace already has partner_config.json — don't overwrite }
function HasExistingConfig(WsPath: string): Boolean;
begin
  Result := FileExists(WsPath + '\config\partner_config.json');
end;

{ Show summary on the finished page }
function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  WsPath: string;
begin
  WsPath := GetWorkspacePath();
  Result :=
    '安装目录: ' + ExpandConstant('{app}') + NewLine +
    '工作区目录: ' + WsPath + NewLine +
    NewLine +
    '安装完成后将自动启动 Partner。' + NewLine +
    '首次启动时将显示配置向导，帮助您设置 LLM API。';
  if HasExistingConfig(WsPath) then
    Result := Result + NewLine + NewLine +
      '注意：检测到已有配置，工作区目录将保留现有设置。';
end;

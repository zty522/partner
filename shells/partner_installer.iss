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
DisableDirPage=no
DisableProgramGroupPage=no

; ★★★ 设置为 no，因为我们将在 [Code] 中手动处理 ★★★
CloseApplications=no

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

function GetUserProfileDir: string;
begin
  Result := GetEnv('USERPROFILE');
  if Result = '' then
    Result := 'C:\Users\Default';
end;

{ ★★★ 新增：强制终止 Partner 进程 ★★★ }
procedure KillPartnerProcess;
var
  ResultCode: Integer;
begin
  // 先尝试正常关闭
  Exec('taskkill', '/IM Partner.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  
  // 等待 1 秒
  Sleep(1000);
  
  // 如果正常关闭失败，强制终止
  if ResultCode <> 0 then
  begin
    Exec('taskkill', '/F /IM Partner.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(1000);
  end;
  
  // 再次检查是否还有残留进程
  Exec('taskkill', '/F /IM Partner.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

{ ★★★ 新增：在安装开始前调用 ★★★ }
function InitializeSetup: Boolean;
begin
  // 在安装程序初始化时，先终止 Partner 进程
  KillPartnerProcess;
  Result := True;
end;

procedure InitializeWizard;
begin
  WorkspacePage := CreateInputDirPage(
    wpSelectDir,
    'Workspace Directory',
    'Where should Partner store instance data, configs and conversation logs?',
    'Select or create a folder for your workspace data.',
    False,
    'New Folder'
  );
  WorkspacePage.Add('');
  WorkspacePage.Values[0] := GetUserProfileDir() + '\partner_workspace';
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

procedure CurStepChanged(CurStep: TSetupStep);
var
  WsPath: string;
begin
  if CurStep = ssPostInstall then
  begin
    WsPath := GetWorkspacePath();
    ForceDirectories(WsPath + '\config');
    ForceDirectories(WsPath + '\instances');
    WriteWorkspacePointer(WsPath);
  end;
end;
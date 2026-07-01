; Partner Windows Installer — Inno Setup 6
; Build: ISCC.exe shells/partner_installer.iss
; Output: dist/Partner_Setup.exe

#define MyAppName "Partner"
#define MyAppVersion "1.1.0"
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
{ ★★★ 强制终止旧版 Partner 进程（安装器自动处理进程关闭） ★★★ }
procedure KillPartnerProcess;
var
  ResultCode: Integer;
begin
  Exec('taskkill', '/IM Partner.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1000);
  if ResultCode <> 0 then
  begin
    Exec('taskkill', '/F /IM Partner.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(1000);
  end;
  Exec('taskkill', '/F /IM Partner.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function InitializeSetup: Boolean;
begin
  KillPartnerProcess;
  Result := True;
end;

{ ★★★ 安装后不创建工作区——等用户在软件中选择 ★★★ }

; Partner 🤝 Windows Installer
; Inno Setup Script
; Build with: ISCC.exe installer.iss

#define MyAppName "Partner"
#define MyAppVersion "0.7.0"
#define MyAppVerName "Partner v0.7.0"
#define MyAppPublisher "Partner Team"
#define MyAppURL "https://github.com/zty522/partner"
#define MyAppExeName "Partner.bat"
#define MyAppId "{{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppVerName}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={userpf}\Partner
DefaultGroupName=Partner
AllowNoIcons=yes
OutputDir=.\output
OutputBaseFilename=Partner-v{#MyAppVersion}-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\partner\assets\partner_app_v2.ico
UninstallDisplayIcon={app}\partner\assets\partner_app_v2.ico
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
DisableDirPage=no
CloseApplications=yes
AppMutex=PartnerMutex
UsePreviousAppDir=yes
UsePreviousGroup=yes
UpdateUninstallLogAppName=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "..\partner\*"; DestDir: "{app}\partner"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc,*.pyo"
Source: "..\dist\Partner\*"; DestDir: "{app}\Partner"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\scripts\*"; DestDir: "{app}\scripts"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc,*.pyo"
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\Partner.vbs"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\Partner.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\config.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\Partner"; Filename: "{app}\Partner\Partner.exe"; WorkingDir: "{app}\Partner"; IconFilename: "{app}\partner\assets\partner_app_v2.ico"
Name: "{userdesktop}\Partner"; Filename: "{app}\Partner\Partner.exe"; WorkingDir: "{app}\Partner"; Tasks: desktopicon; IconFilename: "{app}\partner\assets\partner_app_v2.ico"
Name: "{userprograms}\Uninstall Partner"; Filename: "{uninstallexe}"

[Code]
var
  PythonInstalled: Boolean;
  AppDir: String;

procedure UpdateExistingInstallLabel(Sender: TObject);
var
  ExistingPath: String;
begin
  ExistingPath := WizardDirValue;
  if FileExists(AddBackslash(ExistingPath) + 'Partner.vbs') or
     FileExists(AddBackslash(ExistingPath) + 'Partner\Partner.exe') then
    WizardForm.DiskSpaceLabel.Caption := 'Detected an existing Partner installation in: ' + ExistingPath
  else
    WizardForm.DiskSpaceLabel.Caption := 'No previous installation detected in the selected location.';
end;

{ ── Detect Python ── }
function CheckPython(): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec('python', '-c "import sys; exit(0) if sys.version_info >= (3,10) else exit(1)"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

{ ── Install Python if missing ── }
procedure InstallPython();
var
  InstallerPath: String;
  DownloadCode, InstallCode: Integer;
begin
  WizardForm.StatusLabel.Caption := 'Downloading Python 3.12...';
  InstallerPath := ExpandConstant('{tmp}\python-installer.exe');
  Exec('powershell',
    '-Command "Invoke-WebRequest -Uri ''https://www.python.org/ftp/python/3.12.3/python-3.12.3-amd64.exe'' -OutFile ''' + InstallerPath + '''' + '"',
    '', SW_HIDE, ewWaitUntilTerminated, DownloadCode);
  if DownloadCode <> 0 then
  begin
    Log('Python download failed');
    Exit;
  end;
  WizardForm.StatusLabel.Caption := 'Installing Python 3.12...';
  Exec(InstallerPath, '/quiet InstallAllUsers=0 PrependPath=1',
    '', SW_SHOW, ewWaitUntilTerminated, InstallCode);
  if InstallCode = 0 then
    Log('Python installed successfully')
  else
    Log('Python install failed with code: ' + IntToStr(InstallCode));
end;

{ ── Post-install: install partner package + PATH ── }
procedure RunPostInstall();
var
  ResultCode: Integer;
begin
  WizardForm.StatusLabel.Caption := 'Installing Partner package...';
  Exec('python', '-m pip install -e "' + AppDir + '" -q --break-system-packages',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if ResultCode <> 0 then
    Exec('python', '-m pip install -e "' + AppDir + '" -q',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  WizardForm.StatusLabel.Caption := 'Adding Partner to PATH...';
  Exec('powershell',
    '-Command "$p=[Environment]::GetEnvironmentVariable(''Path'',''User''); ' +
    'if($p -notlike ''*' + AppDir + '*'') ' +
    '{[Environment]::SetEnvironmentVariable(''Path'',''' + AppDir + ';$p'',''User'')}"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

{ ── Detect Python on startup ── }
procedure InitializeWizard;
begin
  PythonInstalled := CheckPython();
  WizardForm.WelcomeLabel2.Caption :=
    'Install Partner v{#MyAppVersion} on this computer.' + #13#10 +
    'You can choose the install location on the next step.';
  WizardForm.DirEdit.OnChange := @UpdateExistingInstallLabel;
  UpdateExistingInstallLabel(WizardForm.DirEdit);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  WizardForm.StatusLabel.Caption := 'Closing running Partner processes...';
  Exec('taskkill.exe', '/IM Partner.exe /T /F',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(800);
end;

{ ── Run install steps ── }
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    AppDir := ExpandConstant('{app}');
  end;

  if CurStep = ssPostInstall then
  begin
    { 1. Install Python if missing }
    if not PythonInstalled then
      InstallPython();

    { 2. Post-install: pip install + PATH }
    RunPostInstall();

    WizardForm.StatusLabel.Caption := 'Setup complete!';
  end;
end;

{ ── Uninstall ── }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Exec('powershell',
      '-Command "[Environment]::SetEnvironmentVariable(''Path'', ' +
      '$([Environment]::GetEnvironmentVariable(''Path'',''User'') -replace ''.*Partner.*'','''').Trim('';''), ''User'')"',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;

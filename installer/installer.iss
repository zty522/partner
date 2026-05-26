; Partner 🤝 Windows Installer
; Inno Setup Script
; Build with: ISCC.exe installer.iss

#define MyAppName "Partner"
#define MyAppVersion "0.3.0"
#define MyAppPublisher "Partner Team"
#define MyAppURL "https://github.com/zty522/partner"
#define MyAppExeName "partner.bat"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={userpf}\Partner
DefaultGroupName=Partner
AllowNoIcons=yes
OutputDir=.\output
OutputBaseFilename=Partner-{#MyAppVersion}-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
CloseApplications=no
; Handle upgrades
AppMutex=PartnerMutex
UsePreviousAppDir=yes
UsePreviousGroup=yes
UpdateUninstallLogAppName=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "..\partner\*"; DestDir: "{app}\partner"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\scripts\*"; DestDir: "{app}\scripts"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\Partner"; Filename: "{app}\Partner.exe"
Name: "{commondesktop}\Partner"; Filename: "{app}\Partner.exe"; Tasks: desktopicon
Name: "{userprograms}\Partner Status"; Filename: "powershell.exe"; Parameters: "-NoExit -Command partner status"
Name: "{userprograms}\Uninstall Partner"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\post_install.bat"; Description: "Complete setup (recommended)"; Flags: postinstall nowait skipifsilent shellexec

[UninstallRun]
Filename: "{app}\uninstall.bat"; Flags: runhidden

[Code]
var
  BackendPage: TInputOptionWizardPage;
  PythonPage: TInputOptionWizardPage;
  BackendChoice: Integer;
  PyCheckCode: Integer;
  PyResultCode: Integer;

procedure InitializeWizard;
begin
  { Python detection page }
  PythonPage := CreateInputOptionPage(wpWelcome,
    'Python Detection', 'Checking your system for Python...',
    'Partner requires Python 3.10 or later.',
    False, False);
  PythonPage.Add('Python 3.10+ is already installed');
  PythonPage.Add('Python not detected (will install Python 3.12)');
  PythonPage.Values[0] := True;

  { Check if Python exists }
  if Exec('python', '--version', '', SW_HIDE, ewWaitUntilTerminated, PyResultCode) then
    PythonPage.Values[0] := True
  else begin
    PythonPage.Values[0] := False;
    PythonPage.Values[1] := True;
  end;

  { Backend selection page }
  BackendPage := CreateInputOptionPage(wpSelectTasks,
    'AI Backend Selection', 'Which AI backend should Partner use?',
    'Partner needs an AI backend to process research tasks.',
    False, False);
  BackendPage.Add('Hermes Agent (recommended)');
  BackendPage.Add('OpenClaw');
  BackendPage.Add('Both');
  BackendPage.Add('Skip, I will configure later');
  BackendPage.Values[0] := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  PythonInstaller: String;
  InstallResultCode: Integer;
  DownloadResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    { Install Python if needed }
    if not PythonPage.Values[0] then
    begin
      PythonInstaller := ExpandConstant('{tmp}\python-installer.exe');
      if not FileExists(PythonInstaller) then
      begin
        { Download Python installer using PowerShell }
        if Exec('powershell', '-Command "Invoke-WebRequest -Uri ''https://www.python.org/ftp/python/3.12.3/python-3.12.3-amd64.exe'' -OutFile ''"' + PythonInstaller + '"''"', '', SW_HIDE, ewWaitUntilTerminated, DownloadResultCode) then
        begin
          if DownloadResultCode <> 0 then
          begin
            Log('Failed to download Python installer, error code: ' + IntToStr(DownloadResultCode));
            Exit;
          end;
        end
        else
        begin
          Log('Failed to execute PowerShell for download');
          Exit;
        end;
      end;
      if Exec(PythonInstaller, '/quiet InstallAllUsers=0 PrependPath=1', '', SW_SHOW, ewWaitUntilTerminated, InstallResultCode) then
        Log('Python installed successfully')
      else
        Log('Python installation failed, error code: ' + IntToStr(InstallResultCode));
    end;

    { Install selected backend }
    case BackendPage.SelectedValueIndex of
      0: { Hermes }
        Exec('python', '-m pip install hermes-agent -q', '', SW_HIDE, ewWaitUntilTerminated, InstallResultCode);
      1: { OpenClaw }
        Exec('npm', 'install -g openclaw@latest', '', SW_HIDE, ewWaitUntilTerminated, InstallResultCode);
      2: { Both }
        begin
          Exec('python', '-m pip install hermes-agent -q', '', SW_HIDE, ewWaitUntilTerminated, InstallResultCode);
          Exec('npm', 'install -g openclaw@latest', '', SW_HIDE, ewWaitUntilTerminated, InstallResultCode);
        end;
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  UninstallResultCode: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    { Remove PATH entry }
    Exec('powershell', '-Command "[Environment]::SetEnvironmentVariable(\"Path\", $([Environment]::GetEnvironmentVariable(\"Path\", \"User\") -replace \".*Partner.*\", \"\").Trim(\";\"), \"User\")"', '', SW_HIDE, ewWaitUntilTerminated, UninstallResultCode);
  end;
end;

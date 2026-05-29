<#
.SYNOPSIS
    Partner - Windows One-Click Installer
.DESCRIPTION
    Installs Partner natively on Windows (no WSL required).
    Usage:
      # One-liner (recommended)
      powershell -Command "& { iwr -useb https://raw.githubusercontent.com/zty522/partner/main/scripts/install.ps1 } | iex"

      # Download and run
      powershell -ExecutionPolicy Bypass -File install.ps1
#>

# ── Encoding ──
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "Installing Partner"

$PartnerRepo = "https://github.com/zty522/partner.git"
$PartnerDir  = "$env:USERPROFILE\.partner"

# ── Colors ──
$cGreen  = "Green"
$cYellow = "Yellow"
$cRed    = "Red"
$cCyan   = "Cyan"

function Write-Info   { Write-Host "`u{2713} " -ForegroundColor $cGreen -NoNewline;  Write-Host "$args" }
function Write-Warn   { Write-Host "`u{26A0} " -ForegroundColor $cYellow -NoNewline; Write-Host "$args" }
function Write-Err    { Write-Host "`u{2717} " -ForegroundColor $cRed -NoNewline;    Write-Host "$args" }
function Write-Header { Write-Host "`n--- $args ---`n" -ForegroundColor $cCyan }

function Exit-Error($msg) {
    Write-Err $msg
    exit 1
}

# ── Detect System ──
Write-Header "Checking system"
Write-Info "Windows $([Environment]::OSVersion.Version)"

# ── Python detection ──
function Test-PythonVersion($pyCmd) {
    try {
        $ver = & $pyCmd --version 2>&1
        if ($ver -match "(\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            return @{Path=$pyCmd; Major=$major; Minor=$minor; Full="$major.$minor"}
        }
    } catch {}
    return $null
}

function Find-InstalledPython {
    foreach ($cmd in @("python", "python3")) {
        $result = Test-PythonVersion $cmd
        if ($result -ne $null) { return $result }
    }
    return $null
}

Write-Header "Checking Python"

$Python = Find-InstalledPython

if ($Python) {
    $verStr = $Python.Full
    Write-Info "Python $verStr found: $($Python.Path)"

    if ($Python.Major -ne 3) {
        Exit-Error "Python 3 is required. Found Python $verStr."
    }

    if ($Python.Minor -lt 10) {
        Exit-Error "Python 3.10+ is required. Found Python $verStr."
    }

    if ($Python.Minor -gt 12) {
        Exit-Error "Python $verStr is not supported. Partner requires Python 3.10, 3.11, or 3.12."
    }
} else {
    Write-Warn "Python 3 not detected"
    $choice = Read-Host "  Download and install Python 3.12 automatically? (Y/n)"
    if ($choice -eq "n") {
        Exit-Error "Please install Python 3.10, 3.11, or 3.12 manually and re-run this installer."
    }

    Write-Info "Downloading Python 3.12..."
    $url = "https://www.python.org/ftp/python/3.12.3/python-3.12.3-amd64.exe"
    $installer = "$env:TEMP\python-3.12.3-amd64.exe"
    try {
        Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing -ErrorAction Stop
    } catch {
        Exit-Error "Failed to download Python installer: $_"
    }

    Write-Info "Installing Python 3.12..."
    try {
        $proc = Start-Process -Wait -FilePath $installer -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1" -PassThru
        if ($proc.ExitCode -ne 0) {
            Exit-Error "Python installer exited with code $($proc.ExitCode). Installation may have failed."
        }
    } catch {
        Exit-Error "Failed to install Python: $_"
    }
    Remove-Item $installer -Force -ErrorAction SilentlyContinue

    # Refresh PATH
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")

    $Python = Find-InstalledPython
    if (-not $Python) {
        Exit-Error "Python was installed but could not be detected. Try restarting your terminal and re-running the installer."
    }
    Write-Info "Python $($Python.Full) installed successfully"
}

# ── Git detection ──
Write-Header "Checking Git"

$GitInstalled = $false
try {
    $gitVer = & git --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        $GitInstalled = $true
        Write-Info "Git: $($gitVer | Select-Object -First 1)"
    }
} catch {}

if (-not $GitInstalled) {
    Write-Warn "Git not detected"
    $choice = Read-Host "  Download and install Git for Windows automatically? (Y/n)"
    if ($choice -eq "n") {
        Exit-Error "Please install Git manually and re-run this installer."
    }

    Write-Info "Downloading Git for Windows..."
    $gitUrl = "https://github.com/git-for-windows/git/releases/download/v2.45.0.windows.1/Git-2.45.0-64-bit.exe"
    $gitInstaller = "$env:TEMP\git-installer.exe"
    try {
        Invoke-WebRequest -Uri $gitUrl -OutFile $gitInstaller -UseBasicParsing -ErrorAction Stop
    } catch {
        Exit-Error "Failed to download Git installer: $_"
    }

    Write-Info "Installing Git for Windows..."
    try {
        $proc = Start-Process -Wait -FilePath $gitInstaller -ArgumentList "/VERYSILENT", "/NORESTART" -PassThru
        if ($proc.ExitCode -ne 0) {
            Exit-Error "Git installer exited with code $($proc.ExitCode). Installation may have failed."
        }
    } catch {
        Exit-Error "Failed to install Git: $_"
    }
    Remove-Item $gitInstaller -Force -ErrorAction SilentlyContinue

    # Refresh PATH
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")

    # Verify Git is now available
    try {
        $gitVer = & git --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Info "Git installed: $($gitVer | Select-Object -First 1)"
        } else {
            Exit-Error "Git was installed but could not be detected. Try restarting your terminal and re-running the installer."
        }
    } catch {
        Exit-Error "Git was installed but could not be detected. Try restarting your terminal and re-running the installer."
    }
}

# ── Clone / Update repository ──
Write-Header "Setting up Partner repository"

if (Test-Path $PartnerDir) {
    Write-Info "Repository directory exists: $PartnerDir"
    try {
        Push-Location $PartnerDir
        & git pull --ff-only 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "Git pull failed, continuing with existing repository"
        } else {
            Write-Info "Repository updated"
        }
        Pop-Location
    } catch {
        Write-Warn "Could not update repository: $_"
        Pop-Location
    }
} else {
    Write-Info "Cloning Partner repository..."
    try {
        & git clone $PartnerRepo $PartnerDir
        if ($LASTEXITCODE -ne 0) {
            Exit-Error "Failed to clone repository. Check your internet connection."
        }
        Write-Info "Repository cloned successfully"
    } catch {
        Exit-Error "Failed to clone repository: $_"
    }
}

# ── Install via pip ──
Write-Header "Installing Partner"

try {
    Push-Location $PartnerDir

    Write-Info "Running pip install -e . --user ..."
    $pipOutput = & $Python.Path -m pip install -e . --user 2>&1
    if ($LASTEXITCODE -ne 0) {
        Pop-Location
        Exit-Error "pip install failed. Error: $pipOutput"
    }

    Pop-Location
} catch {
    Pop-Location
    Exit-Error "pip install failed: $_"
}

Write-Info "Partner package installed"

# ── Verify partner.exe was installed and find Scripts directory ──
Write-Header "Configuring PATH"

# Standard location for pip --user install on Windows
$PythonVerTag = "Python$($Python.Major)$($Python.Minor)"
$ScriptsDir = "$env:APPDATA\Python\$PythonVerTag\Scripts"
$ScriptsFound = $false
$FoundScriptsDir = ""

# Verify the directory exists and contains the partner executable
if (Test-Path "$ScriptsDir\partner.exe" -PathType Leaf) {
    $FoundScriptsDir = $ScriptsDir
    $ScriptsFound = $true
} elseif (Test-Path "$ScriptsDir\partner" -PathType Leaf) {
    $FoundScriptsDir = $ScriptsDir
    $ScriptsFound = $true
} else {
    # Fallback: search common locations
    Write-Warn "partner executable not found in $ScriptsDir"
    Write-Info "Searching for partner executable in common Scripts directories..."
    $candidateDirs = @(
        "$env:APPDATA\Python\Python312\Scripts",
        "$env:APPDATA\Python\Python311\Scripts",
        "$env:APPDATA\Python\Python310\Scripts",
        "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts",
        "$env:LOCALAPPDATA\Programs\Python\Python311\Scripts",
        "$env:LOCALAPPDATA\Programs\Python\Python310\Scripts"
    )
    foreach ($dir in $candidateDirs) {
        if (Test-Path "$dir\partner.exe" -PathType Leaf) {
            $FoundScriptsDir = $dir
            $ScriptsFound = $true
            break
        }
    }
}

if ($ScriptsFound) {
    Write-Info "Found partner executable in: $FoundScriptsDir"

    # Add to user PATH permanently
    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($currentPath -split ";" -notcontains $FoundScriptsDir) {
        try {
            [Environment]::SetEnvironmentVariable("Path", "$currentPath;$FoundScriptsDir", "User")
            Write-Info "Added to user PATH: $FoundScriptsDir"
        } catch {
            Write-Warn "Could not modify PATH: $_"
        }
    } else {
        Write-Info "Already in user PATH: $FoundScriptsDir"
    }
} else {
    Write-Warn "Could not locate partner.exe in any Scripts directory."
    Write-Warn "Partner installed but not added to PATH. You may need to add the directory manually."
    Write-Warn "Look for partner.exe in: $ScriptsDir"
}

# ── Desktop shortcut (optional) ──
Write-Header "Desktop shortcut"

$createShortcut = Read-Host "  Create a desktop shortcut for Partner? (Y/n)"
if ($createShortcut -ne "n") {
    try {
        $DesktopPath  = [Environment]::GetFolderPath("Desktop")
        $ShortcutPath = "$DesktopPath\Partner.lnk"
        $WScriptShell = New-Object -ComObject WScript.Shell
        $Shortcut     = $WScriptShell.CreateShortcut($ShortcutPath)
        $Shortcut.TargetPath         = "wscript.exe"
        $Shortcut.Arguments          = "$PartnerDir\Partner.vbs"
        $Shortcut.Description        = "Partner - Your AI Research Companion"
        $Shortcut.WorkingDirectory   = "$PartnerDir"
        $Shortcut.IconLocation       = "$PartnerDir\Partner.exe, 0"
        $Shortcut.Save()
        Write-Info "Desktop shortcut created: $ShortcutPath"
    } catch {
        Write-Warn "Could not create desktop shortcut: $_"
    }
} else {
    Write-Info "Desktop shortcut skipped"
}

# ── Done ──
Write-Header "Installation complete"
Write-Host ""
Write-Host "  `u{2713} Partner installed successfully. Restart your terminal and run 'partner' to start." -ForegroundColor $cGreen
Write-Host ""
Write-Host "  Installation directory: $PartnerDir" -ForegroundColor $cCyan
if ($ScriptsFound) {
    Write-Host "  Scripts directory:      $FoundScriptsDir" -ForegroundColor $cCyan
}
Write-Host ""

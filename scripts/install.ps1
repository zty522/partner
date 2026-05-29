<#
.SYNOPSIS
    Partner - Universal Windows Installer
.DESCRIPTION
    Installs Partner natively on Windows (no WSL required).
    Detects Python version and installs 3.12 if needed.

    Usage:
      powershell -Command "& { iwr -useb https://raw.githubusercontent.com/zty522/partner/main/scripts/install.ps1 } | iex"
      powershell -ExecutionPolicy Bypass -File install.ps1
#>

# ── Encoding ──
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "Installing Partner"

$PartnerRepo = "https://github.com/zty522/partner.git"
$PartnerDir  = "$env:USERPROFILE\.partner"
$PythonDir   = "$PartnerDir\python"
$PythonVer   = "3.12.3"
$PythonUrl   = "https://www.python.org/ftp/python/$PythonVer/python-${PythonVer}-amd64.exe"

# ── Colors ──
$cGreen  = "Green"
$cYellow = "Yellow"
$cRed    = "Red"
$cCyan   = "Cyan"

function Write-Info   { Write-Host "$([char]0x2713) " -ForegroundColor $cGreen -NoNewline;  Write-Host "$args" }
function Write-Warn   { Write-Host "$([char]0x26A0) " -ForegroundColor $cYellow -NoNewline; Write-Host "$args" }
function Write-Err    { Write-Host "$([char]0x2717) " -ForegroundColor $cRed -NoNewline;    Write-Host "$args" }
function Write-Header { Write-Host "`n--- $args ---`n" -ForegroundColor $cCyan }

function Exit-Error($msg) {
    Write-Err $msg
    exit 1
}

# ── Detect System ──
Write-Header "Checking system"
Write-Info "Windows $([Environment]::OSVersion.Version)"

# ── Python helpers ──
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

function Test-ValidPython($py) {
    if ($py -eq $null) { return $false }
    return ($py.Major -eq 3 -and $py.Minor -ge 10 -and $py.Minor -le 12)
}

function Find-InstalledPython {
    foreach ($cmd in @("python", "python3")) {
        $result = Test-PythonVersion $cmd
        if ($result -ne $null) { return $result }
    }
    return $null
}

# ── Refresh PATH from registry ──
function Refresh-Path {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $env:Path = "$userPath;$machinePath"
}

# ── Install Python 3.12 to isolated directory ──
function Install-PythonIsolated {
    Write-Info "Downloading Python $PythonVer installer..."
    $installer = "$env:TEMP\python-$PythonVer-amd64.exe"
    try {
        Invoke-WebRequest -Uri $PythonUrl -OutFile $installer -UseBasicParsing -ErrorAction Stop
    } catch {
        Exit-Error "Failed to download Python installer: $_"
    }

    Write-Info "Installing Python $PythonVer to $PythonDir ..."
    try {
        $proc = Start-Process -Wait -FilePath $installer -ArgumentList @(
            "/quiet",
            "InstallAllUsers=0",
            "Include_test=0",
            "Include_launcher=0",
            "InstallLauncherAllUsers=0",
            "TargetDir=$PythonDir",
            "PrependPath=0"
        ) -PassThru

        if ($proc.ExitCode -ne 0) {
            Exit-Error "Python installer exited with code $($proc.ExitCode). Installation may have failed."
        }
    } catch {
        Exit-Error "Failed to install Python: $_"
    }
    Remove-Item $installer -Force -ErrorAction SilentlyContinue

    # Verify the isolated Python
    Refresh-Path
    $pyPath = "$PythonDir\python.exe"
    if (-not (Test-Path $pyPath)) {
        Exit-Error "Python was installed but python.exe not found at $pyPath"
    }

    $pyInfo = Test-PythonVersion $pyPath
    if ($pyInfo -eq $null) {
        Exit-Error "Installed Python could not be detected at $pyPath"
    }

    Write-Info "Python $($pyInfo.Full) installed at $PythonDir"
    return $pyInfo
}

# ── Ensure valid Python ──
function Ensure-Python {
    $py = Find-InstalledPython

    if ($py -ne $null -and (Test-ValidPython $py)) {
        Write-Info "Python $($py.Full) found: $($py.Path)"
        return $py
    }

    # System Python exists but wrong version
    if ($py -ne $null) {
        Write-Warn "Python $($py.Full) detected but Partner requires 3.10, 3.11, or 3.12."
        Write-Info "Installing Python 3.12 to isolated directory..."
        return Install-PythonIsolated
    }

    # No Python at all - try winget first
    Write-Warn "Python 3 not detected on this system."

    try {
        $wingetCheck = Get-Command winget -ErrorAction Stop
        Write-Info "winget available. Installing Python 3.12 via winget..."
        $proc = Start-Process -Wait -FilePath "winget" -ArgumentList @(
            "install", "Python.Python.3.12", "--silent", "--accept-package-agreements"
        ) -PassThru

        if ($proc.ExitCode -eq 0) {
            Refresh-Path
            $py = Find-InstalledPython
            if ($py -ne $null -and (Test-ValidPython $py)) {
                Write-Info "Python $($py.Full) installed via winget"
                return $py
            }
        }
        Write-Warn "winget install did not result in a detectable Python. Falling back to direct download..."
    } catch {
        Write-Warn "winget not available. Downloading Python directly..."
    }

    # Fallback: download and install to isolated directory
    return Install-PythonIsolated
}

# ── Ensure Git ──
function Ensure-Git {
    try {
        $gitVer = & git --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Info "Git: $($gitVer | Select-Object -First 1)"
            return $true
        }
    } catch {}

    Write-Warn "Git not detected."
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
        $proc = Start-Process -Wait -FilePath $gitInstaller -ArgumentList @("/VERYSILENT", "/NORESTART") -PassThru
        if ($proc.ExitCode -ne 0) {
            Exit-Error "Git installer exited with code $($proc.ExitCode)."
        }
    } catch {
        Exit-Error "Failed to install Git: $_"
    }
    Remove-Item $gitInstaller -Force -ErrorAction SilentlyContinue

    Refresh-Path

    try {
        $gitVer = & git --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Info "Git installed: $($gitVer | Select-Object -First 1)"
            return $true
        }
    } catch {}
    Exit-Error "Git was installed but could not be detected. Try restarting your terminal and re-running the installer."
}

# ── Clone repository ──
function Install-Repository {
    if (Test-Path $PartnerDir) {
        Write-Info "Repository directory exists: $PartnerDir"
        return
    }

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
function Install-PartnerPackage {
    Push-Location $PartnerDir
    try {
        $pythonExe = $Python.Path
        Write-Info "Running pip install -e . ..."
        $pipOutput = & $pythonExe -m pip install -e . -q 2>&1
        if ($LASTEXITCODE -ne 0) {
            Pop-Location
            Exit-Error "pip install failed. Error: $pipOutput"
        }
        Write-Info "Partner package installed"
        Pop-Location
    } catch {
        Pop-Location
        Exit-Error "pip install failed: $_"
    }
}

# ── Add to PATH ──
function Add-PartnerToPath {
    $scriptsDir = ""
    $pythonDir = Split-Path -Parent (Split-Path -Parent $Python.Path)

    # Determine Scripts directory based on where Python is
    $candidates = @(
        "$pythonDir\Scripts",                                            # e.g. ~\.partner\python\Scripts
        "$env:APPDATA\Python\Python$($Python.Major)$($Python.Minor)\Scripts",
        "$pythonDir\..\..\Scripts"                                       # another common layout
    )

    # Also check for pip-installed scripts dir
    $pipScripts = & $Python.Path -m site --user-site 2>&1
    if ($pipScripts -match "^(.*)\\site-packages") {
        $candidates += "$($Matches[1])\Scripts"
    }

    foreach ($dir in $candidates) {
        $resolved = [System.IO.Path]::GetFullPath($dir)
        if (Test-Path "$resolved\partner.exe" -PathType Leaf) {
            $scriptsDir = $resolved
            break
        }
        if (Test-Path "$resolved\partner" -PathType Leaf) {
            $scriptsDir = $resolved
            break
        }
    }

    if (-not $scriptsDir) {
        # Search more broadly
        foreach ($dir in @(
            "$env:APPDATA\Python\Python312\Scripts",
            "$env:APPDATA\Python\Python311\Scripts",
            "$env:APPDATA\Python\Python310\Scripts",
            "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts",
            "$env:LOCALAPPDATA\Programs\Python\Python311\Scripts",
            "$env:LOCALAPPDATA\Programs\Python\Python310\Scripts"
        )) {
            if (Test-Path "$dir\partner.exe" -PathType Leaf) {
                $scriptsDir = $dir
                break
            }
        }
    }

    if ($scriptsDir) {
        Write-Info "Found partner executable in: $scriptsDir"

        # Add to current session
        $env:Path = "$scriptsDir;$env:Path"

        # Add to user PATH permanently
        $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if ($currentPath -split ";" -notcontains $scriptsDir) {
            try {
                [Environment]::SetEnvironmentVariable("Path", "$currentPath;$scriptsDir", "User")
                Write-Info "Added to user PATH: $scriptsDir"
            } catch {
                Write-Warn "Could not modify PATH: $_"
            }
        } else {
            Write-Info "Already in user PATH: $scriptsDir"
        }
    } else {
        Write-Warn "Could not locate partner.exe in any Scripts directory."
        Write-Warn "Partner installed but not added to PATH automatically."
        Write-Warn "You may need to manually add the Scripts directory to your PATH."
    }
}

# ═══════════════════════════════════════════════
# MAIN INSTALLATION
# ═══════════════════════════════════════════════

Write-Header "Installing Partner"

# Step 1: Ensure valid Python
$Python = Ensure-Python

# Step 2: Ensure Git
Ensure-Git

# Step 3: Clone repository
Install-Repository

# Step 4: Install via pip
Install-PartnerPackage

# Step 5: Add to PATH
Add-PartnerToPath

# Step 6: Done
Write-Header "Installation complete"
Write-Host ""
Write-Host "  $([char]0x2713) Partner installed successfully. Restart your terminal and run 'partner' to start." -ForegroundColor $cGreen
Write-Host ""
Write-Host "  Installation directory: $PartnerDir" -ForegroundColor $cCyan
Write-Host ""

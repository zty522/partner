<#
.SYNOPSIS
    Partner - Universal Windows Installer
.DESCRIPTION
    Installs Partner natively on Windows (no WSL required).
    Uses the embeddable Python package (no admin rights needed).

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
$PythonVerShort = "312"
$PythonZipUrl = "https://www.python.org/ftp/python/$PythonVer/python-${PythonVer}-embed-amd64.zip"

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
    return ($py.Major -eq 3 -and $py.Minor -ge 10)
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

# ── Install Python (embeddable zip package) to isolated directory ──
function Install-PythonIsolated {
    # Ensure target directory exists
    if (-not (Test-Path $PythonDir)) {
        New-Item -ItemType Directory -Path $PythonDir -Force | Out-Null
    }

    $zipFile = "$env:TEMP\python-${PythonVer}-embed-amd64.zip"

    Write-Info "Downloading Python $PythonVer embeddable package..."
    try {
        Invoke-WebRequest -Uri $PythonZipUrl -OutFile $zipFile -UseBasicParsing -ErrorAction Stop
    } catch {
        Exit-Error "Failed to download Python embeddable package: $_"
    }

    Write-Info "Extracting Python $PythonVer to $PythonDir ..."
    try {
        Expand-Archive -Path $zipFile -DestinationPath $PythonDir -Force
    } catch {
        Exit-Error "Failed to extract Python embeddable package: $_"
    }
    Remove-Item $zipFile -Force -ErrorAction SilentlyContinue

    # Verify the extracted Python
    $pyPath = "$PythonDir\python.exe"
    if (-not (Test-Path $pyPath)) {
        Exit-Error "Python was extracted but python.exe not found at $pyPath"
    }

    # Enable site-packages by properly configuring the ._pth file
    $pthFile = "$PythonDir\\python${PythonVerShort}._pth"
    $sitePackagesDir = "$PythonDir\\Lib\\site-packages"
    
    Write-Host "✓ Configuring embedded Python for site-packages and pip..." -ForegroundColor Cyan
    
    # 1. Ensure Lib\site-packages directory exists
    if (-not (Test-Path $sitePackagesDir)) {
        New-Item -ItemType Directory -Force -Path $sitePackagesDir | Out-Null
    }
    
    # 2. Modify the _pth file
    if (Test-Path $pthFile) {
        $pthContent = Get-Content $pthFile
        
        # Add 'Lib\site-packages' path if not already present
        if ($pthContent -notcontains "Lib\site-packages") {
            $pthContent = $pthContent + "Lib\site-packages"
        }
        # Uncomment 'import site' to activate package discovery
        $pthContent = $pthContent -replace '#import site', 'import site'
        
        [System.IO.File]::WriteAllLines($pthFile, $pthContent)
    } else {
        Write-Warn "._pth file not found at $pthFile — creating one..."
        $pthContent = @"
python${PythonVerShort}.zip
.
Lib\site-packages
import site
"@
        [System.IO.File]::WriteAllText($pthFile, $pthContent)
    }

    # Bootstrap pip via get-pip.py
    $getPipUrl = "https://bootstrap.pypa.io/get-pip.py"
    $getPipScript = "$env:TEMP\get-pip.py"
    Write-Info "Downloading get-pip.py..."
    try {
        Invoke-WebRequest -Uri $getPipUrl -OutFile $getPipScript -UseBasicParsing -ErrorAction Stop
    } catch {
        Exit-Error "Failed to download get-pip.py: $_"
    }

    Write-Info "Bootstrapping pip..."
    try {
        $pipOutput = & $pyPath $getPipScript --no-warn-script-location 2>&1
        if ($LASTEXITCODE -ne 0) {
            Exit-Error "pip bootstrap failed: $pipOutput"
        }
        # Also install setuptools and wheel (required for editable installs)
        Write-Info "Installing setuptools and wheel..."
        $null = & $pyPath -m pip install setuptools wheel -q --no-warn-script-location 2>&1
    } catch {
        Exit-Error "Failed to bootstrap pip: $_"
    }
    Remove-Item $getPipScript -Force -ErrorAction SilentlyContinue

    $pyInfo = Test-PythonVersion $pyPath
    if ($pyInfo -eq $null) {
        Exit-Error "Installed Python could not be detected at $pyPath"
    }

    Write-Info "Python $($pyInfo.Full) installed (embeddable) at $PythonDir"
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
        Write-Warn "Python $($py.Full) detected but Partner requires Python 3.10+."
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
        Write-Warn "winget install did not result in a detectable Python. Falling back to embeddable package..."
    } catch {
        Write-Warn "winget not available. Downloading embeddable Python directly..."
    }

    # Fallback: download and install embeddable Python to isolated directory
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
        
        # Verify pip works first
        # 2>&1 is safe here because pip --version doesn't emit WARNING to stderr
        $pipVer = & $pythonExe -m pip --version 2>&1
        if ($LASTEXITCODE -ne 0) {
            Pop-Location
            Exit-Error "pip is not working. Output: $pipVer"
        }
        Write-Info "pip: $($pipVer.Trim())"
        
        # ── pip install ──
        # PowerShell 5.1 ($ErrorActionPreference=Stop) problem:
        #   2>&1 converts stderr lines to ErrorRecord objects, which trigger Stop
        #   even when redirected to the output stream, causing RemoteException.
        # Solution: use 2>$null to discard stderr, then check $LASTEXITCODE.
        # (pip's error messages go to stdout as well when install fails.)
        
        # Pre-uninstall old version silently — handles "no RECORD file" error
        # that occurs when pip tries to upgrade an editable install without a RECORD.
        & $pythonExe -m pip uninstall -y partner-research 2>$null | Out-Null
        
        # Now install fresh. stderr goes to $null to avoid $ErrorActionPreference=Stop
        # converting pip warnings into terminating errors.
        & $pythonExe -m pip install -e . --no-warn-script-location 2>$null
        $pipExitCode = $LASTEXITCODE
        
        # ── Check result ──
        # If install failed but package is already importable (common on re-run),
        # treat it as success. The real test is whether partner actually works.
        if ($pipExitCode -ne 0) {
            python -c "import partner" 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Warn "pip re-install had issues (exit $pipExitCode), but partner is already installed and importable."
            } else {
                Pop-Location
                Exit-Error "pip install failed (exit code: $pipExitCode). Run manually: $pythonExe -m pip install -e ."
            }
        }
        
        Write-Info "Partner package installed"
        Pop-Location
    } catch {
        Pop-Location
        # If we get here, something threw unexpectedly despite the 2>$null fix
        $errMsg = $_.ToString()
        # Check if package is actually usable despite the error
        python -c "import partner" 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Warn "pip install completed with non-fatal issue: $errMsg"
            return
        }
        Exit-Error "pip install failed: $errMsg"
    }
}

# ── Add Python Scripts to PATH ──
function Add-PythonScriptsToPath {
    <#
    .SYNOPSIS
        Add Python Scripts directory to user PATH permanently.
    #>
    # 获取 Python Scripts 目录
    # 方法1: 通过 python -m site --user-site 定位用户级 Scripts
    $userSite = & python -m site --user-site 2>$null
    $userScriptsDir = ""
    if ($userSite) {
        $userScriptsDir = Join-Path (Split-Path $userSite -Parent) "Scripts"
    }
    
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    $sysScriptsDir = if ($pythonCmd) { Join-Path (Split-Path $pythonCmd.Source -Parent) "Scripts" } else { "" }
    $pyDir = if ($pythonCmd) { Split-Path $pythonCmd.Source -Parent } else { "" }

    $pythonPaths = @()
    if ($userScriptsDir) { $pythonPaths += $userScriptsDir }
    if ($sysScriptsDir)  { $pythonPaths += $sysScriptsDir }
    if ($pyDir)          { $pythonPaths += $pyDir }
    if ($PythonDir)      { $pythonPaths += (Join-Path $PythonDir "Scripts") }

    $scriptsDir = $null
    foreach ($p in $pythonPaths) {
        if ($p -and (Test-Path $p)) {
            # 确认该目录下有 partner.exe
            if (Test-Path (Join-Path $p "partner.exe") -PathType Leaf) {
                $scriptsDir = $p
                break
            }
            # 没有 partner.exe 但目录存在，作为后备
            if (-not $scriptsDir) {
                $scriptsDir = $p
            }
        }
    }

    if (-not $scriptsDir) {
        Write-Warn "Could not find Python Scripts directory"
        return
    }

    # 检查是否已在 PATH 中
    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($currentPath -split ";" | Where-Object { $_ -eq $scriptsDir }) {
        Write-Info "$scriptsDir already in PATH"
        return
    }

    # 添加到用户级 PATH
    $newPath = "$currentPath;$scriptsDir"
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")

    # 刷新当前会话
    $env:Path = "$env:Path;$scriptsDir"

    Write-Info "Added to PATH: $scriptsDir"
    Write-Warn "Please restart your terminal for changes to take effect."
}

# ── Add to PATH ──
function Add-PartnerToPath {
    $scriptsDir = ""

    # Resolve "python" command name to a full path so Split-Path works
    $pythonCmd = Get-Command $Python.Path -ErrorAction SilentlyContinue
    $pythonFullPath = if ($pythonCmd) { $pythonCmd.Source } else { "" }
    
    if ($pythonFullPath) {
        $pythonDir = Split-Path -Parent (Split-Path -Parent $pythonFullPath)
    } else {
        $pythonDir = ""
    }

    # Determine Scripts directory based on where Python is
    $candidates = @(
        "$pythonDir\Scripts",                                            # e.g. ~\.partner\python\Scripts
        "$env:APPDATA\Python\Python$($Python.Major)$($Python.Minor)\Scripts",
        "$pythonDir\..\..\Scripts"                                       # another common layout
    )

    # Also check for pip-installed scripts dir (use 2>$null to avoid $ErrorActionPreference=Stop)
    $pipScripts = & $Python.Path -m site --user-site 2>$null
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
            "$env:APPDATA\Python\Python314\Scripts",
            "$env:APPDATA\Python\Python313\Scripts",
            "$env:APPDATA\Python\Python312\Scripts",
            "$env:APPDATA\Python\Python311\Scripts",
            "$env:APPDATA\Python\Python310\Scripts",
            "$env:LOCALAPPDATA\Programs\Python\Python314\Scripts",
            "$env:LOCALAPPDATA\Programs\Python\Python313\Scripts",
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
$null = Ensure-Git

# Step 3: Clone repository
Install-Repository

# Step 4: Install via pip
Install-PartnerPackage

# Step 4b: Add Python Scripts to PATH
Add-PythonScriptsToPath

# Step 5: Add to PATH
Add-PartnerToPath

# Step 6: Done
Write-Header "Installation complete"
Write-Host ""
Write-Host "  $([char]0x2713) Partner installed successfully." -ForegroundColor $cGreen
Write-Host ""
Write-Host "  Installation directory: $PartnerDir" -ForegroundColor $cCyan
Write-Host ""

# Step 7: Interactive setup wizard
Write-Header "Setup wizard"
Write-Host ""
Write-Host "  Now configure your Partner." -ForegroundColor $cCyan
Write-Host "  You'll need your QQ Bot AppID and AppSecret from https://q.qq.com"
Write-Host ""
try {
    $setupResult = & partner setup </dev/tty 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Info "Partner configured successfully!"
    } else {
        Write-Warn "Setup exited with code $LASTEXITCODE. Run 'partner setup' later."
    }
} catch {
    Write-Warn "Setup wizard failed: $_"
    Write-Warn "You can run 'partner setup' manually later."
}
Write-Host ""
Write-Host "  $([char]0x2713) All done! Restart your terminal and run 'partner' to get started." -ForegroundColor $cGreen
Write-Host ""

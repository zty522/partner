<#
.SYNOPSIS
    Partner 🤝 — Windows 一键安装脚本
.DESCRIPTION
    在 Windows 上原生安装 Partner，无需 WSL。
    用法: powershell -ExecutionPolicy Bypass -File install.ps1
#>

$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "Installing Partner 🤝"

$Green  = "Green"
$Yellow = "Yellow"
$Red    = "Red"
$Cyan   = "Cyan"

function Write-Info  { Write-Host "✓ " -ForegroundColor $Green -NoNewline; Write-Host "$args" }
function Write-Warn  { Write-Host "⚠ " -ForegroundColor $Yellow -NoNewline; Write-Host "$args" }
function Write-Error { Write-Host "✗ " -ForegroundColor $Red -NoNewline; Write-Host "$args" }
function Write-Header { Write-Host "`n━━━ $args ━━━`n" -ForegroundColor $Cyan }

# ── 检测系统 ──
Write-Header "检测系统环境"
Write-Info "Windows $([Environment]::OSVersion.Version)"

# ── 安装目录 ──
$PartnerDir = "$env:USERPROFILE\.partner"

# ── 检测 Python ──
function Get-PythonPath {
    foreach ($cmd in @("python", "python3")) {
        try {
            $ver = & $cmd --version 2>&1
            if ($ver -match "(\d+)\.(\d+)") {
                $major = [int]$Matches[1]; $minor = [int]$Matches[2]
                if ($major -ge 3 -and $minor -ge 10) { return @{Path=$cmd; Version="$major.$minor"} }
            }
        } catch { continue }
    }
    return $null
}

$Python = Get-PythonPath
if (-not $Python) {
    Write-Warn "需要 Python 3.10+"
    $choice = Read-Host "  是否自动下载安装 Python? (Y/n)"
    if ($choice -ne "n") {
        Write-Info "正在下载 Python 3.12..."
        $url = "https://www.python.org/ftp/python/3.12.3/python-3.12.3-amd64.exe"
        $installer = "$env:TEMP\python-installer.exe"
        Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
        Start-Process -Wait -FilePath $installer -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1"
        Remove-Item $installer -Force
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
        $Python = Get-PythonPath
    }
}
if (-not $Python) { Write-Error "请手动安装 Python 3.10+ 后重试"; exit 1 }
Write-Info "Python: $($Python.Version) ($($Python.Path))"

# ── 检测 git ──
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Warn "Git 未安装"
    $choice = Read-Host "  是否下载安装 Git? (Y/n)"
    if ($choice -ne "n") {
        Write-Info "正在下载 Git for Windows..."
        $url = "https://github.com/git-for-windows/git/releases/download/v2.45.0.windows.1/Git-2.45.0-64-bit.exe"
        $installer = "$env:TEMP\git-installer.exe"
        Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
        Start-Process -Wait -FilePath $installer -ArgumentList "/VERYSILENT", "/NORESTART"
        Remove-Item $installer -Force
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
    }
}
Write-Info "git: $(& git --version 2>&1 | Select -First 1)"

# ── 安装 Partner ──
Write-Header "安装 Partner"
if (Test-Path $PartnerDir) {
    Write-Info "Partner 目录已存在: $PartnerDir"
    Push-Location $PartnerDir
    & git pull --ff-only 2>&1 | Out-Null
    Pop-Location
} else {
    Write-Info "克隆 Partner 仓库..."
    & git clone "https://github.com/zty522/partner.git" $PartnerDir
}

Push-Location $PartnerDir
& $Python.Path -m pip install -e . -q --break-system-packages 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    & $Python.Path -m pip install -e . -q 2>&1 | Out-Null
}
Pop-Location
Write-Info "Partner 安装完成"

# ── 创建启动器 ──
Write-Header "创建启动器"

# 添加到用户 PATH
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -notlike "*$PartnerDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$UserPath;$PartnerDir", "User")
    Write-Info "已添加到用户 PATH"
}

# 桌面快捷方式 → wscript.exe + Partner.vbs（无终端窗口）
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = "$DesktopPath\Partner.lnk"
$WScriptShell = New-Object -ComObject WScript.Shell
$Shortcut = $WScriptShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "wscript.exe"
$Shortcut.Arguments = "$PartnerDir\Partner.vbs"
$Shortcut.Description = "Partner - Your AI Research Companion"
$Shortcut.WorkingDirectory = "$PartnerDir"
$Shortcut.IconLocation = "$PartnerDir\Partner.exe, 0"
$Shortcut.Save()
Write-Info "桌面快捷方式已创建"

# ── 完成 ──
Write-Header "Partner 安装完成!"
Write-Host ""
Write-Host "  安装目录: $PartnerDir" -ForegroundColor $Cyan
Write-Host ""
Write-Host "  双击桌面上的 Partner 快捷方式启动" -ForegroundColor $Green
Write-Host ""

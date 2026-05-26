<#
.SYNOPSIS
    Partner 🤝 — Windows 一键安装脚本
.DESCRIPTION
    在 Windows 上安装 Partner (支持 WSL 和纯 Python 两种模式)
    用法: powershell -ExecutionPolicy Bypass -File install.ps1
#>

$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "Installing Partner 🤝"

# ── 颜色 ──
$Green  = "Green"
$Yellow = "Yellow"
$Red    = "Red"
$Cyan   = "Cyan"

function Write-Info  { Write-Host "✓" -ForegroundColor $Green -NoNewline; Write-Host " $args" }
function Write-Warn  { Write-Host "⚠" -ForegroundColor $Yellow -NoNewline; Write-Host " $args" }
function Write-Error { Write-Host "✗" -ForegroundColor $Red -NoNewline; Write-Host " $args" }
function Write-Header { Write-Host "`n━━━ $args ━━━`n" -ForegroundColor $Cyan }

# ── 检测运行模式 ──
$IsWSL = (Get-CimInstance -ClassName Win32_ComputerSystem).Model -match "WSL"
Write-Header "检测系统环境"
Write-Info "Windows $([Environment]::OSVersion.Version)"
if ($IsWSL) {
    Write-Info "运行环境: WSL"
} else {
    Write-Info "运行环境: 原生 Windows"
}

# ── 检测 Python ──
function Get-PythonPath {
    foreach ($cmd in @("python", "python3")) {
        try {
            $ver = & $cmd --version 2>&1
            if ($ver -match "(\d+)\.(\d+)") {
                $major = [int]$Matches[1]
                $minor = [int]$Matches[2]
                if ($major -ge 3 -and $minor -ge 10) {
                    return @{Path=$cmd; Version="$major.$minor"}
                }
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
        Write-Info "正在安装 Python (请勾选 'Add Python to PATH')..."
        Start-Process -Wait -FilePath $installer -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1"
        Remove-Item $installer -Force
        # 刷新 PATH
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
        $Python = Get-PythonPath
    }
}
if ($Python) {
    Write-Info "Python: $($Python.Version) ($($Python.Path))"
} else {
    Write-Error "请手动安装 Python 3.10+ 后重试"
    exit 1
}

# ── 检测 pip ──
try {
    & $Python.Path -m pip --version 2>&1 | Out-Null
    Write-Info "pip: 正常"
} catch {
    Write-Warn "pip 未安装，正在安装..."
    & $Python.Path -m ensurepip --upgrade
}

# ── 检测 Git ──
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

# ── 安装 Hermes ──
Write-Header "安装 Hermes Agent"
try {
    & $Python.Path -m pip install hermes-agent -q
    Write-Info "Hermes Agent 安装成功"
} catch {
    Write-Warn "Hermes 安装失败: $_"
}

# ── 安装 Partner ──
Write-Header "安装 Partner"
$PartnerDir = "$env:USERPROFILE\.partner"
if (Test-Path $PartnerDir) {
    Write-Info "Partner 目录已存在: $PartnerDir"
    Push-Location $PartnerDir
    & git pull --ff-only 2>&1 | Out-Null
    Pop-Location
} else {
    Write-Info "克隆 Partner 仓库..."
    & git clone "https://github.com/zty522/partner.git" $PartnerDir
}

try {
    Push-Location $PartnerDir
    & $Python.Path -m pip install -e . -q
    Pop-Location
    Write-Info "Partner 安装成功"
} catch {
    Write-Error "Partner 安装失败: $_"
    exit 1
}

# ── 创建桌面快捷方式 ──
Write-Header "创建快捷方式"
$ShortcutDir = "$env:USERPROFILE\Desktop"
$ShortcutPath = "$ShortcutDir\Partner.lnk"
if (Test-Path $ShortcutDir) {
    $WScriptShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WScriptShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = "powershell.exe"
    $Shortcut.Arguments = "-NoExit -Command partner status"
    $Shortcut.Description = "Partner - Your AI Research Companion"
    $Shortcut.WorkingDirectory = "%USERPROFILE%"
    $Shortcut.Save()
    Write-Info "桌面快捷方式已创建"
}

# ── 创建启动脚本 ──
$BatchPath = "$PartnerDir\partner.bat"
@"
@echo off
python -m partner.cli %*
"@ | Out-File -FilePath $BatchPath -Encoding ASCII
Write-Info "启动脚本: $BatchPath"

# 添加到 PATH
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -notlike "*$PartnerDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$UserPath;$PartnerDir", "User")
    $env:Path += ";$PartnerDir"
    Write-Info "已添加到用户 PATH"
}

# ── 完成 ──
Write-Header "🎉 Partner 安装完成!"
Write-Host "  安装目录: $PartnerDir" -ForegroundColor $Cyan
Write-Host "  配置向导: partner setup" -ForegroundColor $Cyan
Write-Host "  查看状态: partner status" -ForegroundColor $Cyan
Write-Host "  更新:     partner update" -ForegroundColor $Cyan
Write-Host ""
Write-Host "  在命令行中直接输入 partner 即可使用" -ForegroundColor $Green
Write-Host "  或双击桌面的 Partner 快捷方式" -ForegroundColor $Green
Write-Host ""

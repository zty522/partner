<#
.SYNOPSIS
    Partner 🤝 — Windows 一键安装脚本
.DESCRIPTION
    在 Windows 上原生安装 Partner，无需 WSL。
    支持 Hermes Agent、OpenClaw、Codex 等后端选择。
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
$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($IsAdmin) { Write-Info "以管理员身份运行" }
else { Write-Warn "以普通用户身份运行" }

# ── 选择安装目录 ──
$PartnerDir = "$env:USERPROFILE\.partner"
$UserChoice = Read-Host "`n安装目录 (回车默认 $PartnerDir)"
if ($UserChoice) { $PartnerDir = $UserChoice }

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
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment::GetEnvironmentVariable("Path", "Machine")
    }
}
Write-Info "git: $(& git --version 2>&1 | Select -First 1)"

# ── 检测 Node.js (OpenClaw/Codex 可能需要) ──
$HasNode = $null -ne (Get-Command node -ErrorAction SilentlyContinue)
if ($HasNode) { 
    $NodeVer = & node --version
    Write-Info "Node.js: $NodeVer"
}

# ── 选择后端 Agent ──
Write-Header "选择 AI 后端"
Write-Host ""
Write-Host "  Partner 需要一个 AI 后端来处理研究和对话。"
Write-Host "  选择一个你想使用的后端："
Write-Host ""
Write-Host "  ${Cyan}1)${NC} Hermes Agent (推荐)  — pip 安装，功能完整"
if ($HasNode) {
    Write-Host "  ${Cyan}2)${NC} OpenClaw (小龙蝦)      — npm 安装，多渠道 AI 助手"
    Write-Host "  ${Cyan}3)${NC} 两者都装"
}
Write-Host "  ${Cyan}$(if ($HasNode) { '4' } else { '2' })${NC} 先不装，我自己配置"
Write-Host ""
$maxChoice = if ($HasNode) { 4 } else { 2 }
$choice = Read-Host "  请输入 [1-$maxChoice] (默认 1)"
if (-not $choice) { $choice = "1" }

# ── 安装 Node.js（如果需要） ──
if (($choice -eq "2" -or $choice -eq "3") -and -not $HasNode) {
    Write-Warn "OpenClaw 需要 Node.js"
    $yn = Read-Host "  是否下载安装 Node.js? (Y/n)"
    if ($yn -ne "n") {
        Write-Info "正在下载 Node.js..."
        $url = "https://nodejs.org/dist/v22.14.0/node-v22.14.0-x64.msi"
        $installer = "$env:TEMP\node-installer.msi"
        Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
        Start-Process -Wait -FilePath "msiexec.exe" -ArgumentList "/i", "`"$installer`"", "/quiet", "/norestart"
        Remove-Item $installer -Force
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
        Write-Info "Node.js 安装完成"
    }
}

# ── 安装选中的后端 ──
switch ($choice) {
    "1" {
        Write-Header "安装 Hermes Agent"
        & $Python.Path -m pip install hermes-agent -q
        Write-Info "Hermes Agent 安装成功"
    }
    "2" {
        Write-Header "安装 OpenClaw"
        & npm install -g openclaw@latest
        Write-Info "OpenClaw 安装成功"
        Write-Host "  配置: openclaw onboard" -ForegroundColor $Cyan
    }
    "3" {
        Write-Header "安装 Hermes Agent + OpenClaw"
        & $Python.Path -m pip install hermes-agent -q
        Write-Info "Hermes Agent 安装成功"
        & npm install -g openclaw@latest
        Write-Info "OpenClaw 安装成功"
    }
    default {
        Write-Info "跳过后端安装，你可稍后手动安装"
    }
}

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
& $Python.Path -m pip install -e . -q
Pop-Location
Write-Info "Partner 安装完成"

# ── 创建启动器 ──
Write-Header "创建启动器"

# partner.bat — 在命令行中直接输入 partner
$BatchPath = "$PartnerDir\partner.bat"
@"
@echo off
"%~dp0venv\Scripts\python" -m partner.cli %*
"@ | Out-File -FilePath $BatchPath -Encoding ASCII

# 添加到用户 PATH
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -notlike "*$PartnerDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$UserPath;$PartnerDir", "User")
    Write-Info "已添加到用户 PATH（新开命令行生效）"
}

# 桌面快捷方式
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = "$DesktopPath\Partner.lnk"
$WScriptShell = New-Object -ComObject WScript.Shell
$Shortcut = $WScriptShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.Arguments = "-NoExit -Command partner status"
$Shortcut.Description = "Partner - Your AI Research Companion"
$Shortcut.WorkingDirectory = "%USERPROFILE%"
$Shortcut.Save()
Write-Info "桌面快捷方式已创建"

# 创建虚拟环境（可选）
Write-Header "创建 Python 虚拟环境"
$yn = Read-Host "  是否为 Partner 创建独立虚拟环境? (Y/n)"
if ($yn -ne "n") {
    $venvPath = "$PartnerDir\venv"
    & $Python.Path -m venv $venvPath
    & "$venvPath\Scripts\pip" install -e "$PartnerDir" -q
    # 更新 bat 指向 venv
    @"
@echo off
"%~dp0venv\Scripts\python" -m partner.cli %*
"@ | Out-File -FilePath $BatchPath -Encoding ASCII
    Write-Info "虚拟环境已创建: $venvPath"
} else {
    Write-Info "使用系统 Python"
    # 更新 bat 指向系统 Python
    @"
@echo off
python -m partner.cli %*
"@ | Out-File -FilePath $BatchPath -Encoding ASCII
}

# ── 完成 ──
Write-Header "🎉 Partner 安装完成!"
Write-Host ""
Write-Host "  安装目录: $PartnerDir" -ForegroundColor $Cyan
Write-Host ""
Write-Host "  ${Cyan}接下来:${NC}"
Write-Host "  1. 新开命令行窗口（或重启 PowerShell）"
Write-Host "  2. 直接输入 partner 即可使用"
Write-Host "  3. 配置向导: partner setup"
Write-Host "  4. 查看状态: partner status"
Write-Host "  5. 启动 QQ:  partner bot start qq"
Write-Host ""
Write-Host "  或双击桌面上的 Partner 快捷方式" -ForegroundColor $Green
Write-Host ""

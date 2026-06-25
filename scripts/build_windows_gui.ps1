# build_windows_gui.ps1
# Build Partner Windows GUI as a standalone EXE using PyInstaller.
# Run this on Windows (not WSL) in the Partner repo directory.

param(
    [string]$PartnerDir = (Get-Location).Path,
    [switch]$Console = $false
)

Write-Host "🔨 Building Partner Windows GUI..." -ForegroundColor Cyan

# 1. Check dependencies
$py = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Host "❌ Python not found. Install Python 3.10+ from python.org" -ForegroundColor Red
    exit 1
}

$pip = "$($py.Source)\python -m pip"
$pyinstaller = Get-Command pyinstaller -ErrorAction SilentlyContinue
if (-not $pyinstaller) {
    Write-Host "📦 Installing PyInstaller..." -ForegroundColor Yellow
    & $pip install pyinstaller
}

# 2. Install Partner if not already
Write-Host "📦 Installing Partner package..." -ForegroundColor Yellow
& $pip install -e "$PartnerDir"

# 3. Build EXE
$outputDir = Join-Path $PartnerDir "dist"
$iconPath = Join-Path $PartnerDir "partner\desktop_gui\assets\partner_app_v2.ico"
if (-not (Test-Path $iconPath)) {
    $iconPath = ""  # No icon available
}

$consoleFlag = if ($Console) { "" } else { "--windowed" }

Write-Host "🔨 Building EXE (this may take a few minutes)..." -ForegroundColor Cyan

$cmd = @(
    "pyinstaller",
    "--name", "Partner",
    $consoleFlag,
    "--onefile",
    "--clean",
    "--add-data", "partner/locales;partner/locales",
    "--add-data", "partner/desktop_gui/assets;partner/desktop_gui/assets",
    "--hidden-import", "PySide6.QtCore",
    "--hidden-import", "PySide6.QtGui",
    "--hidden-import", "PySide6.QtWidgets",
    "--hidden-import", "partner.desktop_gui.gui_qt",
    "--hidden-import", "partner.setup",
    "--hidden-import", "partner.config",
    "--hidden-import", "partner.file_tools",
    "--hidden-import", "partner.workspace_layout",
    "--hidden-import", "partner.workspace_migration",
    "--hidden-import", "partner.outbound_policy",
    "--hidden-import", "partner.project_registry",
    "--hidden-import", "partner.project_state",
    "--collect-all", "partner",
    "--collect-submodules", "partner"
)

if ($iconPath) {
    $cmd += @("--icon", $iconPath)
}

$cmd += @("-m", "partner.desktop_gui.gui_qt")

$cmdStr = $cmd -join " "
Write-Host "  $cmdStr" -ForegroundColor Gray
& $py $cmdStr

# 4. Verify output
$exePath = Join-Path $outputDir "Partner.exe"
if (Test-Path $exePath) {
    Write-Host "✅ Build successful!" -ForegroundColor Green
    Write-Host "   EXE: $exePath"
    
    # Create desktop shortcut
    $shell = New-Object -ComObject WScript.Shell
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shortcut = $shell.CreateShortcut("$desktop\Partner.lnk")
    $shortcut.TargetPath = $exePath
    $shortcut.WorkingDirectory = $outputDir
    $shortcut.Description = "Partner AI Agent - Windows Edition"
    $shortcut.Save()
    Write-Host "   Shortcut: $desktop\Partner.lnk" -ForegroundColor Green
} else {
    Write-Host "❌ Build failed - check errors above" -ForegroundColor Red
    exit 1
}

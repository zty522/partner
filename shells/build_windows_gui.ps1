# build_windows_gui.ps1
# Build Partner Windows GUI as a standalone EXE using PyInstaller spec.
# Run this on Windows (not WSL) in the Partner repo directory.

param(
    [string]$PartnerDir = (Get-Location).Path,
    [switch]$Console = $false
)

Write-Host "🔨 Building Partner Windows GUI..." -ForegroundColor Cyan

# 1. Check Python
$py = Get-Command python -ErrorAction SilentlyContinue
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

# 2. Install Partner package if not already
Write-Host "📦 Installing Partner package..." -ForegroundColor Yellow
& $pip install -e "$PartnerDir"

# 3. Build EXE using the spec file
$specPath = Join-Path $PartnerDir "shells\partner_windows.spec"
$distDir = Join-Path $PartnerDir "dist"
$buildDir = Join-Path $PartnerDir "build\partner_windows"

Write-Host "🔨 Building EXE via spec file (this may take a few minutes)..." -ForegroundColor Cyan
Write-Host "  Spec: $specPath" -ForegroundColor Gray

# Console flag: modify spec to toggle console mode
if ($Console) {
    Write-Host "  Mode: Console window enabled (debug)" -ForegroundColor Yellow
    pyinstaller --distpath "$distDir" --workpath "$buildDir" --noconfirm "$specPath"
} else {
    Write-Host "  Mode: Windowed (no console)" -ForegroundColor Gray
    pyinstaller --distpath "$distDir" --workpath "$buildDir" --noconfirm "$specPath"
}

# 4. Verify output
$exePath = Join-Path $distDir "Partner.exe"
if (Test-Path $exePath) {
    Write-Host "✅ Build successful!" -ForegroundColor Green
    Write-Host "   EXE: $exePath"

    # Create desktop shortcut
    try {
        $shell = New-Object -ComObject WScript.Shell
        $desktop = [Environment]::GetFolderPath("Desktop")
        $shortcut = $shell.CreateShortcut("$desktop\Partner.lnk")
        $shortcut.TargetPath = $exePath
        $shortcut.WorkingDirectory = $distDir
        $shortcut.Description = "Partner AI Agent - Windows Edition"
        $shortcut.Save()
        Write-Host "   Shortcut: $desktop\Partner.lnk" -ForegroundColor Green
    } catch {
        Write-Host "⚠ Failed to create desktop shortcut: $_" -ForegroundColor Yellow
    }

    # 5. Build Inno Setup installer (if ISCC.exe is available)
    $isccPaths = @(
        "ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    $iscc = $null
    foreach ($p in $isccPaths) {
        $cmd = Get-Command $p -ErrorAction SilentlyContinue
        if ($cmd) { $iscc = $cmd; break }
    }
    if ($iscc) {
        Write-Host "📦 Building installer (Inno Setup)..." -ForegroundColor Yellow
        $issPath = Join-Path $PartnerDir "shells\partner_installer.iss"
        if (Test-Path $issPath) {
            & $iscc.Source $issPath
            Write-Host "✅ Installer built: $PartnerDir\dist\Partner_Setup.exe" -ForegroundColor Green
        } else {
            Write-Host "⚠ ISS file not found: $issPath" -ForegroundColor Yellow
        }
    } else {
        Write-Host "⚠ Inno Setup (ISCC.exe) not found. Skipping installer build." -ForegroundColor Yellow
        Write-Host "   Install Inno Setup 6 from: https://jrsoftware.org/isdl.php" -ForegroundColor Gray
    }
} else {
    Write-Host "❌ Build failed - check errors above" -ForegroundColor Red
    exit 1
}

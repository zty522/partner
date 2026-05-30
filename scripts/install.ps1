# Partner Windows Installer v0.5.0 (build 1445)
     2|.SYNOPSIS
     3|    Partner - Universal Windows Installer
     4|.DESCRIPTION
     5|    Installs Partner natively on Windows (no WSL required).
     6|    Uses the embeddable Python package (no admin rights needed).
     7|
     8|    Usage:
     9|      powershell -Command "& { iwr -useb https://raw.githubusercontent.com/zty522/partner/main/scripts/install.ps1 } | iex"
    10|      powershell -ExecutionPolicy Bypass -File install.ps1
    11|#>
    12|
    13|# ── Encoding ──
    14|[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    15|$ErrorActionPreference = "Stop"
    16|$Host.UI.RawUI.WindowTitle = "Installing Partner"
    17|
    18|$PartnerRepo = "https://github.com/zty522/partner.git"
    19|$PartnerDir  = "$env:USERPROFILE\.partner"
    20|$PythonDir   = "$PartnerDir\python"
    21|$PythonVer   = "3.12.3"
    22|$PythonVerShort = "312"
    23|$PythonZipUrl = "https://www.python.org/ftp/python/$PythonVer/python-${PythonVer}-embed-amd64.zip"
    24|
    25|# ── Colors ──
    26|$cGreen  = "Green"
    27|$cYellow = "Yellow"
    28|$cRed    = "Red"
    29|$cCyan   = "Cyan"
    30|
    31|function Write-Info   { Write-Host "$([char]0x2713) " -ForegroundColor $cGreen -NoNewline;  Write-Host "$args" }
    32|function Write-Warn   { Write-Host "$([char]0x26A0) " -ForegroundColor $cYellow -NoNewline; Write-Host "$args" }
    33|function Write-Err    { Write-Host "$([char]0x2717) " -ForegroundColor $cRed -NoNewline;    Write-Host "$args" }
    34|function Write-Header { Write-Host "`n--- $args ---`n" -ForegroundColor $cCyan }
    35|
    36|function Exit-Error($msg) {
    37|    Write-Err $msg
    38|    exit 1
    39|}
    40|
    41|# ── Detect System ──
    42|Write-Header "Checking system"
    43|Write-Info "Windows $([Environment]::OSVersion.Version)"
    44|
    45|# ── Python helpers ──
    46|function Test-PythonVersion($pyCmd) {
    47|    try {
    48|        $ver = & $pyCmd --version 2>&1
    49|        if ($ver -match "(\d+)\.(\d+)") {
    50|            $major = [int]$Matches[1]
    51|            $minor = [int]$Matches[2]
    52|            return @{Path=$pyCmd; Major=$major; Minor=$minor; Full="$major.$minor"}
    53|        }
    54|    } catch {}
    55|    return $null
    56|}
    57|
    58|function Test-ValidPython($py) {
    59|    if ($py -eq $null) { return $false }
    60|    return ($py.Major -eq 3 -and $py.Minor -ge 10)
    61|}
    62|
    63|function Find-InstalledPython {
    64|    foreach ($cmd in @("python", "python3")) {
    65|        $result = Test-PythonVersion $cmd
    66|        if ($result -ne $null) { return $result }
    67|    }
    68|    return $null
    69|}
    70|
    71|# ── Refresh PATH from registry ──
    72|function Refresh-Path {
    73|    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    74|    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    75|    $env:Path = "$userPath;$machinePath"
    76|}
    77|
    78|# ── Install Python (embeddable zip package) to isolated directory ──
    79|function Install-PythonIsolated {
    80|    # Ensure target directory exists
    81|    if (-not (Test-Path $PythonDir)) {
    82|        New-Item -ItemType Directory -Path $PythonDir -Force | Out-Null
    83|    }
    84|
    85|    $zipFile = "$env:TEMP\python-${PythonVer}-embed-amd64.zip"
    86|
    87|    Write-Info "Downloading Python $PythonVer embeddable package..."
    88|    try {
    89|        Invoke-WebRequest -Uri $PythonZipUrl -OutFile $zipFile -UseBasicParsing -ErrorAction Stop
    90|    } catch {
    91|        Exit-Error "Failed to download Python embeddable package: $_"
    92|    }
    93|
    94|    Write-Info "Extracting Python $PythonVer to $PythonDir ..."
    95|    try {
    96|        Expand-Archive -Path $zipFile -DestinationPath $PythonDir -Force
    97|    } catch {
    98|        Exit-Error "Failed to extract Python embeddable package: $_"
    99|    }
   100|    Remove-Item $zipFile -Force -ErrorAction SilentlyContinue
   101|
   102|    # Verify the extracted Python
   103|    $pyPath = "$PythonDir\python.exe"
   104|    if (-not (Test-Path $pyPath)) {
   105|        Exit-Error "Python was extracted but python.exe not found at $pyPath"
   106|    }
   107|
   108|    # Enable site-packages by properly configuring the ._pth file
   109|    $pthFile = "$PythonDir\\python${PythonVerShort}._pth"
   110|    $sitePackagesDir = "$PythonDir\\Lib\\site-packages"
   111|    
   112|    Write-Host "✓ Configuring embedded Python for site-packages and pip..." -ForegroundColor Cyan
   113|    
   114|    # 1. Ensure Lib\site-packages directory exists
   115|    if (-not (Test-Path $sitePackagesDir)) {
   116|        New-Item -ItemType Directory -Force -Path $sitePackagesDir | Out-Null
   117|    }
   118|    
   119|    # 2. Modify the _pth file
   120|    if (Test-Path $pthFile) {
   121|        $pthContent = Get-Content $pthFile
   122|        
   123|        # Add 'Lib\site-packages' path if not already present
   124|        if ($pthContent -notcontains "Lib\site-packages") {
   125|            $pthContent = $pthContent + "Lib\site-packages"
   126|        }
   127|        # Uncomment 'import site' to activate package discovery
   128|        $pthContent = $pthContent -replace '#import site', 'import site'
   129|        
   130|        [System.IO.File]::WriteAllLines($pthFile, $pthContent)
   131|    } else {
   132|        Write-Warn "._pth file not found at $pthFile — creating one..."
   133|        $pthContent = @"
   134|python${PythonVerShort}.zip
   135|.
   136|Lib\site-packages
   137|import site
   138|"@
   139|        [System.IO.File]::WriteAllText($pthFile, $pthContent)
   140|    }
   141|
   142|    # Bootstrap pip via get-pip.py
   143|    $getPipUrl = "https://bootstrap.pypa.io/get-pip.py"
   144|    $getPipScript = "$env:TEMP\get-pip.py"
   145|    Write-Info "Downloading get-pip.py..."
   146|    try {
   147|        Invoke-WebRequest -Uri $getPipUrl -OutFile $getPipScript -UseBasicParsing -ErrorAction Stop
   148|    } catch {
   149|        Exit-Error "Failed to download get-pip.py: $_"
   150|    }
   151|
   152|    Write-Info "Bootstrapping pip..."
   153|    try {
   154|        $pipOutput = & $pyPath $getPipScript --no-warn-script-location 2>&1
   155|        if ($LASTEXITCODE -ne 0) {
   156|            Exit-Error "pip bootstrap failed: $pipOutput"
   157|        }
   158|        # Also install setuptools and wheel (required for editable installs)
   159|        Write-Info "Installing setuptools and wheel..."
   160|        $null = & $pyPath -m pip install setuptools wheel -q --no-warn-script-location 2>&1
   161|    } catch {
   162|        Exit-Error "Failed to bootstrap pip: $_"
   163|    }
   164|    Remove-Item $getPipScript -Force -ErrorAction SilentlyContinue
   165|
   166|    $pyInfo = Test-PythonVersion $pyPath
   167|    if ($pyInfo -eq $null) {
   168|        Exit-Error "Installed Python could not be detected at $pyPath"
   169|    }
   170|
   171|    Write-Info "Python $($pyInfo.Full) installed (embeddable) at $PythonDir"
   172|    return $pyInfo
   173|}
   174|
   175|# ── Ensure valid Python ──
   176|function Ensure-Python {
   177|    $py = Find-InstalledPython
   178|
   179|    if ($py -ne $null -and (Test-ValidPython $py)) {
   180|        Write-Info "Python $($py.Full) found: $($py.Path)"
   181|        return $py
   182|    }
   183|
   184|    # System Python exists but wrong version
   185|    if ($py -ne $null) {
   186|        Write-Warn "Python $($py.Full) detected but Partner requires Python 3.10+."
   187|        Write-Info "Installing Python 3.12 to isolated directory..."
   188|        return Install-PythonIsolated
   189|    }
   190|
   191|    # No Python at all - try winget first
   192|    Write-Warn "Python 3 not detected on this system."
   193|
   194|    try {
   195|        $wingetCheck = Get-Command winget -ErrorAction Stop
   196|        Write-Info "winget available. Installing Python 3.12 via winget..."
   197|        $proc = Start-Process -Wait -FilePath "winget" -ArgumentList @(
   198|            "install", "Python.Python.3.12", "--silent", "--accept-package-agreements"
   199|        ) -PassThru
   200|
   201|        if ($proc.ExitCode -eq 0) {
   202|            Refresh-Path
   203|            $py = Find-InstalledPython
   204|            if ($py -ne $null -and (Test-ValidPython $py)) {
   205|                Write-Info "Python $($py.Full) installed via winget"
   206|                return $py
   207|            }
   208|        }
   209|        Write-Warn "winget install did not result in a detectable Python. Falling back to embeddable package..."
   210|    } catch {
   211|        Write-Warn "winget not available. Downloading embeddable Python directly..."
   212|    }
   213|
   214|    # Fallback: download and install embeddable Python to isolated directory
   215|    return Install-PythonIsolated
   216|}
   217|
   218|# ── Ensure Git ──
   219|function Ensure-Git {
   220|    try {
   221|        $gitVer = & git --version 2>&1
   222|        if ($LASTEXITCODE -eq 0) {
   223|            Write-Info "Git: $($gitVer | Select-Object -First 1)"
   224|            return $true
   225|        }
   226|    } catch {}
   227|
   228|    Write-Warn "Git not detected."
   229|    Write-Info "Downloading Git for Windows..."
   230|
   231|    $gitUrl = "https://github.com/git-for-windows/git/releases/download/v2.45.0.windows.1/Git-2.45.0-64-bit.exe"
   232|    $gitInstaller = "$env:TEMP\git-installer.exe"
   233|    try {
   234|        Invoke-WebRequest -Uri $gitUrl -OutFile $gitInstaller -UseBasicParsing -ErrorAction Stop
   235|    } catch {
   236|        Exit-Error "Failed to download Git installer: $_"
   237|    }
   238|
   239|    Write-Info "Installing Git for Windows..."
   240|    try {
   241|        $proc = Start-Process -Wait -FilePath $gitInstaller -ArgumentList @("/VERYSILENT", "/NORESTART") -PassThru
   242|        if ($proc.ExitCode -ne 0) {
   243|            Exit-Error "Git installer exited with code $($proc.ExitCode)."
   244|        }
   245|    } catch {
   246|        Exit-Error "Failed to install Git: $_"
   247|    }
   248|    Remove-Item $gitInstaller -Force -ErrorAction SilentlyContinue
   249|
   250|    Refresh-Path
   251|
   252|    try {
   253|        $gitVer = & git --version 2>&1
   254|        if ($LASTEXITCODE -eq 0) {
   255|            Write-Info "Git installed: $($gitVer | Select-Object -First 1)"
   256|            return $true
   257|        }
   258|    } catch {}
   259|    Exit-Error "Git was installed but could not be detected. Try restarting your terminal and re-running the installer."
   260|}
   261|
   262|# ── Clone repository ──
   263|function Install-Repository {
   264|    if (Test-Path $PartnerDir) {
   265|        Write-Info "Repository directory exists: $PartnerDir"
   266|        return
   267|    }
   268|
   269|    Write-Info "Cloning Partner repository..."
   270|    try {
   271|        & git clone $PartnerRepo $PartnerDir
   272|        if ($LASTEXITCODE -ne 0) {
   273|            Exit-Error "Failed to clone repository. Check your internet connection."
   274|        }
   275|        Write-Info "Repository cloned successfully"
   276|    } catch {
   277|        Exit-Error "Failed to clone repository: $_"
   278|    }
   279|}
   280|
   281|# ── Install via pip ──
   282|function Install-PartnerPackage {
   283|    Push-Location $PartnerDir
   284|    try {
   285|        $pythonExe = $Python.Path
   286|        Write-Info "Running pip install -e . ..."
   287|        
   288|        # Verify pip works first
   289|        # 2>&1 is safe here because pip --version doesn't emit WARNING to stderr
   290|        $pipVer = & $pythonExe -m pip --version 2>&1
   291|        if ($LASTEXITCODE -ne 0) {
   292|            Pop-Location
   293|            Exit-Error "pip is not working. Output: $pipVer"
   294|        }
   295|        Write-Info "pip: $($pipVer.Trim())"
   296|        
   297|        # ── pip install ──
   298|        # PowerShell 5.1 ($ErrorActionPreference=Stop) problem:
   299|        #   2>&1 converts stderr lines to ErrorRecord objects, which trigger Stop
   300|        #   even when redirected to the output stream, causing RemoteException.
   301|        # Solution: use 2>$null to discard stderr, then check $LASTEXITCODE.
   302|        # (pip's error messages go to stdout as well when install fails.)
   303|        
   304|        # Pre-uninstall old version silently — handles "no RECORD file" error
   305|        # that occurs when pip tries to upgrade an editable install without a RECORD.
   306|        & $pythonExe -m pip uninstall -y partner-research 2>$null | Out-Null
   307|        
   308|        # Now install fresh. stderr goes to $null to avoid $ErrorActionPreference=Stop
   309|        # converting pip warnings into terminating errors.
   310|        & $pythonExe -m pip install -e . --no-warn-script-location 2>$null
   311|        $pipExitCode = $LASTEXITCODE
   312|        
   313|        # ── Check result ──
   314|        # If install failed but package is already importable (common on re-run),
   315|        # treat it as success. The real test is whether partner actually works.
   316|        if ($pipExitCode -ne 0) {
   317|            python -c "import partner" 2>$null
   318|            if ($LASTEXITCODE -eq 0) {
   319|                Write-Warn "pip re-install had issues (exit $pipExitCode), but partner is already installed and importable."
   320|            } else {
   321|                Pop-Location
   322|                Exit-Error "pip install failed (exit code: $pipExitCode). Run manually: $pythonExe -m pip install -e ."
   323|            }
   324|        }
   325|        
   326|        Write-Info "Partner package installed"
   327|        Pop-Location
   328|    } catch {
   329|        Pop-Location
   330|        # If we get here, something threw unexpectedly despite the 2>$null fix
   331|        $errMsg = $_.ToString()
   332|        # Check if package is actually usable despite the error
   333|        python -c "import partner" 2>$null
   334|        if ($LASTEXITCODE -eq 0) {
   335|            Write-Warn "pip install completed with non-fatal issue: $errMsg"
   336|            return
   337|        }
   338|        Exit-Error "pip install failed: $errMsg"
   339|    }
   340|}
   341|
   342|# ── Add Python Scripts to PATH ──
   343|function Add-PythonScriptsToPath {
   344|    <#
   345|    .SYNOPSIS
   346|        Add Python Scripts directory to user PATH permanently.
   347|    #>
   348|    # 获取 Python Scripts 目录
   349|    # 方法1: 通过 python -m site --user-site 定位用户级 Scripts
   350|    $userSite = & python -m site --user-site 2>$null
   351|    $userScriptsDir = ""
   352|    if ($userSite) {
   353|        $userScriptsDir = Join-Path (Split-Path $userSite -Parent) "Scripts"
   354|    }
   355|    
   356|    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
   357|    $sysScriptsDir = if ($pythonCmd) { Join-Path (Split-Path $pythonCmd.Source -Parent) "Scripts" } else { "" }
   358|    $pyDir = if ($pythonCmd) { Split-Path $pythonCmd.Source -Parent } else { "" }
   359|
   360|    $pythonPaths = @()
   361|    if ($userScriptsDir) { $pythonPaths += $userScriptsDir }
   362|    if ($sysScriptsDir)  { $pythonPaths += $sysScriptsDir }
   363|    if ($pyDir)          { $pythonPaths += $pyDir }
   364|    if ($PythonDir)      { $pythonPaths += (Join-Path $PythonDir "Scripts") }
   365|
   366|    $scriptsDir = $null
   367|    foreach ($p in $pythonPaths) {
   368|        if ($p -and (Test-Path $p)) {
   369|            # 确认该目录下有 partner.exe
   370|            if (Test-Path (Join-Path $p "partner.exe") -PathType Leaf) {
   371|                $scriptsDir = $p
   372|                break
   373|            }
   374|            # 没有 partner.exe 但目录存在，作为后备
   375|            if (-not $scriptsDir) {
   376|                $scriptsDir = $p
   377|            }
   378|        }
   379|    }
   380|
   381|    if (-not $scriptsDir) {
   382|        Write-Warn "Could not find Python Scripts directory"
   383|        return
   384|    }
   385|
   386|    # 检查是否已在 PATH 中
   387|    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
   388|    if ($currentPath -split ";" | Where-Object { $_ -eq $scriptsDir }) {
   389|        Write-Info "$scriptsDir already in PATH"
   390|        return
   391|    }
   392|
   393|    # 添加到用户级 PATH
   394|    $newPath = "$currentPath;$scriptsDir"
   395|    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
   396|
   397|    # 刷新当前会话
   398|    $env:Path = "$env:Path;$scriptsDir"
   399|
   400|    Write-Info "Added to PATH: $scriptsDir"
   401|    Write-Warn "Please restart your terminal for changes to take effect."
   402|}
   403|
   404|# ── Add to PATH ──
   405|function Add-PartnerToPath {
   406|    $scriptsDir = ""
   407|
   408|    # Resolve "python" command name to a full path so Split-Path works
   409|    $pythonCmd = Get-Command $Python.Path -ErrorAction SilentlyContinue
   410|    $pythonFullPath = if ($pythonCmd) { $pythonCmd.Source } else { "" }
   411|    
   412|    if ($pythonFullPath) {
   413|        $pythonDir = Split-Path -Parent (Split-Path -Parent $pythonFullPath)
   414|    } else {
   415|        $pythonDir = ""
   416|    }
   417|
   418|    # Determine Scripts directory based on where Python is
   419|    $candidates = @(
   420|        "$pythonDir\Scripts",                                            # e.g. ~\.partner\python\Scripts
   421|        "$env:APPDATA\Python\Python$($Python.Major)$($Python.Minor)\Scripts",
   422|        "$pythonDir\..\..\Scripts"                                       # another common layout
   423|    )
   424|
   425|    # Also check for pip-installed scripts dir (use 2>$null to avoid $ErrorActionPreference=Stop)
   426|    $pipScripts = & $Python.Path -m site --user-site 2>$null
   427|    if ($pipScripts -match "^(.*)\\site-packages") {
   428|        $candidates += "$($Matches[1])\Scripts"
   429|    }
   430|
   431|    foreach ($dir in $candidates) {
   432|        $resolved = [System.IO.Path]::GetFullPath($dir)
   433|        if (Test-Path "$resolved\partner.exe" -PathType Leaf) {
   434|            $scriptsDir = $resolved
   435|            break
   436|        }
   437|        if (Test-Path "$resolved\partner" -PathType Leaf) {
   438|            $scriptsDir = $resolved
   439|            break
   440|        }
   441|    }
   442|
   443|    if (-not $scriptsDir) {
   444|        # Search more broadly
   445|        foreach ($dir in @(
   446|            "$env:APPDATA\Python\Python314\Scripts",
   447|            "$env:APPDATA\Python\Python313\Scripts",
   448|            "$env:APPDATA\Python\Python312\Scripts",
   449|            "$env:APPDATA\Python\Python311\Scripts",
   450|            "$env:APPDATA\Python\Python310\Scripts",
   451|            "$env:LOCALAPPDATA\Programs\Python\Python314\Scripts",
   452|            "$env:LOCALAPPDATA\Programs\Python\Python313\Scripts",
   453|            "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts",
   454|            "$env:LOCALAPPDATA\Programs\Python\Python311\Scripts",
   455|            "$env:LOCALAPPDATA\Programs\Python\Python310\Scripts"
   456|        )) {
   457|            if (Test-Path "$dir\partner.exe" -PathType Leaf) {
   458|                $scriptsDir = $dir
   459|                break
   460|            }
   461|        }
   462|    }
   463|
   464|    if ($scriptsDir) {
   465|        Write-Info "Found partner executable in: $scriptsDir"
   466|
   467|        # Add to current session
   468|        $env:Path = "$scriptsDir;$env:Path"
   469|
   470|        # Add to user PATH permanently
   471|        $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
   472|        if ($currentPath -split ";" -notcontains $scriptsDir) {
   473|            try {
   474|                [Environment]::SetEnvironmentVariable("Path", "$currentPath;$scriptsDir", "User")
   475|                Write-Info "Added to user PATH: $scriptsDir"
   476|            } catch {
   477|                Write-Warn "Could not modify PATH: $_"
   478|            }
   479|        } else {
   480|            Write-Info "Already in user PATH: $scriptsDir"
   481|        }
   482|    } else {
   483|        Write-Warn "Could not locate partner.exe in any Scripts directory."
   484|        Write-Warn "Partner installed but not added to PATH automatically."
   485|        Write-Warn "You may need to manually add the Scripts directory to your PATH."
   486|    }
   487|}
   488|
   489|# ═══════════════════════════════════════════════
   490|# MAIN INSTALLATION
   491|# ═══════════════════════════════════════════════
   492|
   493|Write-Header "Installing Partner"
   494|
   495|# Step 1: Ensure valid Python
   496|$Python = Ensure-Python
   497|
   498|# Step 2: Ensure Git
   499|$null = Ensure-Git
   500|
   501|
     1|#!/usr/bin/env bash
     2|# ──────────────────────────────────────────────────────────────
     3|# Partner - Universal Linux Installer
     4|# Usage: curl -fsSL https://raw.githubusercontent.com/zty522/partner/main/scripts/install.sh | bash
     5|# ──────────────────────────────────────────────────────────────
     6|set -euo pipefail 2>/dev/null || set -eu
     7|
     8|REPO_URL="https://github.com/zty522/partner.git"
     9|INSTALL_DIR="${PARTNER_HOME:-$HOME/.partner}"
    10|PYTHON_MIN="3.10"
    11|PYTHON_MAX="3.12"
    12|GREEN='\033[0;32m'
    13|YELLOW='\033[1;33m'
    14|RED='\033[0;31m'
    15|CYAN='\033[0;36m'
    16|BOLD='\033[1m'
    17|NC='\033[0m'
    18|
    19|info()  { echo -e "${GREEN}✔${NC} $1"; }
    20|warn()  { echo -e "${YELLOW}⚠${NC} $1"; }
    21|error() { echo -e "${RED}✗${NC} $1"; }
    22|header(){ echo -e "\n${BOLD}${CYAN}--- $1 ---${NC}\n"; }
    23|
    24|# ── Detect OS ──
    25|detect_os() {
    26|    if [ -f /etc/os-release ]; then
    27|        . /etc/os-release
    28|        echo "$ID"
    29|    elif command -v lsb_release &>/dev/null; then
    30|        lsb_release -si | tr '[:upper:]' '[:lower:]'
    31|    elif [ "$(uname)" = "Darwin" ]; then
    32|        echo "macos"
    33|    else
    34|        echo "unknown"
    35|    fi
    36|}
    37|OS=$(detect_os)
    38|ARCH=$(uname -m)
    39|info "System: ${OS} ${ARCH}"
    40|
    41|# ── Version comparison helpers ──
    42|ver_lte() { printf '%s\n%s\n' "$1" "$2" | sort -C -V; }
    43|ver_lt()  { ! ver_lte "$2" "$1"; }
    44|ver_gte() { ver_lte "$2" "$1"; }
    45|
    46|# ── Get Python major.minor version ──
    47|get_python_ver() {
    48|    local py=$1
    49|    $py --version 2>&1 | grep -oP '\d+\.\d+' | head -1
    50|}
    51|
    52|# ── Check if Python version is valid (3.10-3.12) ──
    53|is_valid_python() {
    54|    local py=$1
    55|    local ver
    56|    ver=$(get_python_ver "$py")
    57|    [ -z "$ver" ] && return 1
    58|    local major minor
    59|    major=$(echo "$ver" | cut -d. -f1)
    60|    minor=$(echo "$ver" | cut -d. -f2)
    61|    [ "$major" = "3" ] && [ "$minor" -ge 10 ] && [ "$minor" -le 12 ]
    62|}
    63|
    64|# ── Find a valid system Python ──
    65|find_system_python() {
    66|    for p in python3 python python3.12 python3.11 python3.10; do
    67|        if command -v "$p" &>/dev/null && is_valid_python "$p"; then
    68|            echo "$p"
    69|            return 0
    70|        fi
    71|    done
    72|    return 1
    73|}
    74|
    75|# ── Install Python 3.12 via pyenv ──
    76|install_via_pyenv() {
    77|    if command -v pyenv &>/dev/null; then
    78|        warn "Valid Python not found. Installing Python 3.12.3 via pyenv..."
    79|        pyenv install 3.12.3 -s 2>&1 | tail -1
    80|        local exit_code=$?
    81|        if [ $exit_code -eq 0 ]; then
    82|            local pyenv_root="${PYENV_ROOT:-$HOME/.pyenv}"
    83|            local py_path="$pyenv_root/versions/3.12.3/bin/python3"
    84|            if [ -x "$py_path" ]; then
    85|                info "Python 3.12.3 installed via pyenv"
    86|                echo "$py_path"
    87|                return 0
    88|            fi
    89|        fi
    90|        warn "pyenv install failed, trying other methods..."
    91|    fi
    92|    return 1
    93|}
    94|
    95|# ── Install Python 3.12 via system package manager ──
    96|install_via_package_manager() {
    97|    case "$OS" in
    98|        ubuntu|debian|linuxmint|pop|elementary|zorin)
    99|            warn "Installing Python 3.12 via apt..."
   100|            # Try deadsnakes PPA first for older Ubuntu/Debian
   101|            if command -v add-apt-repository &>/dev/null; then
   102|                sudo add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
   103|            fi
   104|            sudo apt-get update -qq
   105|            sudo apt-get install -y -qq python3.12 python3.12-venv python3.12-distutils 2>/dev/null || \
   106|            sudo apt-get install -y -qq python3.12 2>/dev/null || return 1
   107|            if command -v python3.12 &>/dev/null; then
   108|                echo "python3.12"
   109|                return 0
   110|            fi
   111|            ;;
   112|        centos|fedora|rhel|rocky|almalinux)
   113|            warn "Installing Python 3.12 via yum/dnf..."
   114|            if command -v dnf &>/dev/null; then
   115|                sudo dnf install -y python3.12 2>/dev/null && { echo "python3.12"; return 0; }
   116|            fi
   117|            if command -v yum &>/dev/null; then
   118|                sudo yum install -y python3.12 2>/dev/null && { echo "python3.12"; return 0; }
   119|            fi
   120|            ;;
   121|        arch|manjaro|endeavour)
   122|            warn "Installing Python 3.12 via pacman..."
   123|            sudo pacman -Sy --noconfirm python 2>/dev/null && \
   124|                command -v python &>/dev/null && is_valid_python python && \
   125|                { echo "python"; return 0; }
   126|            ;;
   127|        alpine)
   128|            warn "Installing Python 3.12 via apk..."
   129|            sudo apk add python3 py3-pip 2>/dev/null && \
   130|                command -v python3 &>/dev/null && is_valid_python python3 && \
   131|                { echo "python3"; return 0; }
   132|            ;;
   133|        macos|darwin)
   134|            warn "Installing Python 3.12 via Homebrew..."
   135|            if command -v brew &>/dev/null; then
   136|                brew install python@3.12 2>&1 | tail -1
   137|                local exit_code=$?
   138|                if [ $exit_code -eq 0 ]; then
   139|                    for p in python3.12 python3; do
   140|                        if command -v "$p" &>/dev/null && is_valid_python "$p"; then
   141|                            echo "$p"
   142|                            return 0
   143|                        fi
   144|                    done
   145|                    # Find brew-installed python
   146|                    for prefix in /usr/local /opt/homebrew; do
   147|                        local bp="$prefix/opt/python@3.12/bin/python3.12"
   148|                        [ -x "$bp" ] && { echo "$bp"; return 0; }
   149|                    done
   150|                fi
   151|            fi
   152|            ;;
   153|    esac
   154|    return 1
   155|}
   156|
   157|# ── Compile Python 3.12 from source ──
   158|install_via_source() {
   159|    warn "Package manager install failed. Compiling Python 3.12.3 from source..."
   160|    local build_dir="$HOME/.partner-build"
   161|    local install_prefix="$HOME/.local/python"
   162|    local python_url="https://www.python.org/ftp/python/3.12.3/Python-3.12.3.tar.xz"
   163|
   164|    # Install build dependencies
   165|    info "Installing build dependencies..."
   166|    case "$OS" in
   167|        ubuntu|debian|linuxmint|pop|elementary|zorin)
   168|            sudo apt-get install -y -qq build-essential libssl-dev zlib1g-dev \
   169|                libbz2-dev libreadline-dev libsqlite3-dev curl libncursesw5-dev \
   170|                xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev 2>/dev/null || true
   171|            ;;
   172|        centos|fedora|rhel|rocky|almalinux)
   173|            sudo yum groupinstall -y "Development Tools" 2>/dev/null || \
   174|            sudo dnf groupinstall -y "Development Tools" 2>/dev/null || true
   175|            sudo yum install -y openssl-devel bzip2-devel libffi-devel 2>/dev/null || \
   176|            sudo dnf install -y openssl-devel bzip2-devel libffi-devel 2>/dev/null || true
   177|            ;;
   178|        macos|darwin)
   179|            # On macOS, xcode tools should be enough
   180|            xcode-select --install 2>/dev/null || true
   181|            ;;
   182|    esac
   183|
   184|    mkdir -p "$build_dir"
   185|    cd "$build_dir"
   186|
   187|    info "Downloading Python 3.12.3 source..."
   188|    curl -fsSL "$python_url" -o Python-3.12.3.tar.xz || {
   189|        error "Failed to download Python source"
   190|        cd "$HOME"
   191|        rm -rf "$build_dir"
   192|        return 1
   193|    }
   194|
   195|    tar xf Python-3.12.3.tar.xz
   196|    cd Python-3.12.3
   197|
   198|    info "Configuring build..."
   199|    ./configure --prefix="$install_prefix" --enable-optimizations --with-ensurepip=install 2>&1 | tail -5
   200|
   201|    info "Compiling Python (this may take a few minutes)..."
   202|    make -j"$(nproc 2>/dev/null || echo 2)" 2>&1 | tail -5
   203|
   204|    info "Installing Python..."
   205|    make install 2>&1 | tail -5
   206|
   207|    cd "$HOME"
   208|    rm -rf "$build_dir"
   209|
   210|    local py_path="$install_prefix/bin/python3"
   211|    if [ -x "$py_path" ]; then
   212|        info "Python 3.12.3 compiled and installed to $install_prefix"
   213|        # Add to PATH for this session
   214|        export PATH="$install_prefix/bin:$PATH"
   215|        echo "$py_path"
   216|        return 0
   217|    fi
   218|
   219|    error "Failed to compile Python from source"
   220|    return 1
   221|}
   222|
   223|# ── Ensure a valid Python is available ──
   224|ensure_python() {
   225|    # First try to find one on the system
   226|    local py
   227|    py=$(find_system_python) && {
   228|        info "Python: $(get_python_ver "$py") ($(command -v "$py" || echo "$py"))"
   229|        echo "$py"
   230|        return 0
   231|    }
   232|
   233|    header "Installing Python 3.12"
   234|
   235|    # Try pyenv
   236|    py=$(install_via_pyenv) && {
   237|        info "Python: $(get_python_ver "$py") ($py)"
   238|        echo "$py"
   239|        return 0
   240|    }
   241|
   242|    # Try package manager
   243|    py=$(install_via_package_manager) && {
   244|        info "Python: $(get_python_ver "$py") ($(command -v "$py"))"
   245|        echo "$py"
   246|        return 0
   247|    }
   248|
   249|    # Try source compile
   250|    py=$(install_via_source) && {
   251|        info "Python: $(get_python_ver "$py") ($py)"
   252|        echo "$py"
   253|        return 0
   254|    }
   255|
   256|    error "Failed to install Python 3.12. Please install Python 3.10, 3.11, or 3.12 manually."
   257|    exit 1
   258|}
   259|
   260|# ── Detect / install Git ──
   261|ensure_git() {
   262|    if command -v git &>/dev/null; then
   263|        info "Git: $(git --version 2>&1 | head -1)"
   264|        return 0
   265|    fi
   266|
   267|    warn "Git not found. Installing..."
   268|    case "$OS" in
   269|        ubuntu|debian|linuxmint|pop|elementary|zorin)
   270|            sudo apt-get install -y -qq git ;;
   271|        centos|fedora|rhel|rocky|almalinux)
   272|            sudo yum install -y git 2>/dev/null || sudo dnf install -y git 2>/dev/null ;;
   273|        arch|manjaro)
   274|            sudo pacman -Sy --noconfirm git ;;
   275|        alpine)
   276|            sudo apk add git ;;
   277|        macos|darwin)
   278|            if command -v brew &>/dev/null; then
   279|                brew install git
   280|            else
   281|                error "Please install Git manually: https://git-scm.com/downloads"
   282|                exit 1
   283|            fi
   284|            ;;
   285|        *)
   286|            error "Please install Git manually: https://git-scm.com/downloads"
   287|            exit 1
   288|            ;;
   289|    esac
   290|
   291|    if ! command -v git &>/dev/null; then
   292|        error "Git installation failed."
   293|        exit 1
   294|    fi
   295|    info "Git: $(git --version 2>&1 | head -1)"
   296|}
   297|
   298|# ── Clone repository with retry ──
   299|clone_repo() {
   300|    if [ -d "$INSTALL_DIR" ]; then
   301|        info "Repository directory exists: $INSTALL_DIR"
   302|        return 0
   303|    fi
   304|
   305|    info "Cloning Partner repository..."
   306|
   307|    # Configure Git for stability
   308|    git config --global http.version HTTP/1.1 2>/dev/null || true
   309|    git config --global http.postBuffer 524288000 2>/dev/null || true
   310|
   311|    local max_attempts=3
   312|    local attempt=1
   313|
   314|    while [ $attempt -le $max_attempts ]; do
   315|        if git clone "$REPO_URL" "$INSTALL_DIR" 2>/dev/null; then
   316|            info "Repository cloned successfully"
   317|            return 0
   318|        fi
   319|        if [ $attempt -lt $max_attempts ]; then
   320|            warn "Clone failed (attempt $attempt/$max_attempts), retrying in 3 seconds..."
   321|            sleep 3
   322|        fi
   323|        attempt=$((attempt + 1))
   324|    done
   325|
   326|    # HTTPS failed - try SSH
   327|    warn "HTTPS clone failed, trying SSH..."
   328|    local ssh_url="git@github.com:zty522/partner.git"
   329|    attempt=1
   330|    while [ $attempt -le 2 ]; do
   331|        if git clone "$ssh_url" "$INSTALL_DIR" 2>/dev/null; then
   332|            info "Repository cloned via SSH"
   333|            return 0
   334|        fi
   335|        attempt=$((attempt + 1))
   336|        sleep 2
   337|    done
   338|
   339|    error "Failed to clone repository. Please check your internet connection."
   340|    error "Try manually: git clone $REPO_URL $INSTALL_DIR"
   341|    exit 1
   342|}
   343|
   344|# ── Install Partner via pip ──
   345|install_partner() {
   346|    cd "$INSTALL_DIR"
   347|
   348|    info "Installing Partner package..."
   349|
   350|    # Try with --break-system-packages first (newer pip on some distros)
   351|    $PY -m pip install -e . -q --break-system-packages 2>/dev/null || \
   352|        $PY -m pip install -e . -q --user 2>/dev/null || \
   353|        $PY -m pip install -e . -q
   354|
   355|    local exit_code=$?
   356|    if [ $exit_code -ne 0 ]; then
   357|        error "pip install failed with exit code $exit_code"
   358|        exit 1
   359|    fi
   360|
   361|    info "Partner package installed"
   362|}
   363|
   364|# ── Create startup script ──
   365|create_startup_script() {
   366|    if command -v partner &>/dev/null; then
   367|        info "Partner command already available in PATH"
   368|        return 0
   369|    fi
   370|
   371|    mkdir -p "$HOME/.local/bin"
   372|
   373|    cat > "$HOME/.local/bin/partner" << 'PYEOF'
   374|#!/usr/bin/env python3
   375|import sys, os
   376|sys.path.insert(0, os.path.expanduser("~/.partner"))
   377|from partner.cli import main
   378|main()
   379|PYEOF
   380|
   381|    chmod +x "$HOME/.local/bin/partner"
   382|
   383|    # Add to PATH for current session
   384|    export PATH="$HOME/.local/bin:$PATH"
   385|
   386|    # Add to shell profiles for future sessions
   387|    for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.profile"; do
   388|        if [ -f "$rc" ] && ! grep -q 'PATH="$HOME/.local/bin:$PATH"' "$rc" 2>/dev/null; then
   389|            echo "" >> "$rc"
   390|            echo '# Added by Partner installer' >> "$rc"
   391|            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc"
   392|        fi
   393|    done
   394|
   395|    info "Startup script created at ~/.local/bin/partner"
   396|    info "Added ~/.local/bin to PATH in shell profiles"
   397|}
   398|
   399|# ═══════════════════════════════════════════════
   400|# MAIN INSTALLATION
   401|# ═══════════════════════════════════════════════
   402|
   403|header "Installing Partner"
   404|
   405|# Step 1: Ensure valid Python
   406|PY=$(ensure_python)
   407|
   408|# Step 2: Ensure Git
   409|ensure_git
   410|
   411|# Step 3: Check if already installed and working
   412|if command -v partner &>/dev/null && $PY -c "import partner; print('ok')" 2>/dev/null; then
   413|    info "Partner is already installed and functional"
   414|    info "Run 'partner update' to update to the latest version"
   415|    exit 0
   416|fi
   417|
   418|# Step 4: Clean up broken installations
   419|if command -v partner &>/dev/null; then
   420|    warn "Removing stale partner binary..."
   421|    rm -f "$(command -v partner)" 2>/dev/null || true
   422|fi
   423|
   424|# Step 5: Clone repository
   425|clone_repo
   426|
   427|# Step 6: Install via pip
   428|install_partner
   429|
   430|# Step 7: Create startup scripts
   431|create_startup_script
   432|
   433|# Step 8: Done
   434|header "Installation complete"
   435|echo ""
   436|echo -e "${GREEN}  ✔ Partner installed successfully. Run 'partner' to start.${NC}"
   437|echo ""
   438|echo -e "${CYAN}  Installation directory: $INSTALL_DIR${NC}"
   439|echo ""
   440|
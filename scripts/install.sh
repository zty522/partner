#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Partner - Universal Linux Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/zty522/partner/main/scripts/install.sh | bash
# ──────────────────────────────────────────────────────────────
set -euo pipefail 2>/dev/null || set -eu

REPO_URL="https://github.com/zty522/partner.git"
INSTALL_DIR="${PARTNER_HOME:-$HOME/.partner}"
PYTHON_MIN="3.10"
PYTHON_MAX="3.12"
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${GREEN}\xe2\x9c\x93${NC} $1"; }
warn()  { echo -e "${YELLOW}\xe2\x9a\xa0${NC} $1"; }
error() { echo -e "${RED}\xe2\x9c\x97${NC} $1"; }
header(){ echo -e "\n${BOLD}${CYAN}--- $1 ---${NC}\n"; }

# ── Detect OS ──
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "$ID"
    elif command -v lsb_release &>/dev/null; then
        lsb_release -si | tr '[:upper:]' '[:lower:]'
    elif [ "$(uname)" = "Darwin" ]; then
        echo "macos"
    else
        echo "unknown"
    fi
}
OS=$(detect_os)
ARCH=$(uname -m)
info "System: ${OS} ${ARCH}"

# ── Version comparison helpers ──
ver_lte() { printf '%s\n%s\n' "$1" "$2" | sort -C -V; }
ver_lt()  { ! ver_lte "$2" "$1"; }
ver_gte() { ver_lte "$2" "$1"; }

# ── Get Python major.minor version ──
get_python_ver() {
    local py=$1
    $py --version 2>&1 | grep -oP '\d+\.\d+' | head -1
}

# ── Check if Python version is valid (3.10-3.12) ──
is_valid_python() {
    local py=$1
    local ver
    ver=$(get_python_ver "$py")
    [ -z "$ver" ] && return 1
    local major minor
    major=$(echo "$ver" | cut -d. -f1)
    minor=$(echo "$ver" | cut -d. -f2)
    [ "$major" = "3" ] && [ "$minor" -ge 10 ] && [ "$minor" -le 12 ]
}

# ── Find a valid system Python ──
find_system_python() {
    for p in python3 python python3.12 python3.11 python3.10; do
        if command -v "$p" &>/dev/null && is_valid_python "$p"; then
            echo "$p"
            return 0
        fi
    done
    return 1
}

# ── Install Python 3.12 via pyenv ──
install_via_pyenv() {
    if command -v pyenv &>/dev/null; then
        warn "Valid Python not found. Installing Python 3.12.3 via pyenv..."
        pyenv install 3.12.3 -s 2>&1 | tail -1
        local exit_code=$?
        if [ $exit_code -eq 0 ]; then
            local pyenv_root="${PYENV_ROOT:-$HOME/.pyenv}"
            local py_path="$pyenv_root/versions/3.12.3/bin/python3"
            if [ -x "$py_path" ]; then
                info "Python 3.12.3 installed via pyenv"
                echo "$py_path"
                return 0
            fi
        fi
        warn "pyenv install failed, trying other methods..."
    fi
    return 1
}

# ── Install Python 3.12 via system package manager ──
install_via_package_manager() {
    case "$OS" in
        ubuntu|debian|linuxmint|pop|elementary|zorin)
            warn "Installing Python 3.12 via apt..."
            # Try deadsnakes PPA first for older Ubuntu/Debian
            if command -v add-apt-repository &>/dev/null; then
                sudo add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
            fi
            sudo apt-get update -qq
            sudo apt-get install -y -qq python3.12 python3.12-venv python3.12-distutils 2>/dev/null || \
            sudo apt-get install -y -qq python3.12 2>/dev/null || return 1
            if command -v python3.12 &>/dev/null; then
                echo "python3.12"
                return 0
            fi
            ;;
        centos|fedora|rhel|rocky|almalinux)
            warn "Installing Python 3.12 via yum/dnf..."
            if command -v dnf &>/dev/null; then
                sudo dnf install -y python3.12 2>/dev/null && { echo "python3.12"; return 0; }
            fi
            if command -v yum &>/dev/null; then
                sudo yum install -y python3.12 2>/dev/null && { echo "python3.12"; return 0; }
            fi
            ;;
        arch|manjaro|endeavour)
            warn "Installing Python 3.12 via pacman..."
            sudo pacman -Sy --noconfirm python 2>/dev/null && \
                command -v python &>/dev/null && is_valid_python python && \
                { echo "python"; return 0; }
            ;;
        alpine)
            warn "Installing Python 3.12 via apk..."
            sudo apk add python3 py3-pip 2>/dev/null && \
                command -v python3 &>/dev/null && is_valid_python python3 && \
                { echo "python3"; return 0; }
            ;;
        macos|darwin)
            warn "Installing Python 3.12 via Homebrew..."
            if command -v brew &>/dev/null; then
                brew install python@3.12 2>&1 | tail -1
                local exit_code=$?
                if [ $exit_code -eq 0 ]; then
                    for p in python3.12 python3; do
                        if command -v "$p" &>/dev/null && is_valid_python "$p"; then
                            echo "$p"
                            return 0
                        fi
                    done
                    # Find brew-installed python
                    for prefix in /usr/local /opt/homebrew; do
                        local bp="$prefix/opt/python@3.12/bin/python3.12"
                        [ -x "$bp" ] && { echo "$bp"; return 0; }
                    done
                fi
            fi
            ;;
    esac
    return 1
}

# ── Compile Python 3.12 from source ──
install_via_source() {
    warn "Package manager install failed. Compiling Python 3.12.3 from source..."
    local build_dir="$HOME/.partner-build"
    local install_prefix="$HOME/.local/python"
    local python_url="https://www.python.org/ftp/python/3.12.3/Python-3.12.3.tar.xz"

    # Install build dependencies
    info "Installing build dependencies..."
    case "$OS" in
        ubuntu|debian|linuxmint|pop|elementary|zorin)
            sudo apt-get install -y -qq build-essential libssl-dev zlib1g-dev \
                libbz2-dev libreadline-dev libsqlite3-dev curl libncursesw5-dev \
                xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev 2>/dev/null || true
            ;;
        centos|fedora|rhel|rocky|almalinux)
            sudo yum groupinstall -y "Development Tools" 2>/dev/null || \
            sudo dnf groupinstall -y "Development Tools" 2>/dev/null || true
            sudo yum install -y openssl-devel bzip2-devel libffi-devel 2>/dev/null || \
            sudo dnf install -y openssl-devel bzip2-devel libffi-devel 2>/dev/null || true
            ;;
        macos|darwin)
            # On macOS, xcode tools should be enough
            xcode-select --install 2>/dev/null || true
            ;;
    esac

    mkdir -p "$build_dir"
    cd "$build_dir"

    info "Downloading Python 3.12.3 source..."
    curl -fsSL "$python_url" -o Python-3.12.3.tar.xz || {
        error "Failed to download Python source"
        cd "$HOME"
        rm -rf "$build_dir"
        return 1
    }

    tar xf Python-3.12.3.tar.xz
    cd Python-3.12.3

    info "Configuring build..."
    ./configure --prefix="$install_prefix" --enable-optimizations --with-ensurepip=install 2>&1 | tail -5

    info "Compiling Python (this may take a few minutes)..."
    make -j"$(nproc 2>/dev/null || echo 2)" 2>&1 | tail -5

    info "Installing Python..."
    make install 2>&1 | tail -5

    cd "$HOME"
    rm -rf "$build_dir"

    local py_path="$install_prefix/bin/python3"
    if [ -x "$py_path" ]; then
        info "Python 3.12.3 compiled and installed to $install_prefix"
        # Add to PATH for this session
        export PATH="$install_prefix/bin:$PATH"
        echo "$py_path"
        return 0
    fi

    error "Failed to compile Python from source"
    return 1
}

# ── Ensure a valid Python is available ──
ensure_python() {
    # First try to find one on the system
    local py
    py=$(find_system_python) && {
        info "Python: $(get_python_ver "$py") ($(command -v "$py" || echo "$py"))"
        echo "$py"
        return 0
    }

    header "Installing Python 3.12"

    # Try pyenv
    py=$(install_via_pyenv) && {
        info "Python: $(get_python_ver "$py") ($py)"
        echo "$py"
        return 0
    }

    # Try package manager
    py=$(install_via_package_manager) && {
        info "Python: $(get_python_ver "$py") ($(command -v "$py"))"
        echo "$py"
        return 0
    }

    # Try source compile
    py=$(install_via_source) && {
        info "Python: $(get_python_ver "$py") ($py)"
        echo "$py"
        return 0
    }

    error "Failed to install Python 3.12. Please install Python 3.10, 3.11, or 3.12 manually."
    exit 1
}

# ── Detect / install Git ──
ensure_git() {
    if command -v git &>/dev/null; then
        info "Git: $(git --version 2>&1 | head -1)"
        return 0
    fi

    warn "Git not found. Installing..."
    case "$OS" in
        ubuntu|debian|linuxmint|pop|elementary|zorin)
            sudo apt-get install -y -qq git ;;
        centos|fedora|rhel|rocky|almalinux)
            sudo yum install -y git 2>/dev/null || sudo dnf install -y git 2>/dev/null ;;
        arch|manjaro)
            sudo pacman -Sy --noconfirm git ;;
        alpine)
            sudo apk add git ;;
        macos|darwin)
            if command -v brew &>/dev/null; then
                brew install git
            else
                error "Please install Git manually: https://git-scm.com/downloads"
                exit 1
            fi
            ;;
        *)
            error "Please install Git manually: https://git-scm.com/downloads"
            exit 1
            ;;
    esac

    if ! command -v git &>/dev/null; then
        error "Git installation failed."
        exit 1
    fi
    info "Git: $(git --version 2>&1 | head -1)"
}

# ── Clone repository with retry ──
clone_repo() {
    if [ -d "$INSTALL_DIR" ]; then
        info "Repository directory exists: $INSTALL_DIR"
        return 0
    fi

    info "Cloning Partner repository..."

    # Configure Git for stability
    git config --global http.version HTTP/1.1 2>/dev/null || true
    git config --global http.postBuffer 524288000 2>/dev/null || true

    local max_attempts=3
    local attempt=1

    while [ $attempt -le $max_attempts ]; do
        if git clone "$REPO_URL" "$INSTALL_DIR" 2>/dev/null; then
            info "Repository cloned successfully"
            return 0
        fi
        if [ $attempt -lt $max_attempts ]; then
            warn "Clone failed (attempt $attempt/$max_attempts), retrying in 3 seconds..."
            sleep 3
        fi
        attempt=$((attempt + 1))
    done

    # HTTPS failed - try SSH
    warn "HTTPS clone failed, trying SSH..."
    local ssh_url="git@github.com:zty522/partner.git"
    attempt=1
    while [ $attempt -le 2 ]; do
        if git clone "$ssh_url" "$INSTALL_DIR" 2>/dev/null; then
            info "Repository cloned via SSH"
            return 0
        fi
        attempt=$((attempt + 1))
        sleep 2
    done

    error "Failed to clone repository. Please check your internet connection."
    error "Try manually: git clone $REPO_URL $INSTALL_DIR"
    exit 1
}

# ── Install Partner via pip ──
install_partner() {
    cd "$INSTALL_DIR"

    info "Installing Partner package..."

    # Try with --break-system-packages first (newer pip on some distros)
    $PY -m pip install -e . -q --break-system-packages 2>/dev/null || \
        $PY -m pip install -e . -q --user 2>/dev/null || \
        $PY -m pip install -e . -q

    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        error "pip install failed with exit code $exit_code"
        exit 1
    fi

    info "Partner package installed"
}

# ── Create startup script ──
create_startup_script() {
    if command -v partner &>/dev/null; then
        info "Partner command already available in PATH"
        return 0
    fi

    mkdir -p "$HOME/.local/bin"

    cat > "$HOME/.local/bin/partner" << 'PYEOF'
#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.expanduser("~/.partner"))
from partner.cli import main
main()
PYEOF

    chmod +x "$HOME/.local/bin/partner"

    # Add to PATH for current session
    export PATH="$HOME/.local/bin:$PATH"

    # Add to shell profiles for future sessions
    for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.profile"; do
        if [ -f "$rc" ] && ! grep -q 'PATH="$HOME/.local/bin:$PATH"' "$rc" 2>/dev/null; then
            echo "" >> "$rc"
            echo '# Added by Partner installer' >> "$rc"
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc"
        fi
    done

    info "Startup script created at ~/.local/bin/partner"
    info "Added ~/.local/bin to PATH in shell profiles"
}

# ═══════════════════════════════════════════════
# MAIN INSTALLATION
# ═══════════════════════════════════════════════

header "Installing Partner"

# Step 1: Ensure valid Python
PY=$(ensure_python)

# Step 2: Ensure Git
ensure_git

# Step 3: Check if already installed and working
if command -v partner &>/dev/null && $PY -c "import partner; print('ok')" 2>/dev/null; then
    info "Partner is already installed and functional"
    info "Run 'partner update' to update to the latest version"
    exit 0
fi

# Step 4: Clean up broken installations
if command -v partner &>/dev/null; then
    warn "Removing stale partner binary..."
    rm -f "$(command -v partner)" 2>/dev/null || true
fi

# Step 5: Clone repository
clone_repo

# Step 6: Install via pip
install_partner

# Step 7: Create startup scripts
create_startup_script

# Step 8: Done
header "Installation complete"
echo ""
echo -e "${GREEN}  \xe2\x9c\x93 Partner installed successfully. Run 'partner' to start.${NC}"
echo ""
echo -e "${CYAN}  Installation directory: $INSTALL_DIR${NC}"
echo ""

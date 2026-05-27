#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Partner 🤝 — 一键安装脚本 (Linux)
# 用法: curl -fsSL https://raw.githubusercontent.com/zty522/partner/main/scripts/install.sh | bash
# ──────────────────────────────────────────────────────────────
set -euo pipefail

REPO_URL="https://github.com/zty522/partner.git"
INSTALL_DIR="${PARTNER_HOME:-$HOME/.partner}"
PYTHON_MIN="3.10"

# ── ANSI ──
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

info()  { echo -e "  ${GREEN}✓${NC} $1"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $1"; }
skip()  { echo -e "  ${DIM}○${NC} $1"; }
error() { echo -e "  ${RED}✗${NC} $1"; }
step()  { echo -e "\n${BOLD}${CYAN}  ▸ $1${NC}"; echo -e "  ${DIM}$(printf '%.0s─' {1..46})${NC}"; }

# ── Banner ──
echo ""
echo -e "  ${BOLD}${CYAN}🤝 Partner${NC} ${DIM}— AI Research Companion${NC}"
echo -e "  ${DIM}One-click installer for Linux${NC}"
echo -e "  ${CYAN}$(printf '%.0s━' {1..46})${NC}"
echo ""

# ── 检测系统 ──
step "检查系统环境"
OS=""
[ -f /etc/os-release ] && . /etc/os-release
info "系统: ${BOLD}${OS:-$(uname)}${NC} $(uname -m)"

# ── Python ──
check_python() {
    for p in python3 python; do
        if command -v $p &>/dev/null; then
            local ver=$($p --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
            if [ "$(echo -e "$ver\n$PYTHON_MIN" | sort -V | head -1)" = "$PYTHON_MIN" ]; then
                echo "$p"; return
            fi
        fi
    done
}

PY=$(check_python)
if [ -z "$PY" ]; then
    warn "需要 Python ${PYTHON_MIN}+，正在安装..."
    case "$OS" in
        ubuntu|debian) sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip python3-venv; PY=python3 ;;
        centos|fedora|rhel) sudo yum install -y python3 python3-pip; PY=python3 ;;
        arch) sudo pacman -Sy --noconfirm python python-pip; PY=python ;;
        alpine) sudo apk add python3 py3-pip; PY=python3 ;;
        *) error "请手动安装 Python ${PYTHON_MIN}+"; exit 1 ;;
    esac
fi
info "Python: ${BOLD}$($PY --version 2>&1 | head -1)${NC}"

# ── git ──
if ! command -v git &>/dev/null; then
    warn "git 未安装，正在安装..."
    case "$OS" in
        ubuntu|debian) sudo apt-get install -y -qq git ;;
        centos|fedora) sudo yum install -y git ;;
        arch) sudo pacman -Sy --noconfirm git ;;
        alpine) sudo apk add git ;;
    esac
fi
info "git: ${BOLD}$(git --version 2>&1 | head -1)${NC}"

# ── 安装 Partner ──
step "安装 Partner"
if [ -d "$INSTALL_DIR" ]; then
    info "Partner 目录已存在，更新中..."
    cd "$INSTALL_DIR"
    git pull --ff-only 2>&1 | head -3 || true
else
    info "克隆 Partner 仓库..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

$PY -m pip install -e . -q 2>/dev/null
info "Partner 安装完成"

# ── PATH 链接 ──
if ! command -v partner &>/dev/null; then
    mkdir -p "$HOME/.local/bin"
    cat > "$HOME/.local/bin/partner" << 'PYEOF'
#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.expanduser("~/.partner"))
from partner.cli import main
main()
PYEOF
    chmod +x "$HOME/.local/bin/partner"
    for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
        if [ -f "$rc" ] && ! grep -q '\.local/bin' "$rc" 2>/dev/null; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc"
        fi
    done
fi
info "partner 命令: ${BOLD}$HOME/.local/bin/partner${NC}"

# ── 完成 ──
echo ""
echo -e "  ${GREEN}${BOLD}━━━ 🎉 安装完成! ━━━${NC}"
echo ""
echo -e "  ${BOLD}安装目录:${NC} ${INSTALL_DIR}"
echo ""
echo -e "  ${CYAN}接下来:${NC}"
echo -e "    ${BOLD}partner setup${NC}           首次配置向导"
echo -e "    ${BOLD}partner status${NC}          查看状态"
echo -e "    ${BOLD}partner bot start qq${NC}    启动 QQ 机器人"
echo -e "    ${BOLD}partner update${NC}          更新到最新版本"
echo ""

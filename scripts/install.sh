#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Partner 🤝 — 一键安装脚本 (Linux)
# 用法: curl -fsSL https://raw.githubusercontent.com/zty522/partner/main/scripts/install.sh | bash
# ──────────────────────────────────────────────────────────────
set -euo pipefail

REPO_URL="https://github.com/zty522/partner.git"
INSTALL_DIR="${PARTNER_HOME:-$HOME/.partner}"
PYTHON_MIN="3.10"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

info()  { echo -e "  ${GREEN}✓${NC} $1"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $1"; }
error() { echo -e "  ${RED}✗${NC} $1"; }

echo ""
echo -e "  ${BOLD}${CYAN}🤝 Partner${NC} ${DIM}— AI Research Companion${NC}"
echo -e "  ${CYAN}$(printf '%.0s━' {1..46})${NC}"
echo ""

# ── 1. 环境检查 ──
echo -e "${BOLD}${CYAN}  ▸ 环境检查${NC}"
echo -e "  ${DIM}$(printf '%.0s─' {1..46})${NC}"

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
    OS=""; [ -f /etc/os-release ] && . /etc/os-release
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

if ! command -v git &>/dev/null; then
    OS=""; [ -f /etc/os-release ] && . /etc/os-release
    warn "git 未安装，正在安装..."
    case "$OS" in
        ubuntu|debian) sudo apt-get install -y -qq git ;;
        centos|fedora) sudo yum install -y git ;;
        arch) sudo pacman -Sy --noconfirm git ;;
        alpine) sudo apk add git ;;
    esac
fi
info "git: ${BOLD}$(git --version 2>&1 | head -1)${NC}"

# ── 2. 检测已有安装 ──
echo ""
echo -e "${BOLD}${CYAN}  ▸ 检测已有安装${NC}"
echo -e "  ${DIM}$(printf '%.0s─' {1..46})${NC}"

# 优先走更新路径：二进制存在 + 模块可加载
if command -v partner &>/dev/null && $PY -c "import partner; print('ok')" 2>/dev/null; then
    info "Partner 已安装且可用，执行更新..."
    exec partner update
fi

# 清理损坏/残留
_cleaned=false
if command -v partner &>/dev/null; then
    warn "发现 Partner 二进制文件但模块无法加载，自动清理..."
    rm -f "$(command -v partner)" 2>/dev/null || true
    _cleaned=true
fi
if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR" 2>/dev/null || true
    _cleaned=true
elif [ -f "$INSTALL_DIR" ]; then
    rm -f "$INSTALL_DIR" 2>/dev/null || true
    _cleaned=true
fi

if [ "$_cleaned" = true ]; then
    echo -e "  ${GREEN}  已清理完毕，继续全新安装...${NC}"
else
    info "未检测到已有安装"
fi

# ── 3. 下载 Partner ──
echo ""
echo -e "${BOLD}${CYAN}  ▸ 下载 Partner${NC}"
echo -e "  ${DIM}$(printf '%.0s─' {1..46})${NC}"

info "克隆仓库..."
git clone "$REPO_URL" "$INSTALL_DIR"
cd "$INSTALL_DIR"

# ── 4. 安装 ──
echo ""
echo -e "${BOLD}${CYAN}  ▸ 安装${NC}"
echo -e "  ${DIM}$(printf '%.0s─' {1..46})${NC}"

$PY -m pip install -e . -q 2>/dev/null
info "Python 包安装完成"

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
info "partner 命令已就绪"

# ── 5. 运行 setup 向导 ──
echo ""
echo -e "${BOLD}${CYAN}  ▸ 配置向导${NC}"
echo -e "  ${DIM}$(printf '%.0s─' {1..46})${NC}"
echo ""

export PATH="$HOME/.local/bin:$PATH"
exec partner setup

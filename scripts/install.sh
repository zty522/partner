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

# ── 1. 检测 Python ──
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
    OS=""
    [ -f /etc/os-release ] && . /etc/os-release
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

# ── 2. 检测残留/损坏的安装 ──
echo ""
echo -e "${BOLD}${CYAN}  ▸ 检测已有安装${NC}"
echo -e "  ${DIM}$(printf '%.0s─' {1..46})${NC}"

# Test: does 'partner' binary exist AND can it actually load the module?
_partner_ok=false
if command -v partner &>/dev/null; then
    # 直接测试 Python 模块是否能加载（不依赖 partner CLI 是否有 --version 参数）
    if $PY -c "import partner; print('ok')" 2>/dev/null; then
        _partner_ok=true
    else
        warn "发现 Partner 二进制文件但模块加载失败"
        echo -e "  ${DIM}  原因: 旧安装可能不完整（pip 可编辑安装链接损坏、${NC}"
        echo -e "  ${DIM}        仓库目录被删除或文件不完整）${NC}"
        echo ""
        echo -e "  ${YELLOW}  自动清理中...${NC}"

        # 清理损坏的二进制
        _partner_path="$(command -v partner)"
        rm -f "$_partner_path" 2>/dev/null || true
        info "已删除损坏的二进制: $_partner_path"

        # 清理损坏的仓库（如果有备份）
        if [ -d "$INSTALL_DIR" ]; then
            mv "$INSTALL_DIR" "${INSTALL_DIR}.bak.$(date +%s)"
            info "已备份旧目录: ${INSTALL_DIR}.bak.$(date +%s)"
        fi

        echo ""
        echo -e "  ${GREEN}  已清理完毕，继续全新安装...${NC}"
    fi
else
    info "未检测到已有安装"
fi

# ── 3. 下载 Partner ──
echo ""
echo -e "${BOLD}${CYAN}  ▸ 下载 Partner${NC}"
echo -e "  ${DIM}$(printf '%.0s─' {1..46})${NC}"

if [ "$_partner_ok" = true ]; then
    info "Partner 已安装且可用，执行更新..."
    exec partner update
elif [ -d "$INSTALL_DIR/.git" ]; then
    info "已存在，更新到最新..."
    cd "$INSTALL_DIR"
    git pull --ff-only 2>&1 | head -3 || true
    cd "$INSTALL_DIR"
elif [ -d "$INSTALL_DIR" ]; then
    # Directory exists but not a git repo — backup and re-clone
    warn "目录存在但不完整，正在重新安装..."
    mv "$INSTALL_DIR" "${INSTALL_DIR}.bak.$(date +%s)"
    info "克隆仓库..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
else
    info "克隆仓库..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# ── 4. 安装 ──
echo ""
echo -e "${BOLD}${CYAN}  ▸ 安装${NC}"
echo -e "  ${DIM}$(printf '%.0s─' {1..46})${NC}"

$PY -m pip install -e . -q 2>/dev/null
info "Python 包安装完成"

# PATH 链接（如果 pip 没创建 entry point）
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

# ── 5. 验证安装 ──
echo ""
echo -e "${BOLD}${CYAN}  ▸ 验证${NC}"
echo -e "  ${DIM}$(printf '%.0s─' {1..46})${NC}"

if ! command -v partner &>/dev/null; then
    error "partner 命令未在 PATH 中找到"
    echo -e "  ${YELLOW}请手动运行: export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
    echo -e "  ${YELLOW}然后: partner setup${NC}"
    exit 1
fi

# 最终验证：模块能加载
if $PY -c "import partner; print('ok')" 2>/dev/null; then
    info "Partner 安装成功！"
else
    error "Partner 二进制存在但无法加载模块"
    echo -e "  ${YELLOW}请手动检查:${NC}"
    echo -e "  ${DIM}    ls -la $INSTALL_DIR/partner/cli.py${NC}"
    echo -e "  ${DIM}    $PY -m pip list 2>/dev/null | grep partner${NC}"
    exit 1
fi

# ── 6. 运行 setup 向导 ──
echo ""
echo -e "${BOLD}${CYAN}  ▸ 配置向导${NC}"
echo -e "  ${DIM}$(printf '%.0s─' {1..46})${NC}"
echo ""

# 确保 PATH 包含 ~/.local/bin
export PATH="$HOME/.local/bin:$PATH"
exec partner setup

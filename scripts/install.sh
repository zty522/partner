#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Partner 🤝 — 一键安装脚本 (Linux)
# 用法: curl -fsSL https://raw.githubusercontent.com/zty522/partner/main/scripts/install.sh | bash
# ──────────────────────────────────────────────────────────────
set -euo pipefail

REPO_URL="https://github.com/zty522/partner.git"
INSTALL_DIR="${PARTNER_HOME:-$HOME/.partner}"
PYTHON_MIN="3.10"
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── 工具函数 ──
info()  { echo -e "${GREEN}✓${NC} $1"; }
warn()  { echo -e "${YELLOW}⚠${NC} $1"; }
error() { echo -e "${RED}✗${NC} $1"; }
header(){ echo -e "\n${BOLD}${CYAN}━━━ $1 ━━━${NC}\n"; }

# ── 检查系统 ──
header "检查系统环境"

OS=""
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
fi
info "系统: ${OS:-$(uname)} $(uname -m)"

# ── 检查 Python ──
check_python() {
    local py=""
    for p in python3 python; do
        if command -v $p &>/dev/null; then
            local ver=$($p --version 2>&1 | grep -oP '\d+\.\d+')
            if [ "$(echo -e "$ver\n$PYTHON_MIN" | sort -V | head -1)" = "$PYTHON_MIN" ]; then
                py=$p
                break
            fi
        fi
    done
    echo "$py"
}

PY=$(check_python)
if [ -z "$PY" ]; then
    warn "需要 Python $PYTHON_MIN+，正在安装..."
    case "$OS" in
        ubuntu|debian)
            sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip python3-venv
            PY=python3
            ;;
        centos|fedora|rhel)
            sudo yum install -y python3 python3-pip
            PY=python3
            ;;
        arch)
            sudo pacman -Sy --noconfirm python python-pip
            PY=python
            ;;
        alpine)
            sudo apk add python3 py3-pip
            PY=python3
            ;;
        *)
            error "不支持的发行版: $OS，请手动安装 Python $PYTHON_MIN+"
            exit 1
            ;;
    esac
fi
info "Python: $($PY --version)"

# ── 检查 pip ──
if ! $PY -m pip --version &>/dev/null; then
    warn "pip 未安装，正在安装..."
    case "$OS" in
        ubuntu|debian) sudo apt-get install -y -qq python3-pip ;;
        centos|fedora) sudo yum install -y python3-pip ;;
        *) $PY -m ensurepip --upgrade ;;
    esac
fi
info "pip: $($PY -m pip --version | head -1)"

# ── 检查 git ──
if ! command -v git &>/dev/null; then
    warn "git 未安装，正在安装..."
    case "$OS" in
        ubuntu|debian) sudo apt-get install -y -qq git ;;
        centos|fedora) sudo yum install -y git ;;
        arch) sudo pacman -Sy --noconfirm git ;;
        alpine) sudo apk add git ;;
    esac
fi
info "git: $(git --version 2>&1 | head -1)"

# ── 安装 Hermes Agent ──
header "安装 Hermes Agent"
if command -v hermes &>/dev/null; then
    info "Hermes 已安装: $(hermes --version 2>&1 | head -1)"
else
    info "正在安装 Hermes Agent..."
    $PY -m pip install hermes-agent -q
    if command -v hermes &>/dev/null; then
        info "Hermes 安装成功"
    else
        warn "hermes 命令未在 PATH 中，尝试添加..."
        export PATH="$HOME/.local/bin:$PATH"
        $PY -m pip install hermes-agent -q
    fi
fi

# ── 安装 Partner ──
header "安装 Partner"
if [ -d "$INSTALL_DIR" ]; then
    info "Partner 目录已存在: $INSTALL_DIR"
    cd "$INSTALL_DIR"
    git pull --ff-only 2>/dev/null || true
else
    info "克隆 Partner 仓库..."
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"
$PY -m pip install -e . -q
info "Partner 安装完成"

# ── 创建 PATH 链接 ──
if ! command -v partner &>/dev/null; then
    warn "partner 命令未在 PATH 中，添加 symlink..."
    mkdir -p "$HOME/.local/bin"
    ln -sf "$INSTALL_DIR/partner/cli.py" "$HOME/.local/bin/partner" 2>/dev/null || true
    export PATH="$HOME/.local/bin:$PATH"
    info "已添加 $HOME/.local/bin 到 PATH"
    # 写入 shell 配置
    for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
        if [ -f "$rc" ] && ! grep -q "PARTNER_HOME" "$rc" 2>/dev/null; then
            echo "" >> "$rc"
            echo "# Partner" >> "$rc"
            echo "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> "$rc"
        fi
    done
fi
info "partner: $(partner 2>&1 | head -1)"

# ── 设置 Cron ──
header "设置自动心跳"
if command -v hermes &>/dev/null; then
    info "请运行以下命令完成配置:"
    echo -e "  ${CYAN}partner setup${NC}"
    echo ""
    echo -e "然后启动 QQ 机器人:"
    echo -e "  ${CYAN}partner bot start qq${NC}"
    echo ""
    echo -e "Partner 后台研究循环将自动运行 (每 30 分钟心跳)"
else
    warn "Hermes 未安装，跳过 cron 设置"
    echo "请手动安装 Hermes: pip install hermes-agent"
fi

# ── 完成 ──
header "🎉 Partner 安装完成!"
echo -e "  ${BOLD}安装目录:${NC} $INSTALL_DIR"
echo -e "  ${BOLD}配置向导:${NC} partner setup"
echo -e "  ${BOLD}查看状态:${NC} partner status"
echo -e "  ${BOLD}更新:${NC}     partner update"
echo ""
echo -e "  ${CYAN}需要打开新终端或运行: source ~/.bashrc${NC}"
echo ""

# ── 清理 ──
cd "$HOME"

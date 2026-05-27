#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Partner 🤝 — 一键安装脚本 (Linux)
# 用法: curl -fsSL https://raw.githubusercontent.com/zty522/partner/main/scripts/install.sh | bash
# ──────────────────────────────────────────────────────────────
set -euo pipefail

REPO_URL="https://github.com/zty522/partner.git"
INSTALL_DIR="${PARTNER_HOME:-$HOME/.partner}"
PYTHON_MIN="3.10"

# ── ANSI Colors ──
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

info()  { echo -e "  ${GREEN}✓${NC} $1"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $1"; }
error() { echo -e "  ${RED}✗${NC} $1"; }
step()  { echo -e "\n${BOLD}${CYAN}  ▸ $1${NC}"; echo -e "  ${DIM}$(printf '%.0s─' {1..46})${NC}"; }

# ── Banner ──
echo ""
echo -e "  ${BOLD}${CYAN}🤝 Partner${NC} ${DIM}— AI Research Companion${NC}"
echo -e "  ${DIM}One-click installer for Linux${NC}"
echo -e "  ${CYAN}$(printf '%.0s━' {1..46})${NC}"
echo ""

# ── TTY detection: curl | bash pipes stdin, so read from /dev/tty ──
INPUT_TTY=""
if [ -t 0 ]; then
    INPUT_TTY="/dev/stdin"
elif [ -e /dev/tty ]; then
    INPUT_TTY="/dev/tty"
fi

# ── Arrow-key selection menu (bash version of prompt_choice) ──
# Usage: prompt_choice "question" "opt1" "opt2" ...
# Sets global SELECTED_INDEX (0-based)
prompt_choice() {
    local prompt="$1"
    shift
    local options=("$@")
    local n=${#options[@]}
    local selected=0

    if [ -z "$INPUT_TTY" ]; then
        # No TTY available — default to first option
        SELECTED_INDEX=0
        return
    fi

    # Hide cursor
    printf '\033[?25l' >&2

    # Print prompt
    echo -e "  ${BOLD}${prompt}${NC}"

    # Print all options
    for i in "${!options[@]}"; do
        if [ "$i" -eq "$selected" ]; then
            echo -e "    ${CYAN}▶ ${options[$i]}${NC}"
        else
            echo -e "    ${DIM}  ${options[$i]}${NC}"
        fi
    done

    # Move cursor back to first option
    printf "\033[${n}A" >&2

    while true; do
        # Read single char (raw mode via stty or read -rsn1)
        local key
        IFS= read -rsn1 key < "$INPUT_TTY" 2>/dev/null || break

        if [ "$key" = $'\x1b' ]; then
            # Escape sequence — read next 2 chars
            local seq2=""
            IFS= read -rsn1 -t 0.1 seq2 < "$INPUT_TTY" 2>/dev/null || true
            local seq3=""
            IFS= read -rsn1 -t 0.1 seq3 < "$INPUT_TTY" 2>/dev/null || true
            if [ "$seq2" = '[' ]; then
                case "$seq3" in
                    A) selected=$(( (selected - 1 + n) % n )) ;;  # Up
                    B) selected=$(( (selected + 1) % n )) ;;      # Down
                esac
            fi
        elif [ "$key" = '' ] || [ "$key" = $'\n' ] || [ "$key" = $'\r' ]; then
            # Enter — confirm
            break
        elif [[ "$key" =~ [1-9] ]]; then
            local idx=$(( key - 1 ))
            if [ "$idx" -lt "$n" ]; then
                selected=$idx
                break
            fi
        fi

        # Rewrite all options in place
        for i in "${!options[@]}"; do
            if [ "$i" -eq "$selected" ]; then
                printf "\r    \033[0;36m▶ %s\033[0m\033[K" "${options[$i]}"
            else
                printf "\r    \033[2m  %s\033[0m\033[K" "${options[$i]}"
            fi
            if [ "$i" -lt $((n - 1)) ]; then
                printf "\033[1B"
            fi
        done
        printf "\033[%dA" $((n - 1))
    done

    # Clear below options and show result
    printf "\033[%dB" "$n"
    printf "\033[J" >&2
    # Show cursor again
    printf '\033[?25h' >&2

    echo -e "    ${GREEN}▶ ${options[$selected]}${NC}"
    echo ""
    SELECTED_INDEX=$selected
}

# ── 检测系统 ──
step "检查系统环境"
OS=""
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
fi
info "系统: ${BOLD}${OS:-$(uname)}${NC} $(uname -m)"

# ── 检测 Python ──
check_python() {
    for p in python3 python; do
        if command -v $p &>/dev/null; then
            local ver=$($p --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
            if [ "$(echo -e "$ver\n$PYTHON_MIN" | sort -V | head -1)" = "$PYTHON_MIN" ]; then
                echo "$p"
                return
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

# ── 检测 git ──
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

# ── 选择后端 Agent ──
step "选择 AI 后端"
echo ""

prompt_choice "选择 AI 后端:" \
    "Hermes Agent (推荐)  — pip 安装，功能完整" \
    "OpenClaw (小龙虾)    — npm 安装，多渠道 AI 助手" \
    "两者都装" \
    "先不装，我自己配置"

AGENT_CHOICE=$((SELECTED_INDEX + 1))

case "$AGENT_CHOICE" in
    2|3)
        # OpenClaw 需要 Node.js
        if ! command -v node &>/dev/null || [ "$(node --version 2>&1 | grep -oP '\d+' | head -1)" -lt 22 ]; then
            info "安装 Node.js 22 (OpenClaw 需要)..."
            if ! command -v n &>/dev/null; then
                npm install -g n 2>/dev/null || true
            fi
            export N_PREFIX="$HOME/.n"
            export PATH="$N_PREFIX/bin:$PATH"
            n 22 2>/dev/null || true
            mkdir -p "$HOME/.npm-global"
            npm config set prefix "$HOME/.npm-global" 2>/dev/null || true
            export PATH="$HOME/.npm-global/bin:$PATH"
        fi
        info "Node.js: ${BOLD}$(node --version 2>&1)${NC}"
        ;;
esac

case "$AGENT_CHOICE" in
    1)
        step "安装 Hermes Agent"
        $PY -m pip install hermes-agent -q 2>/dev/null && info "Hermes 安装成功" || warn "Hermes 安装失败，可稍后手动安装"
        ;;
    2)
        step "安装 OpenClaw"
        npm install -g openclaw@latest 2>&1 | tail -1 && info "OpenClaw 安装成功" || warn "OpenClaw 安装失败"
        for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
            [ -f "$rc" ] && grep -q "N_PREFIX" "$rc" 2>/dev/null && continue
            echo "" >> "$rc"
            echo "# Node.js (for OpenClaw)" >> "$rc"
            echo 'export N_PREFIX="$HOME/.n"' >> "$rc"
            echo 'export PATH="$N_PREFIX/bin:$HOME/.npm-global/bin:$PATH"' >> "$rc"
        done
        ;;
    3)
        step "安装 Hermes Agent + OpenClaw"
        $PY -m pip install hermes-agent -q 2>/dev/null && info "Hermes 安装成功" || warn "Hermes 安装失败"
        npm install -g openclaw@latest 2>&1 | tail -1 && info "OpenClaw 安装成功" || warn "OpenClaw 安装失败"
        for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
            [ -f "$rc" ] && grep -q "N_PREFIX" "$rc" 2>/dev/null && continue
            echo "" >> "$rc"
            echo "# Node.js (for OpenClaw)" >> "$rc"
            echo 'export N_PREFIX="$HOME/.n"' >> "$rc"
            echo 'export PATH="$N_PREFIX/bin:$HOME/.npm-global/bin:$PATH"' >> "$rc"
        done
        ;;
    4)
        info "跳过后端安装，你可稍后手动安装"
        ;;
esac

# ── 安装 Partner ──
step "安装 Partner"
if [ -d "$INSTALL_DIR" ]; then
    info "Partner 目录已存在: ${INSTALL_DIR}"
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
    mkdir -p "$HOME/.local/bin"
    cat > "$HOME/.local/bin/partner" << 'PYEOF'
#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.expanduser("~/.partner"))
from partner.cli import main
main()
PYEOF
    chmod +x "$HOME/.local/bin/partner"
    export PATH="$HOME/.local/bin:$PATH"
    for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
        if [ -f "$rc" ] && ! grep -q '\.local/bin' "$rc" 2>/dev/null; then
            echo "" >> "$rc"
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc"
        fi
    done
fi
info "partner: ${BOLD}$HOME/.local/bin/partner${NC}"

# ── 完成 ──
echo ""
echo -e "  ${GREEN}${BOLD}━━━ 🎉 Partner 安装完成! ━━━${NC}"
echo ""
echo -e "  ${BOLD}安装目录:${NC} ${INSTALL_DIR}"
echo ""
echo -e "  ${CYAN}接下来:${NC}"
echo -e "    1. 新开终端或执行: ${BOLD}source ~/.bashrc${NC}"
echo -e "    2. 运行配置向导:    ${BOLD}partner setup${NC}"
echo -e "    3. 查看状态:        ${BOLD}partner status${NC}"
echo -e "    4. 启动 QQ 机器人:  ${BOLD}partner bot start qq${NC}"
echo ""

cd "$HOME"

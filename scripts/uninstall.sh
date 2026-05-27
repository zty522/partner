#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Partner 🤝 — 卸载脚本 (Linux)
# ──────────────────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo "  正在卸载 Partner..."
echo ""

# 1. 停止 QQ 机器人
if command -v partner &>/dev/null; then
    echo "  停止 QQ 机器人..."
    partner bot stop qq 2>/dev/null || true
fi

# 2. 删除安装目录
INSTALL_DIR="${PARTNER_HOME:-$HOME/.partner}"
if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
    echo -e "  ${GREEN}✓${NC} 删除安装目录: $INSTALL_DIR"
fi

# 3. 删除 PATH 链接
rm -f "$HOME/.local/bin/partner" 2>/dev/null || true

# 4. 从 shell 配置中移除 PATH
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
    if [ -f "$rc" ]; then
        # 移除 Partner 相关行
        sed -i '/PARTNER_HOME/d' "$rc" 2>/dev/null || true
        # 清理多余的空行
        sed -i '/^# Partner$/d' "$rc" 2>/dev/null || true
    fi
done

# 5. 卸载 Python 包
pip uninstall partner-research -y 2>/dev/null || true

# 6. 清理工作区（可选）
WORKSPACE="${PARTNER_WORKSPACE:-$HOME/partner_workspace}"
if [ -d "$WORKSPACE" ]; then
    echo ""
    echo -e "  ${YELLOW}⚠${NC} 工作区目录仍存在: $WORKSPACE"
    echo "     如需删除: rm -rf $WORKSPACE"
fi

echo ""
echo -e "  ${GREEN}✓${NC} Partner 已卸载"
echo ""

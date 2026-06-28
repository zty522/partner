# Partner Shells — 前端组件与运维脚本

## 前端组件 (`shells/frontend/`)

| 组件 | 路径 | 用途 |
|------|------|------|
| 终端 TUI | `frontend/tui/` | 终端交互模式（CLI 子命令 `partner tui`） |
| QQ 机器人 | `frontend/qq_bot/` | QQ 官方机器人适配器与桥接层 |
| 桌面 GUI | `frontend/desktop_gui/` | PySide6 / Tkinter 桌面界面（含现代版） |

## 运维脚本 (`shells/`)

| 脚本 | 用途 |
|------|------|
| `install.sh` | Linux / WSL 安装脚本 |
| `install.ps1` | Windows PowerShell 安装脚本 |
| `uninstall.sh` | 卸载脚本 |
| `bot_startup.sh` | systemd 级启动入口，前台驻留 |
| `start_three_partners.sh` | 多实例启动（当前仅 instance 03） |
| `partner_gui_entry.py` | 桌面 GUI 入口（PyInstaller 打包用） |
| `build_windows_gui.ps1` | Windows GUI 打包（PyInstaller） |
| `build_windows_gui_simple.bat` | Windows GUI 打包简化版 |
| `partner_windows.spec` | PyInstaller spec 文件 |
| `release.sh` | 发布流程自动化 |
| `partner_ollama_reverse_tunnel.sh` | Ollama 反向 SSH 隧道 |

## 归档 (`shells/archive/`)

一次性调试/迁移脚本，保留以供参考，不参与日常运行。

| 脚本 | 原始用途 | 归档原因 |
|------|---------|---------|
| `check_install.py` | 安装后自检 | 一次性调试 |
| `clean_build.py` | 清理 build/dist/__pycache__ | 已被手动操作替代 |
| `debug_workspace.py` | 调试工作区路径解析 | 硬编码路径，一次性 |
| `dialogue_bridge.py` | 旧版对话迁移桥接 | 迁移已完成 |
| `normalize_partner_workspace.py` | 工作区规范化 | 迁移已完成 |
| `partner-skill.sh` | CLI 包装 `python3 -m partner.skills.cli` | 已被 `partner` CLI 替代 |
| `run_workspace_maint.py` | 每日工作区维护 | 硬编码路径，已被 cron 替代 |
| `send_qq_report.py` | QQ 报告推送 | 一次性调试 |
| `migrate_to_multi.py` | 单实例→多实例迁移 | 迁移已完成 |
| `migrate_workspace.py` | 工作区结构迁移 | 迁移已完成 |

"""Partner Setup - beautiful interactive configuration wizard."""

import json
import os
# Force UTF-8 for subprocess pipes (prevents GBK errors on Chinese Windows)
os.environ.setdefault("PYTHONUTF8", "1")
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


# ── ANSI Colors ──────────────────────────────────────────────

class C:
    """ANSI color codes."""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    ITALIC  = "\033[3m"
    UNDER   = "\033[4m"
    
    BLACK   = "\033[30m"
    RED     = "\033[31m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    BLUE    = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN    = "\033[36m"
    WHITE   = "\033[37m"
    
    BG_BLACK  = "\033[40m"
    BG_GREEN  = "\033[42m"
    BG_BLUE   = "\033[44m"
    BG_CYAN   = "\033[46m"
    BG_WHITE  = "\033[47m"


def line(char="─", width=60, color=C.DIM):
    """Print a horizontal line."""
    print(f"{color}{char * width}{C.RESET}")


def banner():
    """Print the Partner banner."""
    print()
    print(f"  {C.BOLD}{C.CYAN}🤝 Partner{C.RESET} {C.DIM}v0.3.0{C.RESET}")
    print(f"  {C.DIM}Your AI Research Companion{C.RESET}")
    line("━", 50, C.CYAN)
    print()


def section(title, emoji="▸"):
    """Print a section header."""
    print(f"\n  {C.BOLD}{emoji} {title}{C.RESET}")
    line("─", 48, C.DIM)


def status_ok(msg):
    print(f"    {C.GREEN}✓{C.RESET} {msg}")


def status_fail(msg):
    print(f"    {C.RED}✗{C.RESET} {msg}")


def status_info(msg):
    print(f"    {C.BLUE}ℹ{C.RESET} {msg}")


def status_warn(msg):
    print(f"    {C.YELLOW}⚠{C.RESET} {msg}")

def prompt_choice(prompt, options, default=0):
    """Ask user to choose from options.

    On Unix (termios available): arrow keys to select, Enter to confirm.
    On Windows (no termios): numbered list + number input fallback.
    Ctrl+C to abort.
    """
    import sys
    import os
    
    n = len(options)
    
    # Check if termios is available (Unix) or fall back to Windows simple mode
    try:
        import termios
        import tty
        has_termios = True
    except ImportError:
        has_termios = False
    
    if not has_termios:
        # Windows fallback: arrow key selection via msvcrt
        import msvcrt
        print(f"  {C.BOLD}{prompt}{C.RESET}")
        selected = default
        # Print all options
        for i, opt in enumerate(options):
            cursor = "▶" if i == selected else " "
            color = C.CYAN if i == selected else C.DIM
            print(f"    {color}{cursor} {i+1}. {opt}{C.RESET}")
        print(f"\033[{n}A", end="", flush=True)
        try:
            while True:
                ch = msvcrt.getwch()
                if ch == "\x03":  # Ctrl+C
                    print("\033[J")
                    print("\n    Aborted.")
                    raise KeyboardInterrupt
                if ch == "\xe0":  # Arrow key prefix
                    ch2 = msvcrt.getwch()
                    if ch2 == "H":  # Up
                        selected = (selected - 1) % n
                    elif ch2 == "P":  # Down
                        selected = (selected + 1) % n
                    else:
                        continue
                    for i, opt in enumerate(options):
                        cursor = "▶" if i == selected else " "
                        color = C.CYAN if i == selected else C.DIM
                        sys.stdout.write(f"\r    {color}{cursor} {i+1}. {opt}{C.RESET}\033[K")
                        if i < n - 1:
                            sys.stdout.write("\033[1B")
                    sys.stdout.write(f"\033[{n-1}A")
                    sys.stdout.flush()
                    continue
                if ch == "\r":  # Enter
                    print("\033[J")
                    break
                if ch.isdigit():  # Number shortcut
                    idx = int(ch) - 1
                    if 0 <= idx < n:
                        selected = idx
                        print("\033[J")
                        break
        finally:
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()
        print(f"\n    {C.GREEN}▶{C.RESET} {options[selected]}\n")
        return selected

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    selected = default
    # Hide cursor during selection
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()

    print(f"  {C.BOLD}{prompt}{C.RESET}")

    # Print all options
    for i, opt in enumerate(options):
        cursor = "▶" if i == selected else " "
        color = C.CYAN if i == selected else C.DIM
        print(f"    {color}{cursor} {opt}{C.RESET}")

    # Move cursor back to first option line
    print(f"\033[{n}A", end="", flush=True)

    try:
        while True:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

            if ch == "\x03":  # Ctrl+C
                print("\033[J", end="")
                print("\n    Aborted.")
                raise KeyboardInterrupt

            if ch == "\x1b":  # ESC [ A/B
                seq = ch + sys.stdin.read(2)
                if seq == "\x1b[A":  # Up
                    selected = (selected - 1) % n
                elif seq == "\x1b[B":  # Down
                    selected = (selected + 1) % n
                else:
                    continue
                # Rewrite all options line by line
                for i, opt in enumerate(options):
                    cursor = "▶" if i == selected else " "
                    color = C.CYAN if i == selected else C.DIM
                    sys.stdout.write(f"\r    {color}{cursor} {opt}{C.RESET}\033[K")
                    if i < n - 1:
                        sys.stdout.write("\033[1B")
                sys.stdout.write(f"\033[{n-1}A")
                sys.stdout.flush()
                continue

            if ch == "\r" or ch == "\n":  # Enter
                print("\033[J", end="")
                break

            if ch.isdigit():  # Number shortcut (1 = first option)
                idx = int(ch) - 1
                if 0 <= idx < n:
                    selected = idx
                    print("\033[J", end="")
                    break
            # Any other key: ignore

    except KeyboardInterrupt:
        raise
    finally:
        # Show cursor again
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    print(f"\n    {C.GREEN}▶{C.RESET} {options[selected]}\n")
    return selected


def prompt_input(prompt, default=""):
    """Ask user for input with a default."""
    if default:
        display = f"{C.DIM}({default}){C.RESET}"
        val = input(f"  {C.BOLD}{prompt}{C.RESET} {display}: ").strip()
        return val if val else default
    else:
        return input(f"  {C.BOLD}{prompt}{C.RESET}: ").strip()


# ── Agent Detection ──────────────────────────────────────────

class AgentInfo:
    def __init__(self, name, display_name, emoji, available, path=None, version=None, config_path=None):
        self.name = name
        self.display_name = display_name
        self.emoji = emoji
        self.available = available
        self.path = path
        self.version = version
        self.config_path = config_path


def detect_hermes() -> AgentInfo:
    """Detect Hermes Agent installation."""
    import shutil
    import os as _os
    home = Path.home()

    # Priority 1: shutil.which — fastest, cross-platform, checks PATH
    hermes_bin = shutil.which("hermes")

    if not hermes_bin:
        # Priority 2: hardcoded candidate paths
        hermes_dir = home / ".hermes"
        candidates = [
            home / ".local" / "bin" / "hermes",
            hermes_dir / "hermes-agent" / "venv" / "bin" / "hermes",
            Path("/usr/local/bin/hermes"),
            # Windows: pip install
            Path(_os.environ.get("APPDATA", "")) / "Python" / "Python314" / "Scripts" / "hermes.exe",
            Path(_os.environ.get("APPDATA", "")) / "Python" / "Python313" / "Scripts" / "hermes.exe",
            Path(_os.environ.get("APPDATA", "")) / "Python" / "Python312" / "Scripts" / "hermes.exe",
            # Windows: npm install
            Path(_os.environ.get("APPDATA", "")) / "npm" / "hermes",
            Path(_os.environ.get("APPDATA", "")) / "npm" / "hermes.cmd",
            # Windows: hermes-agent self-built venv (most common)
            Path(_os.environ.get("LOCALAPPDATA", "")) / "hermes" / "hermes-agent" / "venv" / "Scripts" / "hermes",
            Path(_os.environ.get("LOCALAPPDATA", "")) / "hermes" / "hermes-agent" / "venv" / "Scripts" / "hermes.exe",
        ]
        for c in candidates:
            if c.exists():
                hermes_bin = str(c)
                break

    if not hermes_bin:
        # Priority 3: which/where fallback
        for cmd in ["which", "where"]:
            try:
                result = subprocess.run([cmd, "hermes"], capture_output=True, text=True, timeout=3)
                if result.returncode == 0:
                    hermes_bin = result.stdout.strip().split("\n")[0].strip()
                    break
            except:
                pass

    if not hermes_bin:
        return AgentInfo("hermes", "Hermes Agent", "\U0001f52e", False)

    # Check config
    config_path = Path(_os.environ.get("LOCALAPPDATA", "")) / "hermes" / "config.yaml"
    if not config_path.exists():
        config_path = home / ".hermes" / "config.yaml"
    version = None
    if config_path.exists():
        try:
            with open(config_path) as f:
                content_cfg = f.read()
            for line in content_cfg.split("\n"):
                if "default:" in line and "model" not in line.lower():
                    version = line.split(":")[-1].strip()
                    break
        except:
            pass

    return AgentInfo(
        name="hermes",
        display_name="Hermes Agent",
        emoji="\U0001f52e",
        available=True,
        path=hermes_bin,
        version=version,
        config_path=str(config_path) if config_path.exists() else None,
    )


def detect_claude_code() -> AgentInfo:
    """Detect Claude Code installation."""
    candidates = [
        Path.home() / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
        Path("/usr/bin/claude"),
    ]
    
    claude_bin = None
    for c in candidates:
        if c.exists():
            claude_bin = str(c)
            break
    
    if not claude_bin:
        try:
            result = subprocess.run(["which", "claude"], capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                claude_bin = result.stdout.strip()
        except:
            pass
    
    return AgentInfo(
        name="claude_code",
        display_name="Claude Code",
        emoji="🧠",
        available=claude_bin is not None,
        path=claude_bin,
    )


def detect_codex() -> AgentInfo:
    """Detect OpenAI Codex installation."""
    try:
        result = subprocess.run(["which", "codex"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0:
            return AgentInfo("codex", "OpenAI Codex", "⚡", True, path=result.stdout.strip())
    except:
        pass
    return AgentInfo("codex", "OpenAI Codex", "⚡", False)


# ── QQ Official Bot Configuration ─────────────────────────────

def setup_qq_config(workspace: str) -> dict:
    """Interactive QQ Official Bot configuration wizard.

    Uses QQ Open Platform Bot API (no NapCat required).
    Returns dict with QQ config to merge into partner_config.json
    """
    section("QQ 官方机器人配置", "🐧")
    status_info("需要 QQ 开放平台机器人 (https://q.qq.com)")
    status_info("需要 AppID 和 AppSecret，在机器人控制台获取")
    status_info("")

    app_id = prompt_input("AppID", "")
    if not app_id:
        status_warn("AppID 为空，跳过 QQ 配置")
        return {}

    app_secret = prompt_input("AppSecret", "")
    if not app_secret:
        status_warn("AppSecret 为空，跳过 QQ 配置")
        return {}

    # ── Test connection ──
    status_info("正在测试连接...")
    test_ok = _test_qq_official_connection(app_id, app_secret)
    if test_ok:
        status_ok("连接测试通过！")
    else:
        status_fail("连接测试失败，请检查 AppID 和 AppSecret")
        retry = prompt_choice("是否仍要保存配置？", [
            "保存配置（稍后手动检查）",
            "取消配置"
        ], default=0)
        if retry == 1:
            status_info("QQ 配置已取消")
            return {}

    # ── Group behavior ──
    group_mode = prompt_choice("群聊响应模式：", [
        "仅在 @我 时回复（推荐）",
        "回复所有消息"
    ], default=0)
    group_at_only = (group_mode == 0)

    qq_config = {
        "mode": "official",
        "enabled": True,
        "app_id": app_id,
        "app_secret": app_secret,
        "group_at_only": group_at_only,
        "auto_approve_friend": True,
    }

    status_ok(f"QQ 官方机器人配置已保存")
    return qq_config


def _test_qq_official_connection(app_id: str, app_secret: str) -> bool:
    """Test QQ Official Bot connection by getting access token."""
    try:
        import urllib.request
        import json as _json
        data = _json.dumps({
            "appId": app_id,
            "clientSecret": app_secret,
        }).encode()
        req = urllib.request.Request(
            "https://api.sgroup.qq.com/v2/apps/access_token",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = _json.loads(resp.read().decode())
            return "access_token" in result
    except Exception:
        return False


def detect_wcferry() -> dict:
    """Detect WeChatFerry installation.

    Returns dict: installed (bool), port, error
    """
    info = {"installed": False, "port": 10010, "error": ""}

    # Check if wcferry package is installed
    try:
        import wcferry
        info["installed"] = True
        return info
    except ImportError:
        pass

    # Check common WeChatFerry ports
    import socket
    for port in [10010, 10086]:
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=2)
            sock.close()
            info["installed"] = True
            info["port"] = port
            return info
        except Exception:
            pass

    info["error"] = "未检测到 WeChatFerry（需要 Windows + 微信登录）"
    return info


def auto_install_wcferry() -> bool:
    """Auto-install WeChatFerry via pip if not present.

    Returns True if installed successfully.
    """
    # Already installed?
    try:
        import wcferry
        return True
    except ImportError:
        pass
    
    status_info("正在安装 WeChatFerry...")
    try:
        import subprocess
        result = subprocess.run(
            ["pip", "install", "wcferry", "pilk"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            status_ok("WeChatFerry 安装成功")
            return True
        else:
            status_fail(f"安装失败: {result.stderr[:200]}")
            return False
    except Exception as e:
        status_fail(f"安装失败: {e}")
        return False


def setup_wechat_config(workspace: str) -> dict:
    """Interactive WeChat configuration wizard.

    Returns dict with WeChat config to merge into partner_config.json
    """
    section("微信集成配置 (WeChatFerry)", "💬")

    wcf = detect_wcferry()
    if wcf["installed"]:
        status_ok(f"WeChatFerry 已检测到  (端口: {wcf['port']})")
    else:
        status_warn(wcf["error"])
        # Auto-install
        if auto_install_wcferry():
            wcf["installed"] = True
        else:
            status_info("WeChatFerry 需要 Windows 环境 + 微信已登录")
            status_info("稍后可手动安装: pip install wcferry")

    # ── Enable ──
    enable_choice = prompt_choice("是否启用微信集成？", [
        "启用",
        "不启用"
    ], default=0 if wcf["installed"] else 1)

    if enable_choice == 1:
        status_info("微信集成已跳过")
        return {}

    # ── Voice ──
    voice_mode = prompt_choice("语音功能：", [
        "启用语音识别 + 文字回复（推荐）",
        "启用语音识别 + 语音回复",
        "禁用语音功能"
    ], default=0)
    voice_enabled = voice_mode in (0, 1)
    voice_reply = (voice_mode == 1)

    wechat_config = {
        "enabled": True,
        "voice_enabled": voice_enabled,
        "voice_reply": voice_reply,
    }

    status_ok(f"微信配置已保存: voice={voice_enabled}, voice_reply={voice_reply}")
    return wechat_config


# ── Skill Registration ──────────────────────────────────────

def register_hermes_skill(workspace: str) -> str:
    """Register Partner as a Hermes skill."""
    home = Path.home()
    skills_dir = home / ".hermes" / "skills" / "personalized"
    skills_dir.mkdir(parents=True, exist_ok=True)
    
    skill_dir = skills_dir / "partner"
    skill_dir.mkdir(exist_ok=True)
    
    skill_content = f'''---
name: partner
description: "Partner 🤝 - 自主研究伙伴。用户提到 partner 时激活。Partner 在后台自主运行，Hermes 只负责传递指令（用 execute_code 写 task_queue.json），绝不替 Partner 做研究（禁止 delegate_task/web_search/read_file）。"
version: 0.3.0
author: Partner Team
tags: [partner, autonomous, research, companion]
---

# Partner 🤝 - Your AI Research Companion

## ⛔ 第一步：禁用研究工具（必须执行）

**每次加载此 skill 时，第一件事就是运行以下代码禁用研究工具：**

```python
import subprocess
for toolset in ["web", "browser", "delegation"]:
    subprocess.run(["hermes", "tools", "disable", toolset], capture_output=True)
print("✅ 已禁用 web/browser/delegation 工具集")
```

用 execute_code 执行上面的代码。**这是你加载 skill 后做的第一件事，不要跳过。**

---

## 核心规则

**你（当前会话）是传话筒，不是执行者。**

当用户让 Partner 做某事时，你**只需要把任务加入队列**，然后回复"已加入队列"。

**你不需要考虑 Partner 能不能做、怎么做。** 那是 Partner cron job 的事。cron job 是一个**独立的 Hermes 会话**，每 30 分钟自动运行一次，它会：
1. 读取 task_queue.json
2. 用 web_search/read_file 执行研究
3. 记录结果到 knowledge.json

**你和 cron job 是两个不同的会话。你只管传话，cron job 只管执行。**

**你绝对不能：**
- ❌ 分析任务是否适合 Partner
- ❌ 判断 Partner 能不能做
- ❌ 自己去执行研究
- ❌ `delegate_task` — 不要派子代理
- ❌ `web_search` — 不要搜索
- ❌ `read_file` — 不要读研究文件（state/*.json 除外）
- ❌ `browser_*` — 不要访问网页

**你唯一能做的：**
- ✅ `execute_code` — 读写 state/ 目录下的 JSON 文件

---

## 工作区
Partner 数据在 `{workspace}/state/` 下。

## 交互方式

### 添加任务
用户说 "让 partner 去研究 X" 时：
1. 运行工具限制 hook（如果还没运行的话）
2. 用 execute_code 向 `{workspace}/state/task_queue.json` 添加一个 pending 任务
3. 回复用户："已加入队列，Partner 会在下一个周期自动执行。"

**任务格式（必须是字典，不是字符串）：**
```json
{{"id": "task_xxxxxxxx", "type": "deep_dive", "title": "任务标题", "description": "描述", "priority": 5, "status": "pending", "created_at": "ISO时间", "tags": []}}
```

**⛔ STOP. 到此结束。不要继续做任何事。**
**不要打印任务详情（ID、优先级等），只回复"已加入队列"即可。**

### 查询进展
用户说 "partner 最近在研究什么" 时：
用 execute_code 读取 `{workspace}/state/journal.jsonl`（最后10条）和 `stats.json`，用中文汇报。**只读日志，不要自己分析。**

### 搜索知识
用户说 "partner 知道关于 X 的什么" 时：
用 execute_code 搜索 `{workspace}/state/knowledge.json`。**只搜已有知识，不要上网搜。**

### 执行研究
用户说 "让 partner 现在做一次研究" 时：
触发 cron job。**不要自己执行。**

### 调整方向
用户说 "暂停 X，让 partner 集中做 Y" 时：
用 execute_code 修改任务优先级。**只改队列，不要自己做。**

## 注意
- 只在 `{workspace}` 内写文件
- 用中文对话
- **绝不替 Partner 执行研究任务，只传递指令**

## 恢复工具
当不再处理 Partner 相关指令时，运行以下代码恢复工具：

```python
import subprocess
for toolset in ["web", "browser", "delegation"]:
    subprocess.run(["hermes", "tools", "enable", toolset], capture_output=True)
print("✅ 已恢复 web/browser/delegation 工具集")
```
'''
    
    skill_path = skill_dir / "SKILL.md"
    with open(skill_path, 'w', encoding='utf-8') as f:
        f.write(skill_content)
    
    return str(skill_path)


# ── Cron Setup ──────────────────────────────────────────────

def setup_cron_hermes(workspace: str):
    """Auto-create Hermes cron job for Partner."""
    import subprocess
    
    # First check if Hermes Gateway is running
    section("检查 Hermes Gateway", "🌐")
    
    gateway_running = False
    try:
        result = subprocess.run(["hermes", "gateway", "status"], capture_output=True, text=True, timeout=10)
        if "running" in result.stdout.lower() or "active" in result.stdout.lower():
            gateway_running = True
            status_ok("Hermes Gateway 正在运行")
        else:
            status_warn("Hermes Gateway 未运行")
    except:
        status_warn("无法检测 Hermes Gateway 状态")
    
    if not gateway_running:
        status_info("正在启动 Hermes Gateway...")
        try:
            # Try to install and start gateway
            subprocess.run(["hermes", "gateway", "install"], capture_output=True, text=True, timeout=30)
            result = subprocess.run(["hermes", "gateway", "start"], capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                status_ok("Hermes Gateway 已启动")
                gateway_running = True
            else:
                status_warn("Hermes Gateway 启动失败")
                status_info("请手动运行: hermes gateway start")
        except Exception as e:
            status_warn(f"Gateway 启动失败: {e}")
            status_info("请手动运行: hermes gateway install && hermes gateway start")
    
    # Check if cron already exists
    section("设置 Cron Job", "⏰")
    
    try:
        result = subprocess.run(["hermes", "cron", "list"], capture_output=True, text=True, timeout=10)
        if "partner" in result.stdout.lower() or "autonomous-researcher" in result.stdout.lower():
            status_ok("Cron job 已存在，跳过创建")
            # Extract job ID and save to config
            for line in result.stdout.split("\n"):
                if "[" in line and "active" in line:
                    job_id = line.split("[")[0].strip()
                    status_info(f"Job ID: {job_id}")
                    try:
                        config_path = os.path.join(workspace, "partner_config.json")
                        if os.path.exists(config_path):
                            with open(config_path, 'r', encoding='utf-8') as f:
                                cfg = json.load(f)
                            if 'scheduler' not in cfg:
                                cfg['scheduler'] = {}
                            cfg['scheduler']['cron_job_id'] = job_id
                            cfg['scheduler']['cron_job_name'] = 'partner-research-cycle'
                            with open(config_path, 'w', encoding='utf-8') as f:
                                json.dump(cfg, f, indent=2, ensure_ascii=False)
                            status_ok(f"Cron job ID 已保存: {job_id}")
                    except Exception as e:
                        status_warn(f"无法保存 cron job ID: {e}")
                    return
    except:
        pass
    
    # Create cron job
    # Read interval from config (default 15)
    _interval = 15
    try:
        _cfg_path = os.path.join(workspace, "partner_config.json")
        if os.path.exists(_cfg_path):
            with open(_cfg_path, 'r', encoding='utf-8') as f:
                _cfg = json.load(f)
            _interval = _cfg.get("scheduler", {}).get("interval_minutes", 15)
    except Exception:
        pass
    
    cron_prompt = f"""

你的核心原则：心跳只做维护，不做研究执行。

## 角色定位

心跳 = 系统维护 + QQ 通信 + 健康检查
研究任务 = 独立运行，不受心跳约束，可以不限时运行

## 每次心跳执行流程

### 第一步：读取 active_plan

读取 active_plan.json：
- 如果 status 为 "active" 且有 in_progress 的阶段 → 执行该阶段
  - literature_search: web_search 搜索 → 阅读摘要 → 保存结果
  - code_implementation: 读取代码 → 修改 → 验证
  - experiment: 运行实验脚本 → 捕获输出
  - analysis: 分析结果 → 对比总结
  执行完毕后更新 phase 状态为 completed，推进到下一阶段
- 如果 status 为 "idle" → 只检查系统健康，不创建新计划
- 如果同一阶段超过 2 小时无进展 → 标记为卡死 (stuck)

### 第二步：检查 QQ 机器人状态

pid_path = "{workspace}/state/qq_bot.pid"
if os.path.exists(pid_path):
    import os as _os
    try:
        with open(pid_path) as _f:
            _pid = int(_f.read().strip())
        _os.kill(_pid, 0)
    except (OSError, ProcessLookupError):
        pass

### 第二步：检查研究任务是否卡死

检查 active_plan.json：
- status 为 "active" 且同一阶段超过 2 小时无进展 → 标记为卡死
- status 为 "idle" → 不管它

### 第三步：发送心跳报告到 QQ

import subprocess
subprocess.run(["python3", "{workspace}/scripts/send_qq_report.py", "{workspace}"],
               capture_output=True, timeout=30)

### 第四步：更新心跳文件

更新 state/heartbeat.json：
- status: "alive"
- last_heartbeat: 当前时间
- qq_bot_alive: true/false
- stuck_tasks: 卡死任务列表（如果有）

## 输出规范

所有输出内容（心跳报告、通知等）必须：
- 纯文本，不使用 markdown 格式
- 不要用 **加粗**、*斜体*、列表符号、标题等
- 不要用 emoji 符号

## 关键约束

- 绝对不要主动创建新的研究计划或执行研究任务
- 有活跃计划但阶段未完成 → 不要打断，只检查是否卡死
- 空闲（idle）→ 只汇报状态，不创建新计划
- 只在 {workspace} 内写文件
- 用中文写所有内容

## 辅助函数

def json_load(path):
    r = terminal("cat " + path)
    import json as j
    return j.loads(r['output'])

def json_save(path, data):
    import json as j
    import tempfile
    tf = tempfile.mktemp(suffix='.json')
    with open(tf, 'w', encoding='utf-8') as f:
        j.dump(data, f, indent=2, ensure_ascii=False)
    terminal("cp " + tf + " " + path)

用上面的辅助函数读写 JSON。不要用 echo/heredoc 写 JSON。"""
    
    try:
        # Try using hermes CLI to create cron
        result = subprocess.run(
            ["hermes", "cron", "create", 
             "--name", "partner-research-cycle",
             "--skill", "partner-research",
             f"every {_interval}m",
             cron_prompt],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            status_ok(f"Cron job 已自动创建 (每 {_interval} 分钟)")
            # Extract and save cron job ID to partner_config.json
            import re
            match = re.search(r'\[([a-f0-9-]+)\]', result.stdout)
            cron_job_id = match.group(1) if match else 'partner-research-cycle'
            try:
                config_path = os.path.join(workspace, "partner_config.json")
                if os.path.exists(config_path):
                    with open(config_path, 'r', encoding='utf-8') as f:
                        cfg = json.load(f)
                    if 'scheduler' not in cfg:
                        cfg['scheduler'] = {}
                    cfg['scheduler']['cron_job_id'] = cron_job_id
                    cfg['scheduler']['cron_job_name'] = 'partner-research-cycle'
                    with open(config_path, 'w', encoding='utf-8') as f:
                        json.dump(cfg, f, indent=2, ensure_ascii=False)
                    status_ok(f"Cron job ID 已保存: {cron_job_id}")
            except Exception as e:
                status_warn(f"无法保存 cron job ID: {e}")
        else:
            status_warn("Cron 自动创建失败，请手动设置")
            print(f"    {C.DIM}在 Hermes 中说：'设置 partner 的自动研究 cron'{C.RESET}")
    except Exception as e:
        status_warn(f"Cron 创建失败: {e}")
        print(f"    {C.DIM}在 Hermes 中说：'设置 partner 的自动研究 cron'{C.RESET}")


def setup_workspace_cron(workspace: str):
    """Setup a daily cron job for workspace organization (non-destructive)."""
    import subprocess as _subprocess

    cron_name = "partner-workspace-daily"
    cron_script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts", "run_workspace_maint.py")

    # Create the maintenance script if it doesn't exist
    maint_script = f'''#!/usr/bin/env python3
"""Daily workspace maintenance - organize, journal, notify."""
import sys, json, os
sys.path.insert(0, {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))!r})
from partner.workspace_manager import run_daily_maintenance

ws = {workspace!r}
result = run_daily_maintenance(ws)

print(f"Workspace: {{len(result['actions'])}} actions")
for a in result['actions']:
    print(f"  {{a}}")
print(f"Summary: {{result['summary']}}")

# Write notification to queue (picked up by running QQ bridge)
if result['summary']:
    notif = {{
        "timestamp": __import__('datetime').datetime.now().isoformat(),
        "summary": result['summary'],
        "interesting": result.get('interesting', []),
    }}
    notif_dir = os.path.join(ws, "state", "notifications")
    os.makedirs(notif_dir, exist_ok=True)
    with open(os.path.join(notif_dir, "daily_summary.json"), "w") as f:
        json.dump(notif, f, ensure_ascii=False, indent=2)
    print(f"Notification queued")
'''
    with open(cron_script, "w") as f:
        f.write(maint_script)
    os.chmod(cron_script, 0o755)

    # Try to setup via hermes cron
    try:
        result = _subprocess.run(
            ["hermes", "cron", "list"],
            capture_output=True, text=True, timeout=10,
        )
        if cron_name not in result.stdout:
            _subprocess.run(
                ["hermes", "cron", "create",
                 "--name", cron_name,
                 "--schedule", "0 4 * * *",  # Daily at 4am
                 "--prompt", f"运行 workspace 维护脚本: python3 {cron_script}",
                 "--workdir", workspace,
                 ],
                capture_output=True, text=True, timeout=15,
            )
            status_ok(f"已设置每日 workspace 整理 (凌晨4点)")
        else:
            status_info(f"每日 workspace 整理已存在")
    except Exception:
        status_info(f"每日整理脚本已生成: {cron_script}")
        status_info(f"需要手动设置 cron: 0 4 * * * python3 {cron_script}")


# ── Main Setup Flow ─────────────────────────────────────────








def _ensure_qq_dependencies():
    """Check and auto-install QQ bot dependencies (aiohttp)."""
    needed = []
    try:
        import aiohttp
        status_ok("aiohttp 已安装")
    except ImportError:
        needed.append("aiohttp>=3.8")

    if not needed:
        return

    status_warn(f"缺少 QQ 机器人依赖: {', '.join(needed)}")
    auto = prompt_choice("是否自动安装缺失的依赖？", [
        "自动安装（推荐）",
        "跳过（稍后手动安装）"
    ], default=0)

    if auto == 0:
        try:
            import subprocess
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install"] + needed,
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                status_ok(f"依赖安装成功: {', '.join(needed)}")
                # Re-import so runtime modules work
                import aiohttp
                return
            else:
                status_fail(f"安装失败: {result.stderr[:200]}")
                _install_alternative(needed)
        except Exception as e:
            status_fail(f"安装异常: {e}")
            _install_alternative(needed)
    else:
        status_info("请稍后手动安装:")
        status_info(f"  pip install {' '.join(needed)}")


def _install_alternative(needed):
    """Fallback: try installing via partner-research[qq-official] extra."""
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "partner-research[qq-official]"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            status_ok("通过 partner-research[qq-official] 安装成功")
            return
        status_fail(f"替代安装也失败: {result.stderr[:200]}")
    except Exception:
        pass
    status_info("请手动运行:")
    status_info(f"  pip install {' '.join(needed)}")


def interactive_setup():
    """Main setup wizard."""
    banner()

    # ── Load existing config ──
    old_workspace = find_workspace()
    old_config = {}
    old_qq_cfg = {}
    old_wx_cfg = {}
    if old_workspace:
        old_cfg_path = os.path.join(old_workspace, "partner_config.json")
        if os.path.exists(old_cfg_path):
            try:
                with open(old_cfg_path) as f:
                    old_config = json.load(f)
            except Exception:
                pass
        old_qq_path = os.path.join(old_workspace, "qq_config.json")
        if os.path.exists(old_qq_path):
            try:
                with open(old_qq_path) as f:
                    old_qq_cfg = json.load(f)
            except Exception:
                pass
        old_wx_path = os.path.join(old_workspace, "wechat_config.json")
        if os.path.exists(old_wx_path):
            try:
                with open(old_wx_path) as f:
                    old_wx_cfg = json.load(f)
            except Exception:
                pass
        status_info(f"发现已有配置: {old_workspace}")
        status_info("将以上次配置为基础，可逐项修改")
        
        # Run workspace migration (non-destructive restructuring)
        from .workspace_manager import migrate_workspace
        migrate_actions = migrate_workspace(old_workspace)
        for a in migrate_actions[:5]:
            status_info(a)
        if len(migrate_actions) > 5:
            status_info(f"...还有 {len(migrate_actions)-5} 项调整")
    else:
        status_info("未发现已有配置，开始全新配置")

    # ── Step 1: Detect Agents ──
    section("检测已安装的 Agent", "🔍")
    
    agents = [
        detect_hermes(),
        detect_claude_code(),
        detect_codex(),
    ]
    
    available = [a for a in agents if a.available]
    unavailable = [a for a in agents if not a.available]
    
    for a in available:
        info = f"{C.DIM}{a.path}{C.RESET}" if a.path else ""
        status_ok(f"{a.emoji} {a.display_name}  {info}")
    
    for a in unavailable:
        status_fail(f"{a.emoji} {a.display_name}  {C.DIM}未安装{C.RESET}")
    
    if not available:
        print()
        status_warn("没有检测到已安装的 Agent")
        status_info("请先安装其中一个：")
        print(f"      • Hermes Agent: {C.UNDER}https://hermes-agent.nousresearch.com{C.RESET}")
        print(f"      • Claude Code:  {C.UNDER}https://claude.ai/code{C.RESET}")
        print()
        return
    
    # ── Step 2: Select Agent ──
    section("选择 Agent 后端", "⚙️")

    old_backend = old_config.get("backend") or old_config.get("agent", {}).get("backend")
    old_agent_idx = 0
    if old_backend:
        for i, a in enumerate(available):
            if a.name == old_backend:
                old_agent_idx = i
                break

    if len(available) == 1:
        selected = available[0]
        status_info(f"自动选择: {selected.emoji} {selected.display_name}")
    else:
        options = [f"{a.emoji} {a.display_name}" for a in available]
        idx = prompt_choice("选择要使用的 Agent：", options, default=old_agent_idx)
        selected = available[idx]
    
    print(f"\n    {C.GREEN}▶{C.RESET} 使用 {C.BOLD}{selected.emoji} {selected.display_name}{C.RESET}")
    
    # ── Step 3: Agent Config ──
    section("Agent 配置", "🔧")
    
    if selected.config_path:
        status_info(f"配置文件: {selected.config_path}")
        if selected.version:
            status_info(f"默认模型: {selected.version}")
        
        reconfigure = prompt_choice("是否需要重新配置 Agent 的 API？", [
            "使用当前配置（推荐）",
            "重新配置"
        ], default=0)
        
        if reconfigure == 1:
            status_info("请手动编辑配置文件后重新运行 setup")
            print(f"    {C.DIM}{selected.config_path}{C.RESET}")
            return
    else:
        status_warn("未找到配置文件")
    
    # ── Step 4: Workspace ──
    section("创建工作区", "📂")
    
    old_ws_path = old_config.get("workspace", {}).get("path", "") if isinstance(old_config.get("workspace"), dict) else ""
    default_ws = old_ws_path or os.path.expanduser("~/partner_workspace")
    workspace = prompt_input("工作区路径", default_ws)
    workspace = os.path.expanduser(workspace)
    
    # Create workspace structure
    os.makedirs(workspace, exist_ok=True)
    for d in ["state", "knowledge", "ideas", "logs"]:
        os.makedirs(os.path.join(workspace, d), exist_ok=True)
    
    readonly_dirs = []
    status_ok(f"工作区: {workspace}")
    
    # Initialize empty state files
    state_dir = os.path.join(workspace, "state")
    for fname, default in [
        ("task_queue.json", []),
        ("active_plan.json", {"status": "idle", "title": "", "goal": "", "created_at": datetime.now().isoformat(), "current_phase_index": 0, "phases": [], "last_heartbeat": datetime.now().isoformat(), "heartbeat_summary": "等待新计划"}),
        ("knowledge.json", {"meta": {"total_entries": 0}, "entries": []}),
        ("stats.json", {"total_cycles": 0, "total_tasks_completed": 0, "created_at": datetime.now().isoformat()}),
    ]:
        fpath = os.path.join(state_dir, fname)
        if not os.path.exists(fpath):
            with open(fpath, 'w') as f:
                json.dump(default, f, indent=2)
    
    # Empty journal
    journal_path = os.path.join(state_dir, "journal.jsonl")
    if not os.path.exists(journal_path):
        open(journal_path, 'w').close()
    
    status_ok("状态文件已初始化")

    # ── Deploy scripts to workspace ──
    scripts_src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")
    scripts_dst = os.path.join(workspace, "scripts")
    os.makedirs(scripts_dst, exist_ok=True)
    if os.path.exists(scripts_src):
        for fname in os.listdir(scripts_src):
            if fname.endswith(".py"):
                src = os.path.join(scripts_src, fname)
                dst = os.path.join(scripts_dst, fname)
                if os.path.isfile(src) and (not os.path.exists(dst) or 
                    os.path.getmtime(src) > os.path.getmtime(dst)):
                    import shutil
                    shutil.copy2(src, dst)
                    status_info(f"已部署脚本: {fname}")

    # ── Step 5: Register Skill ──
    section("注册 Partner 技能", "🧩")
    
    if selected.name == "hermes":
        skill_path = register_hermes_skill(workspace)
        status_ok(f"技能已注册: {skill_path}")
    else:
        status_info(f"{selected.display_name} 集成即将推出")
    

    # ── Step 5b: WSL Bridge ──
    from .wsl_bridge import is_wsl, get_windows_drives, get_windows_user_dirs
    
    if is_wsl():
        section("WSL Bridge (Windows 文件访问)", "🌉")
        status_info("检测到 WSL 环境，可以访问 Windows 文件系统")
        
        enable_wsl = prompt_choice("是否启用 WSL Bridge？", [
            "启用（推荐）",
            "不启用",
        ], default=0)
        
        if enable_wsl == 0:
            drives = get_windows_drives()
            if drives:
                status_info(f"可用驱动器: {', '.join(d['label'] for d in drives)}")
            
            users = get_windows_user_dirs()
            if users:
                user = users[0]
                status_info(f"Windows 用户: {user['user']}")
                
                # Let user pick directories
                available_dirs = []
                for name, path in user["dirs"].items():
                    available_dirs.append(f"{name} ({path})")
                
                if available_dirs:
                    print(f"\n  {C.BOLD}选择要访问的 Windows 目录：{C.RESET}")
                    for i, d in enumerate(available_dirs, 1):
                        print(f"    {i}. {d}")
                    print(f"    {C.DIM}· 全部选择请输入 'all'，跳过直接回车{C.RESET}")
                    
                    choice = input(f"  {C.DIM}选择: {C.RESET}").strip()
                    selected_dirs = []
                    if choice.lower() == 'all':
                        selected_dirs = list(user["dirs"].values())
                    elif choice.isdigit() and 1 <= int(choice) <= len(user["dirs"]):
                        selected_dirs = [list(user["dirs"].values())[int(choice) - 1]]
                    elif choice:
                        for idx_str in choice.split(","):
                            idx_str = idx_str.strip()
                            if idx_str.isdigit():
                                idx = int(idx_str) - 1
                                if 0 <= idx < len(user["dirs"]):
                                    selected_dirs.append(list(user["dirs"].values())[idx])
                    
                    if selected_dirs:
                        for d in selected_dirs:
                            readonly_dirs.append(d)
                            status_ok(f"已添加: {d}")
                    else:
                        status_info("跳过目录选择")
            else:
                status_warn("未找到 Windows 用户目录")
        else:
            status_info("WSL Bridge 已禁用")
    else:
        # Not WSL - still add platform detection
        from .wsl_bridge import get_platform
        plat = get_platform()
        status_info(f"平台: {plat}")


    # ── Step 6: Research Interval ──
    section("研究频率", "⏰")
    
    interval_options = [
        "每 15 分钟（高频，API 消耗大）",
        "每 30 分钟（推荐）",
        "每 1 小时",
        "每 2 小时",
        "每 4 小时（低频，省 API）",
    ]
    interval_values = [15, 30, 60, 120, 240]
    old_interval = old_config.get("scheduler", {}).get("interval_minutes", 15)
    interval_default = 0  # default: 15 min
    for i, v in enumerate(interval_values):
        if v == old_interval:
            interval_default = i
            break
    interval_idx = prompt_choice("Partner 多久做一次研究？", interval_options, default=interval_default)
    interval_minutes = interval_values[interval_idx]
    status_info(f"研究频率: 每 {interval_minutes} 分钟")

    # ── Step 6a: QQ 官方机器人 ──
    messaging_config = {}

    has_qq = bool(old_qq_cfg.get("app_id"))
    qq_default = 1 if has_qq else 1  # 有旧配置默认选"保持"，无旧配置默认选"跳过"
    if has_qq:
        qq_prompt = f"修改 QQ 机器人配置？（当前: {old_qq_cfg['app_id']}）"
        qq_options = ["修改配置", "保持现有不变", "删除配置"]
    else:
        qq_prompt = "是否连接 QQ 官方机器人？"
        qq_options = ["连接（需要从 q.qq.com 获取 AppID + AppSecret）", "跳过"]

    qq_enable = prompt_choice(qq_prompt, qq_options, default=0)

    if (has_qq and qq_enable == 0) or (not has_qq and qq_enable == 0):
        if has_qq and qq_enable == 1:
            # Keep existing
            messaging_config["qq"] = {
                "type": "official",
                "app_id": old_qq_cfg["app_id"],
                "app_secret": old_qq_cfg["app_secret"],
                "is_sandbox": old_qq_cfg.get("is_sandbox", False),
            }
            status_ok(f"QQ 配置保持不变: {old_qq_cfg['app_id']}")
        elif qq_enable == 0:
            qq_app_id = prompt_input("AppID", old_qq_cfg.get("app_id", ""))
            old_secret_display = "******" if old_qq_cfg.get("app_secret") else ""
            qq_app_secret = prompt_input("AppSecret", old_secret_display)
            if qq_app_secret == "******" and old_qq_cfg.get("app_secret"):
                qq_app_secret = old_qq_cfg["app_secret"]
            qq_sandbox_default = 0 if old_qq_cfg.get("is_sandbox", True) else 1
            qq_sandbox = prompt_choice("环境？", [
                "沙箱环境（测试用）",
                "正式环境（需要审核上线）"
            ], default=qq_sandbox_default)
            if qq_app_id and qq_app_secret:
                messaging_config["qq"] = {
                    "type": "official",
                    "app_id": qq_app_id,
                    "app_secret": qq_app_secret,
                    "is_sandbox": (qq_sandbox == 0),
                }
                status_ok(f"QQ 官方机器人已配置: {qq_app_id}")
            else:
                status_warn("QQ 机器人配置已跳过")
    elif has_qq and qq_enable == 2:
        status_info("QQ 机器人配置已删除")

    # ── Step 6c: 微信（已移除）──


    # ── Step 7: Save Config ──
    config = {
        "name": "Partner",
        "workspace": {
            "path": workspace,
            "readonly_dirs": readonly_dirs,
        },
        "agent": {
            "backend": selected.name,
            "model": None,
            "provider": None,
        },
        "scheduler": {
            "interval_minutes": interval_minutes,
            "max_tasks_per_cycle": 1,
            "heartbeat_timeout_minutes": 60,
        },
        "setup_time": datetime.now().isoformat(),
        "agent_path": selected.path,
    }
    
    if messaging_config:
        config["messaging"] = messaging_config
    config_path = os.path.join(workspace, "partner_config.json")
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    status_ok(f"配置已保存: {config_path}")

    # ── 保存 QQ 机器人独立配置 ──
    qq_cfg = messaging_config.get("qq", {})
    if qq_cfg.get("type") == "official":
        qq_cfg_path = os.path.join(workspace, "qq_config.json")
        with open(qq_cfg_path, "w", encoding="utf-8") as f:
            json.dump({
                "app_id": qq_cfg["app_id"],
                "app_secret": qq_cfg["app_secret"],
                "is_sandbox": qq_cfg.get("is_sandbox", False),
                "auto_reconnect": True,
            }, f, indent=2, ensure_ascii=False)
        status_ok(f"QQ 机器人配置已写入: {qq_cfg_path}")

    # ── 安装 QQ 依赖 ──
    if qq_cfg.get("type") == "official":
        section("安装 QQ 依赖", "📦")
        _ensure_qq_dependencies()
    
    # ── 自动后台启动机器人 ──
    if qq_cfg.get("type") == "official":
        auto_start = prompt_choice("是否现在后台启动 QQ 机器人？", [
            "启动（推荐）",
            "稍后手动启动"
        ], default=0)
        if auto_start == 0:
            setup_path = os.path.dirname(os.path.abspath(__file__))
            partner_pkg = os.path.join(os.path.dirname(setup_path))

            if qq_cfg.get("type") == "official":
                import subprocess
                qq_log = os.path.join(workspace, "logs", "qq_bot.log")
                # Escape backslashes in paths for -c string (Windows)
                _pp = partner_pkg.replace("\\", "/")
                _ws = workspace.replace("\\", "/")
                cmd = [
                    sys.executable, "-c",
                    f"import sys; sys.path.insert(0, '{_pp}'); "
                    f"from partner.qq_official_bridge import QQQfficialBridge; "
                    f"b = QQQfficialBridge('{_ws}'); "
                    f"b.load_config_from_file('{qq_cfg_path}'.replace('\\\\','/')); "
                    f"b.start()"
                ]
                proc = subprocess.Popen(
                    cmd, stdout=open(qq_log, "w"), stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
                pid_path = os.path.join(workspace, "state", "qq_bot.pid")
                with open(pid_path, "w") as f:
                    f.write(str(proc.pid))
                status_ok(f"QQ 机器人已后台启动 (PID: {proc.pid})")
    else:
        status_info("未配置 QQ 机器人，跳过自动启动")
    
    # Save pointer for easy discovery
    save_workspace_pointer(workspace)
    section("设置自动研究", "⏰")
    
    if selected.name == "hermes":
        setup_cron_hermes(workspace)
        # Trigger immediate first run so Partner starts working now
        try:
            import subprocess as _sp
            _sp.run(
                ["hermes", "cron", "run", "partner-research-cycle", "--accept-hooks"],
                capture_output=True, timeout=120,
            )
            status_ok("✅ 首次研究周期已触发，Partner 开始后台工作！")
        except Exception:
            status_info("ℹ Cron 将在下一分钟自动运行")
    
    # Daily workspace organization
    setup_workspace_cron(workspace)
    
    # ── Done ──
    print()
    line("━", 50, C.GREEN)
    print(f"\n  {C.BOLD}{C.GREEN}🎉 Partner Setup Complete!{C.RESET}\n")
    print(f"  {C.BOLD}Usage:{C.RESET}")
    print(f"    1. Open {selected.emoji} {selected.display_name}")
    print(f"    2. Say: {C.CYAN}'partner, what have you been doing?'{C.RESET}")
    print(f"    3. Or: {C.CYAN}'partner, research XXX'{C.RESET}")
    print(f"    4. Partner will run autonomously in the background\n")
    print(f"  {C.BOLD}Commands:{C.RESET}")
    print(f"    {C.DIM}partner status       Check Partner status{C.RESET}")
    print(f"    {C.DIM}partner setup        Reconfigure{C.RESET}")
    print(f"    {C.DIM}partner bot start qq Start QQ bot{C.RESET}")
    print(f"    {C.DIM}partner bot stop qq  Stop QQ bot{C.RESET}")
    print(f"    {C.DIM}partner update       Update to latest version{C.RESET}")
    print(f"    {C.DIM}partner queue clear  Clear task queue{C.RESET}")
    print()


# ── Status Check ─────────────────────────────────────────────

def show_status(workspace=None):
    """Show Partner status with nice formatting."""
    banner()
    
    if not workspace:
        workspace = find_workspace()
    
    if not workspace:
        status_warn("Partner 未配置")
        status_info("运行 'partner setup' 开始配置")
        return
    
    config_path = os.path.join(workspace, "partner_config.json")
    if not os.path.exists(config_path):
        status_warn(f"未找到配置: {config_path}")
        return
    
    with open(config_path) as f:
        config = json.load(f)
    
    section("配置信息", "⚙️")
    ws_cfg = config.get("workspace", {})
    ws_path = ws_cfg.get("path", workspace) if isinstance(ws_cfg, dict) else workspace
    agent_cfg = config.get("agent", {})
    backend = agent_cfg.get("backend", config.get("backend", "unknown"))
    status_info(f"工作区: {ws_path}")
    status_info(f"后端: {backend}")
    
    section("研究统计", "📊")

    state_dir = os.path.join(workspace, "state")
    
    stats_path = os.path.join(state_dir, "stats.json")
    if os.path.exists(stats_path):
        with open(stats_path) as f:
            stats = json.load(f)
        print(f"    ⏱  研究周期: {C.BOLD}{stats.get('total_cycles', 0)}{C.RESET}")
        print(f"    📋 完成任务: {C.BOLD}{stats.get('total_tasks_completed', 0)}{C.RESET}")
    
    kb_path = os.path.join(state_dir, "knowledge.json")
    if os.path.exists(kb_path):
        with open(kb_path) as f:
            kb = json.load(f)
        entries = kb.get("entries", []) if isinstance(kb, dict) else kb
        print(f"    📚 知识条目: {C.BOLD}{len(entries)}{C.RESET}")
    
    tq_path = os.path.join(state_dir, "task_queue.json")
    if os.path.exists(tq_path):
        with open(tq_path) as f:
            tasks = json.load(f)
        pending = sum(1 for t in tasks if t.get("status") == "pending")
        print(f"    ⏳ 待执行:   {C.BOLD}{pending}{C.RESET}")

    # ── Active plan (new heartbeat model) ──
    plan_path = os.path.join(state_dir, "active_plan.json")
    if os.path.exists(plan_path):
        with open(plan_path) as f:
            plan = json.load(f)
        status_map = {"idle": "空闲", "planning": "规划中", "active": "执行中",
                      "completed": "已完成", "paused": "已暂停"}
        raw_status = plan.get("status", "idle")
        display_status = status_map.get(raw_status, raw_status)
        hb = plan.get("last_heartbeat", "")
        summary = plan.get("heartbeat_summary", "")
        print(f"    💓 心跳:     {hb[:16] if hb else '未知'}")
        print(f"    📶 状态:     {C.BOLD}{display_status}{C.RESET}")
        if summary:
            print(f"    📝 摘要:     {summary[:50]}")
    else:
        # Fallback to old heartbeat.json
        hb_path = os.path.join(state_dir, "heartbeat.json")
        if os.path.exists(hb_path):
            with open(hb_path) as f:
                hb = json.load(f)
            print(f"    💓 最后心跳: {hb.get('last_heartbeat', 'unknown')[:16]}")
            s = hb.get('status', 'unknown')
            print(f"    📶 状态:     {C.BOLD}{s}{C.RESET}")

    # ── Interval ──
    interval = config.get("scheduler", {}).get("interval_minutes", 15)
    print(f"    ⏰ 间隔:     {C.BOLD}每 {interval} 分钟{C.RESET}")
    print(f"    📌 修改:     {C.DIM}partner config set interval N{C.RESET}")
    
    section("使用方法", "💡")
    backend = config.get('backend', 'hermes')
    if backend == 'hermes':
        print(f"    打开 Hermes，说：{C.CYAN}'partner 最近在研究什么？'{C.RESET}")
    else:
        print(f"    在 {backend} 中说：{C.CYAN}'partner 最近在研究什么？'{C.RESET}")
    
    # ── 机器人状态 ──
    messaging = config.get("messaging", {})
    
    qq_config_path = os.path.join(workspace, "qq_config.json")
    has_qq_bot = bool(messaging.get("qq")) or os.path.exists(qq_config_path)
    
    if has_qq_bot:
        section("机器人状态", "🤖")
        for platform, label, cfg_path in [("qq", "QQ", qq_config_path)]:
            cfg = messaging.get(platform, {})
            if not cfg and not os.path.exists(cfg_path):
                continue
            pid_path = os.path.join(state_dir, f"{platform}_bot.pid")
            running = False
            if os.path.exists(pid_path):
                try:
                    with open(pid_path) as f:
                        pid = int(f.read().strip())
                    try:
                        os.kill(pid, 0)
                        running = True
                    except OSError:
                        running = False
                except (ValueError, OSError):
                    running = False
            if running:
                print(f"    {C.GREEN}●{C.RESET} {label} 机器人: 运行中 (PID: {pid})")
                print(f"      停止: partner bot stop {platform}")
                log_path = os.path.join(workspace, "logs", f"{platform}_bot.log")
                if os.path.exists(log_path):
                    print(f"      日志: {log_path}")
            else:
                print(f"    {C.DIM}○{C.RESET} {label} 机器人: 已配置但未运行")
                log_path = os.path.join(workspace, "logs", f"{platform}_bot.log")
                if os.path.exists(log_path):
                    print(f"      日志: {log_path}")
                if os.path.exists(cfg_path):
                    print(f"      启动: partner bot start {platform}")

    # ── Commands ──
    print()
    line("─", 48, C.DIM)
    print(f"  {C.BOLD}Commands:{C.RESET}")
    print(f"    {C.DIM}partner status       Check Partner status{C.RESET}")
    print(f"    {C.DIM}partner setup        Reconfigure{C.RESET}")
    print(f"    {C.DIM}partner bot start qq Start QQ bot{C.RESET}")
    print(f"    {C.DIM}partner bot stop qq  Stop QQ bot{C.RESET}")
    print(f"    {C.DIM}partner update       Update to latest version{C.RESET}")
    print(f"    {C.DIM}partner queue clear  Clear task queue{C.RESET}")

    print()


def find_workspace():
    """Find Partner workspace."""
    # 1. Environment variable
    ws = os.environ.get("PARTNER_WORKSPACE")
    if ws and os.path.exists(ws):
        return ws
    
    # 2. Check pointers and repo directory
    partner_home = os.path.expanduser("~/.partner")
    pointer_file = os.path.expanduser("~/.partner_workspace")

    # 2a. Pointer file ~/.partner_workspace (new, avoids colliding with repo dir)
    if os.path.isfile(pointer_file):
        try:
            with open(pointer_file) as f:
                path = f.read().strip()
            if path and os.path.exists(os.path.join(path, "partner_config.json")):
                return path
        except OSError:
            pass

    # 2b. ~/.partner — could be a pointer file (old)
    if os.path.isfile(partner_home):
        try:
            with open(partner_home) as f:
                path = f.read().strip()
            if path and os.path.exists(os.path.join(path, "partner_config.json")):
                return path
        except OSError:
            pass

    # 2c. ~/.partner is the repo directory — check for config inside
    if os.path.isdir(partner_home):
        config_in_home = os.path.join(partner_home, "partner_config.json")
        if os.path.exists(config_in_home):
            return partner_home
    
    # 3. Common locations
    candidates = [
        os.path.expanduser("~/partner_workspace"),
        os.path.expanduser("~/.partner_workspace"),
    ]
    for c in candidates:
        if os.path.exists(os.path.join(c, "partner_config.json")):
            return c

    # 4. Partner app directory itself (has config.json and partner/__init__.py)
    partner_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isfile(os.path.join(partner_dir, "partner", "__init__.py")):
        return partner_dir
    cfg_in_partner = os.path.join(partner_dir, "config.json")
    if os.path.exists(cfg_in_partner):
        try:
            with open(cfg_in_partner) as f:
                data = json.load(f)
            ws = data.get("workspace", "")
            if ws and os.path.isfile(os.path.join(ws, "partner", "__init__.py")):
                return ws
        except Exception:
            pass

    return None


def save_workspace_pointer(workspace: str):
    """Save workspace path to ~/.partner_workspace for easy discovery."""
    pointer = os.path.expanduser("~/.partner_workspace")
    with open(pointer, 'w') as f:
        f.write(workspace)

"""Partner Setup - beautiful interactive configuration wizard."""

import json
import os
# Force UTF-8 for subprocess pipes (prevents GBK errors on Chinese Windows)
os.environ.setdefault("PYTHONUTF8", "1")
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import i18n
from .config import (
    apply_runtime_agent_defaults,
    load_partner_config_data,
    resolve_partner_config_path,
    save_partner_config_data,
    workspace_has_partner_config,
)
from .instance_root import resolve_global_config_path, resolve_instance_workspace, resolve_partner_root
from .workspace_layout import ensure_instance_layout

# Windows: suppress console windows for subprocess calls
_NTFLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


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
    print(f"  {C.BOLD}{C.CYAN}🤝 Partner{C.RESET}")
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


def _is_zh() -> bool:
    return i18n.lang() != "en"


def _txt(zh: str, en: str) -> str:
    return zh if _is_zh() else en


def _prompt_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    try:
        answer = input(f"  {C.BOLD}{prompt}{C.RESET} [{suffix}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default
    if not answer:
        return default
    return answer in {"y", "yes", "1", "true", "是", "好", "安装", "确认"}


def _choose_language():
    current = i18n.lang()
    print()
    print(f"  {C.BOLD}{_txt('请选择语言 / Choose Language', 'Choose Language / 请选择语言')}{C.RESET}")
    print(f"    1. 中文")
    print(f"    2. English")
    prompt = "  选择 [1/2, 回车保持当前]: " if _is_zh() else "  Choose [1/2, Enter to keep current]: "
    try:
        answer = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer == "1":
        i18n.set_lang("zh")
    elif answer == "2":
        i18n.set_lang("en")
    elif current not in {"zh", "en"}:
        i18n.set_lang("zh")
    current_label = "中文" if i18n.lang() == "zh" else "English"
    print(f"    {C.GREEN}▶{C.RESET} {_txt('当前语言', 'Current language')}: {current_label}")


def _fmt_ts_short(value: str) -> str:
    if not value:
        return "未知"
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text[:16]


def _minutes_since(value: str):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        return max(0, int((now - dt).total_seconds() // 60))
    except ValueError:
        return None


def _health_label(minutes_since: int | None, interval: int) -> str:
    if minutes_since is None:
        return f"{C.YELLOW}未知{C.RESET}"
    if minutes_since <= max(interval * 2, 10):
        return f"{C.GREEN}正常{C.RESET}"
    if minutes_since <= max(interval * 6, 30):
        return f"{C.YELLOW}偏久未更新{C.RESET}"
    return f"{C.RED}可能已停滞{C.RESET}"

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
                result = subprocess.run([cmd, "hermes"], capture_output=True, text=True, timeout=3, encoding="utf-8", errors="replace", creationflags=_NTFLAGS)
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


def detect_openclaw() -> AgentInfo:
    """Detect OpenClaw installation."""
    try:
        from .openclaw_adapter import OpenClawAdapter

        info = OpenClawAdapter.detect_installation()
        return AgentInfo(
            "openclaw",
            "OpenClaw",
            "🦞",
            bool(info.get("available")),
            path=info.get("executable") or info.get("path"),
            version=info.get("version"),
            config_path=info.get("config_path"),
        )
    except Exception:
        pass
    return AgentInfo("openclaw", "OpenClaw", "🦞", False)


def detect_codex() -> AgentInfo:
    """Detect OpenAI Codex CLI installation."""
    import os as _os
    import shutil

    home = Path.home()
    appdata = Path(_os.environ.get("APPDATA", ""))
    localappdata = Path(_os.environ.get("LOCALAPPDATA", ""))
    codex_bin = shutil.which("codex")
    if not codex_bin:
        candidates = [
            appdata / "npm" / "codex.cmd",
            appdata / "npm" / "codex.ps1",
            appdata / "npm" / "codex",
            localappdata / "Programs" / "Codex" / "codex.exe",
            home / ".local" / "bin" / "codex",
            home / ".npm-global" / "bin" / "codex",
            Path("/usr/local/bin/codex"),
            Path("/usr/bin/codex"),
        ]
        for candidate in candidates:
            if candidate.exists():
                codex_bin = str(candidate)
                break

    if not codex_bin:
        for cmd in ["which", "where"]:
            try:
                result = subprocess.run(
                    [cmd, "codex"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=_NTFLAGS,
                )
                if result.returncode == 0:
                    codex_bin = result.stdout.strip().split("\n")[0].strip()
                    break
            except Exception:
                pass

    if not codex_bin:
        return AgentInfo("codex", "OpenAI Codex", "⌘", False)

    version = None
    try:
        result = subprocess.run(
            [codex_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
            creationflags=_NTFLAGS,
        )
        version = (result.stdout or result.stderr or "").strip().splitlines()[0].strip() or None
    except Exception:
        version = None

    config_candidates = [
        home / ".codex" / "config.toml",
        Path(_os.environ.get("USERPROFILE", "")) / ".codex" / "config.toml",
        localappdata / "codex" / "config.toml",
    ]
    config_path = next((str(p) for p in config_candidates if p.exists()), None)
    return AgentInfo(
        name="codex",
        display_name="OpenAI Codex",
        emoji="⌘",
        available=True,
        path=codex_bin,
        version=version,
        config_path=config_path,
    )


SUPPORTED_SETUP_AGENTS = ("hermes", "codex", "openclaw")


def _install_command_for_agent(agent_name: str) -> list[str] | None:
    if agent_name == "hermes":
        return [sys.executable, "-m", "pip", "install", "-U", "hermes-agent"]
    if agent_name == "openclaw":
        npm = shutil.which("npm")
        if not npm:
            return None
        return [npm, "install", "-g", "openclaw"]
    if agent_name == "codex":
        npm = shutil.which("npm")
        if not npm:
            return None
        return [npm, "install", "-g", "@openai/codex"]
    return None


def _detect_supported_setup_agents() -> list[AgentInfo]:
    return [detect_hermes(), detect_codex(), detect_openclaw()]


def _detect_supported_setup_agent(agent_name: str) -> AgentInfo:
    if agent_name == "hermes":
        return detect_hermes()
    if agent_name == "openclaw":
        return detect_openclaw()
    if agent_name == "codex":
        return detect_codex()
    return AgentInfo(agent_name, agent_name, "?", False)


def _offer_install_supported_agents(agents: list[AgentInfo], quick: bool = False) -> list[AgentInfo]:
    """Offer installation only for the currently supported setup backends."""
    if quick:
        return agents

    updated = []
    for agent in agents:
        if agent.available:
            updated.append(agent)
            continue

        should_install = _prompt_yes_no(
            _txt(
                f"未检测到 {agent.display_name}，是否现在尝试安装？",
                f"{agent.display_name} was not detected. Install it now?",
            ),
            default=False,
        )
        if not should_install:
            updated.append(agent)
            continue

        cmd = _install_command_for_agent(agent.name)
        if not cmd:
            status_warn(
                _txt(
                    f"无法自动安装 {agent.display_name}：未找到必要的安装工具",
                    f"Cannot auto-install {agent.display_name}: required installer was not found",
                )
            )
            updated.append(agent)
            continue

        status_info(_txt(f"正在安装 {agent.display_name}...", f"Installing {agent.display_name}..."))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                encoding="utf-8",
                errors="replace",
                creationflags=_NTFLAGS,
            )
            if result.returncode == 0:
                refreshed = _detect_supported_setup_agent(agent.name)
                if refreshed.available:
                    status_ok(_txt(f"{agent.display_name} 安装并检测成功", f"{agent.display_name} installed and detected"))
                    updated.append(refreshed)
                else:
                    status_warn(_txt(f"{agent.display_name} 安装命令已完成，但仍未在 PATH 中检测到", f"{agent.display_name} install finished, but it was still not detected in PATH"))
                    updated.append(agent)
            else:
                stderr = (result.stderr or result.stdout or "").strip().splitlines()
                detail = stderr[-1] if stderr else _txt("安装命令失败", "Install command failed")
                status_warn(f"{agent.display_name}: {detail}")
                updated.append(agent)
        except Exception as exc:
            status_warn(f"{agent.display_name}: {exc}")
            updated.append(agent)
    return updated


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
            capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace"
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
version: 0.7.0
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

**你不需要考虑 Partner 能不能做、怎么做。** 那是 Partner 运行时的事。Partner 有一个持续运行的 mind loop，会连续消费用户事件和项目队列；自脉冲/cron 只负责恢复检查、心跳和必要的通信补偿，不代表“每隔固定时间才研究一次”。它会：
1. 读取 mind_pool / task_queue
2. 判断消息属于项目指令、参考材料、纠偏、暂停还是普通学习
3. 把需要推进的内容交给执行引擎，并把结果记录到 workspace

**你和 Partner 执行引擎是两个不同的会话。你只管传话，执行引擎只管推进。**

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
        result = subprocess.run(["hermes", "gateway", "status"], capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace", creationflags=_NTFLAGS)
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
            subprocess.run(["hermes", "gateway", "install"], capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace", creationflags=_NTFLAGS)
            result = subprocess.run(["hermes", "gateway", "start"], capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace", creationflags=_NTFLAGS)
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
        result = subprocess.run(["hermes", "cron", "list"], capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace", creationflags=_NTFLAGS)
        if "partner" in result.stdout.lower() or "autonomous-researcher" in result.stdout.lower():
            status_ok("Cron job 已存在，跳过创建")
            # Extract job ID and save to config
            for line in result.stdout.split("\n"):
                if "[" in line and "active" in line:
                    job_id = line.split("[")[0].strip()
                    status_info(f"Job ID: {job_id}")
                    try:
                        if workspace_has_partner_config(workspace):
                            cfg = load_partner_config_data(workspace)
                            if 'scheduler' not in cfg:
                                cfg['scheduler'] = {}
                            cfg['scheduler']['cron_job_id'] = job_id
                            cfg['scheduler']['cron_job_name'] = 'partner-research-cycle'
                            save_partner_config_data(workspace, cfg)
                            status_ok(f"Cron job ID 已保存: {job_id}")
                    except Exception as e:
                        status_warn(f"无法保存 cron job ID: {e}")
                    return
    except:
        pass
    
    # Create heartbeat job.
    # This interval is only a health/recovery pulse, not research cadence.
    _interval = 15
    try:
        if workspace_has_partner_config(workspace):
            _cfg = load_partner_config_data(workspace)
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
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace"
        )
        if result.returncode == 0:
            status_ok(f"Cron job 已自动创建 (每 {_interval} 分钟)")
            # Extract and save cron job ID to partner_config.json
            import re
            match = re.search(r'\[([a-f0-9-]+)\]', result.stdout)
            cron_job_id = match.group(1) if match else 'partner-research-cycle'
            try:
                if workspace_has_partner_config(workspace):
                    cfg = load_partner_config_data(workspace)
                    if 'scheduler' not in cfg:
                        cfg['scheduler'] = {}
                    cfg['scheduler']['cron_job_id'] = cron_job_id
                    cfg['scheduler']['cron_job_name'] = 'partner-research-cycle'
                    save_partner_config_data(workspace, cfg)
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
            capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace",
        )
        if cron_name not in result.stdout:
            _subprocess.run(
                ["hermes", "cron", "create",
                 "--name", cron_name,
                 "--schedule", "0 4 * * *",  # Daily at 4am
                 "--prompt", f"运行 workspace 维护脚本: python3 {cron_script}",
                 "--workdir", workspace,
                 ],
                capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace",
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
                capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace"
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
            capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace"
        )
        if result.returncode == 0:
            status_ok("通过 partner-research[qq-official] 安装成功")
            return
        status_fail(f"替代安装也失败: {result.stderr[:200]}")
    except Exception:
        pass
    status_info("请手动运行:")
    status_info(f"  pip install {' '.join(needed)}")


def _load_json_if_exists(path: str) -> dict:
    try:
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _discover_existing_setup_context() -> dict:
    """Prefer the current multi-instance workspace and default instance as setup defaults."""
    partner_root = str(resolve_partner_root())
    ctx = {
        "partner_root": partner_root,
        "default_instance_id": "",
        "instance_workspace": "",
        "workspace_config": {},
        "instance_config": {},
        "qq_config": {},
        "wechat_config": {},
        "global_config": {},
    }

    global_cfg = _load_json_if_exists(str(resolve_global_config_path()))
    ctx["global_config"] = global_cfg
    default_instance_id = (global_cfg.get("default_instance") or "").strip()
    if not default_instance_id:
        instances = global_cfg.get("instances", {})
        if isinstance(instances, dict) and instances:
            default_instance_id = next(iter(instances.keys()))
    ctx["default_instance_id"] = default_instance_id

    if default_instance_id:
        inst_ws = str(resolve_instance_workspace(default_instance_id))
        ctx["instance_workspace"] = inst_ws
        ctx["instance_config"] = _load_json_if_exists(resolve_partner_config_path(inst_ws))
        ctx["qq_config"] = (
            _load_json_if_exists(os.path.join(inst_ws, "config", "qq_config.json"))
            or _load_json_if_exists(os.path.join(inst_ws, "qq_config.json"))
        )
        ctx["wechat_config"] = (
            _load_json_if_exists(os.path.join(inst_ws, "config", "wechat_config.json"))
            or _load_json_if_exists(os.path.join(inst_ws, "wechat_config.json"))
        )

    if not ctx["qq_config"]:
        instances = global_cfg.get("instances", {})
        if isinstance(instances, dict):
            for instance_id in instances:
                inst_ws = str(resolve_instance_workspace(instance_id))
                qq_cfg = (
                    _load_json_if_exists(os.path.join(inst_ws, "config", "qq_config.json"))
                    or _load_json_if_exists(os.path.join(inst_ws, "qq_config.json"))
                )
                if qq_cfg:
                    ctx["qq_config"] = qq_cfg
                    break

    if workspace_has_partner_config(partner_root):
        ctx["workspace_config"] = _load_json_if_exists(resolve_partner_config_path(partner_root))

    return ctx


def _build_agent_config_for_setup(selected_agent: AgentInfo, existing_agent: dict | None = None) -> dict:
    """Build a clean agent config for the selected backend."""
    existing_agent = existing_agent if isinstance(existing_agent, dict) else {}
    if selected_agent.name == "hermes":
        return apply_runtime_agent_defaults({
            "backend": "hermes",
            "model": existing_agent.get("model"),
            "provider": existing_agent.get("provider"),
        })

    return {
        "backend": selected_agent.name,
        "model": None,
        "provider": None,
        "classifier_backend": selected_agent.name,
        "classifier_model": None,
        "classifier_provider": None,
    }


def _split_csv(value: str, default: str = "") -> list[str]:
    text = value if value is not None else default
    return [x.strip() for x in str(text or "").split(",") if x.strip()]


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _prompt_server_config(existing_servers: dict | None = None) -> dict:
    """Optional global server registry used by CLI/GUI and Ollama hints."""
    existing_servers = existing_servers if isinstance(existing_servers, dict) else {}
    if not _prompt_yes_no(
        _txt(
            "是否配置服务器连接？可记录多台腾讯云/实验室服务器，供 GUI、运维和 Ollama 隧道提示使用。",
            "Configure server connections? This stores multiple cloud/lab servers for GUI, ops, and Ollama tunnel hints.",
        ),
        default=False,
    ):
        return existing_servers

    servers = dict(existing_servers)
    while True:
        name = prompt_input(_txt("服务器名称", "Server name"), f"server{len(servers)+1}")
        host = prompt_input(_txt("SSH Host / IP", "SSH Host / IP"), "")
        if not name or not host:
            status_warn(_txt("名称和 Host 不能为空，跳过本条", "Name and host are required; skipped"))
        else:
            old = servers.get(name) if isinstance(servers.get(name), dict) else {}
            user = prompt_input(_txt("SSH 用户", "SSH user"), old.get("user", "ubuntu"))
            port = prompt_input(_txt("SSH 端口", "SSH port"), str(old.get("port", 22)))
            key_path = prompt_input(_txt("密钥路径（可空）", "Key path (optional)"), old.get("key_path", ""))
            remote_workspace = prompt_input(
                _txt("远端 Partner workspace（可空）", "Remote Partner workspace (optional)"),
                old.get("workspace", ""),
            )
            servers[name] = {
                "name": name,
                "host": host,
                "user": user or "ubuntu",
                "port": _safe_int(port or 22, 22),
                "key_path": key_path,
                "workspace": remote_workspace,
                "enabled": True,
            }
            status_ok(_txt(f"服务器已保存: {name}", f"Server saved: {name}"))

        if not _prompt_yes_no(_txt("继续添加服务器？", "Add another server?"), default=False):
            break
    return servers


def _print_ollama_tunnel_hint(server: dict, remote_port: int = 11434, local_port: int = 11434):
    host = server.get("host") or "<server>"
    user = server.get("user") or "ubuntu"
    port = _safe_int(server.get("port") or 22, 22)
    key = server.get("key_path") or ""
    key_part = f" -i {key}" if key else ""
    port_part = f" -p {port}" if port != 22 else ""
    print()
    status_info(_txt(
        "如果想把本地电脑的 Ollama 提供给服务器使用，在本地电脑运行：",
        "To expose your local Ollama to the server, run this on your local computer:",
    ))
    print(f"    ssh -N -R {remote_port}:127.0.0.1:{local_port}{key_part}{port_part} {user}@{host}")
    status_info(_txt(
        f"然后在服务器实例里把 Ollama endpoint 配成: http://127.0.0.1:{remote_port}",
        f"Then configure the server instance Ollama endpoint as: http://127.0.0.1:{remote_port}",
    ))


def _configure_ollama_pool_for_workspace(workspace: str, existing_agent: dict | None = None,
                                         servers: dict | None = None, quick: bool = False) -> dict:
    """Interactive Ollama pool config for one workspace/instance."""
    existing_agent = existing_agent if isinstance(existing_agent, dict) else {}
    pool = existing_agent.get("ollama_pool") if isinstance(existing_agent.get("ollama_pool"), dict) else {}
    if quick:
        return pool

    section(_txt("Ollama 连接池", "Ollama Pool"), "🧠")
    status_info(_txt(
        "可以配置多个 Ollama endpoint：本机、服务器、SSH 隧道或自定义地址。不可用时会自动回退主后端。",
        "You can configure multiple Ollama endpoints: local, server, SSH tunnel, or custom. Partner falls back automatically.",
    ))
    if not _prompt_yes_no(_txt("是否现在配置 Ollama？", "Configure Ollama now?"), default=bool(pool.get("enabled"))):
        return pool

    mode = prompt_input(_txt("使用范围 off/lite/project/all", "Scope off/lite/project/all"), str(pool.get("mode") or "lite")).strip().lower()
    if mode not in {"off", "lite", "project", "all"}:
        mode = "lite"
    pool["enabled"] = mode != "off"
    pool["mode"] = mode
    pool.setdefault("probe_timeout_sec", 2)
    pool.setdefault("chat_timeout_sec", 30)
    pool.setdefault("max_input_chars", 4000)
    endpoints = pool.get("endpoints") if isinstance(pool.get("endpoints"), list) else []
    location_keys = ["local", "server", "tunnel", "custom"]

    while mode != "off":
        location = prompt_choice(
            _txt("Ollama 位置：", "Ollama location:"),
            [
                _txt("本机电脑 Ollama", "Local computer Ollama"),
                _txt("服务器 Ollama", "Server Ollama"),
                _txt("本机电脑通过 SSH 反向隧道给服务器用", "Local Ollama exposed to server via SSH reverse tunnel"),
                _txt("自定义地址", "Custom URL"),
                _txt("结束添加", "Finish"),
            ],
            default=0,
        )
        if location == 4:
            break
        default_url = "http://127.0.0.1:11434"
        default_name = f"ollama{len(endpoints)+1}"
        location_key = location_keys[location]
        server_name = ""
        if location == 1:
            server_names = list((servers or {}).keys())
            if server_names:
                print("  " + _txt("已配置服务器：", "Configured servers:") + ", ".join(server_names))
                server_name = prompt_input(_txt("关联服务器名称", "Linked server name"), server_names[0]).strip()
            default_name = "server_ollama"
            default_url = "http://127.0.0.1:11434"
        elif location == 2:
            server_items = [v for v in (servers or {}).values() if isinstance(v, dict)]
            if server_items:
                _print_ollama_tunnel_hint(server_items[0])
            server_names = list((servers or {}).keys())
            if server_names:
                server_name = prompt_input(_txt("隧道目标服务器名称", "Tunnel target server name"), server_names[0]).strip()
            default_name = "local_tunnel"
            default_url = "http://127.0.0.1:11434"
        elif location == 3:
            default_name = "custom"
            default_url = ""

        name = prompt_input(_txt("连接名称", "Connection name"), default_name)
        base_url = prompt_input(_txt("Ollama 地址", "Ollama URL"), default_url).rstrip("/")
        models = prompt_input(
            _txt("模型优先级，逗号分隔", "Model priority, comma-separated"),
            "qwen3:1.7b,qwen3:4b,qwen2.5:7b",
        )
        if not base_url:
            status_warn(_txt("Ollama 地址为空，跳过", "Ollama URL is empty; skipped"))
        else:
            endpoints = [e for e in endpoints if not (isinstance(e, dict) and e.get("name") == name)]
            endpoint = {
                "name": name or f"ollama{len(endpoints)+1}",
                "base_url": base_url,
                "models": _split_csv(models, "qwen3:1.7b,qwen3:4b,qwen2.5:7b"),
                "enabled": True,
                "location": location_key,
            }
            if server_name:
                endpoint["server"] = server_name
            endpoints.append(endpoint)
            status_ok(_txt(f"Ollama endpoint 已添加: {base_url}", f"Ollama endpoint added: {base_url}"))
        if not _prompt_yes_no(_txt("继续添加 Ollama endpoint？", "Add another Ollama endpoint?"), default=False):
            break

    pool["endpoints"] = endpoints
    return pool


def _sync_multi_instance_defaults(
    partner_root: str,
    selected_agent: AgentInfo,
    interval_minutes: int,
    root_config: dict | None = None,
):
    """Propagate global setup choices to all configured instances."""
    global_cfg_path = str(resolve_global_config_path())
    global_cfg = _load_json_if_exists(global_cfg_path)
    instances = global_cfg.get("instances", {})
    if not isinstance(instances, dict) or not instances:
        return 0

    updated = 0
    for instance_id, meta in instances.items():
        if not isinstance(meta, dict):
            continue
        inst_ws = str(resolve_instance_workspace(instance_id))
        if not os.path.isdir(inst_ws):
            continue
        inst_cfg = _load_json_if_exists(resolve_partner_config_path(inst_ws))
        if not inst_cfg:
            inst_cfg = {
                "name": "Partner",
                "workspace": {"path": inst_ws, "readonly_dirs": []},
                "agent": {},
                "scheduler": {},
            }
        inst_cfg.setdefault("workspace", {})
        inst_cfg["workspace"]["path"] = inst_ws
        if root_config:
            inst_cfg["workspace"]["readonly_dirs"] = (
                root_config.get("workspace", {}).get("readonly_dirs", [])
            )
            if root_config.get("messaging"):
                inst_cfg["messaging"] = root_config.get("messaging")
            inst_cfg["name"] = root_config.get("name", inst_cfg.get("name", "Partner"))
        inst_cfg["agent"] = _build_agent_config_for_setup(
            selected_agent,
            inst_cfg.get("agent", {}),
        )
        inst_cfg.setdefault("scheduler", {})
        inst_cfg["scheduler"]["interval_minutes"] = interval_minutes
        inst_cfg["scheduler"]["max_tasks_per_cycle"] = 1
        inst_cfg["scheduler"]["heartbeat_timeout_minutes"] = 60
        save_partner_config_data(inst_ws, inst_cfg)

        meta["agent_backend"] = selected_agent.name
        meta["interval_minutes"] = interval_minutes
        updated += 1

    if updated:
        with open(global_cfg_path, "w", encoding="utf-8") as f:
            json.dump(global_cfg, f, indent=2, ensure_ascii=False)
    return updated


def _default_workspace_for_setup(existing: dict, old_config: dict) -> str:
    """Return the workspace root shown by setup, never an instance dir in multi-instance mode."""
    partner_root = existing.get("partner_root") or str(resolve_partner_root())
    global_instances = (existing.get("global_config") or {}).get("instances", {})
    if isinstance(global_instances, dict) and global_instances:
        return partner_root

    workspace_cfg = old_config.get("workspace", {})
    if isinstance(workspace_cfg, dict) and workspace_cfg.get("path"):
        return workspace_cfg["path"]
    return partner_root


def _prompt_instance_id() -> str:
    prompt = _txt("实例 ID: ", "Instance ID: ")
    try:
        return input(f"  {prompt}").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def _prompt_qq_fields(existing: dict | None = None) -> dict:
    existing = existing or {}
    app_id = prompt_input(_txt("AppID", "AppID"), existing.get("app_id", ""))
    old_secret_display = "******" if existing.get("app_secret") else ""
    app_secret = prompt_input(_txt("AppSecret", "AppSecret"), old_secret_display)
    if app_secret == "******" and existing.get("app_secret"):
        app_secret = existing["app_secret"]
    sandbox_default = 0 if existing.get("is_sandbox", True) else 1
    sandbox = prompt_choice(
        _txt("环境？", "Environment?"),
        [
            _txt("沙箱环境（测试）", "Sandbox (test)"),
            _txt("正式环境", "Production"),
        ],
        default=sandbox_default,
    )
    return {
        "app_id": app_id,
        "app_secret": app_secret,
        "is_sandbox": sandbox == 0,
    }


def _write_instance_qq_config(instance_id: str, qq_config: dict):
    from . import manager
    cfg_path = manager.qq_config_path(instance_id)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({
            "app_id": qq_config["app_id"],
            "app_secret": qq_config["app_secret"],
            "is_sandbox": qq_config.get("is_sandbox", False),
            "auto_reconnect": True,
        }, f, indent=2, ensure_ascii=False)


def _register_instance_meta(instance_id: str):
    from . import manager
    ensure_instance_layout(str(resolve_instance_workspace(instance_id)))
    cfg = manager.load_global_config()
    cfg.setdefault("instances", {})
    cfg["instances"][instance_id] = {
        "enabled": True,
        "working_dir": str(resolve_instance_workspace(instance_id)),
        "qq_config": "00_config/qq_config.json",
        "agent_backend": "hermes",
        "interval_minutes": 30,
    }
    manager.save_global_config(cfg)


def _delete_instance(instance_id: str) -> bool:
    from . import manager
    inst_dir = resolve_instance_workspace(instance_id)
    if not inst_dir.exists():
        return False
    try:
        manager.stop_instance(instance_id)
    except Exception:
        pass
    shutil.rmtree(inst_dir, ignore_errors=True)
    cfg = manager.load_global_config()
    if isinstance(cfg.get("instances"), dict):
        cfg["instances"].pop(instance_id, None)
    if cfg.get("default_instance") == instance_id:
        cfg["default_instance"] = ""
    manager.save_global_config(cfg)
    return True


def manage_instances():
    from . import manager

    while True:
        print()
        section(_txt("实例管理", "Instance Management"), "🧩")
        _print_instance_list_localized()
        print()
        options = [
            _txt("创建实例", "Create instance"),
            _txt("删除实例", "Delete instance"),
            _txt("配置 QQ 机器人", "Configure QQ bot"),
            _txt("返回", "Back"),
        ]
        choice = prompt_choice(_txt("选择操作：", "Choose an action:"), options, default=0)
        if choice == 0:
            instance_id = _prompt_instance_id()
            if not instance_id:
                status_warn(_txt("实例 ID 不能为空", "Instance ID is required"))
                continue
            if manager.instance_exists(instance_id):
                status_warn(_txt(f"实例已存在: {instance_id}", f"Instance already exists: {instance_id}"))
                continue
            inst = resolve_instance_workspace(instance_id)
            inst.mkdir(parents=True, exist_ok=True)
            for sub in manager.INSTANCE_SUBDIRS:
                (inst / sub).mkdir(parents=True, exist_ok=True)
            _register_instance_meta(instance_id)
            status_ok(_txt(f"实例已创建: {instance_id}", f"Instance created: {instance_id}"))
            if prompt_choice(_txt("现在配置 QQ 机器人？", "Configure QQ bot now?"), [_txt("是", "Yes"), _txt("否", "No")], default=0) == 0:
                qq_config = _prompt_qq_fields()
                if qq_config.get("app_id") and qq_config.get("app_secret"):
                    _write_instance_qq_config(instance_id, qq_config)
                    status_ok(_txt("QQ 配置已保存", "QQ config saved"))
        elif choice == 1:
            instance_id = _prompt_instance_id()
            if not instance_id:
                continue
            confirm = prompt_choice(
                _txt(f"删除实例 {instance_id}？", f"Delete instance {instance_id}?"),
                [_txt("删除", "Delete"), _txt("取消", "Cancel")],
                default=1,
            )
            if confirm == 0:
                if _delete_instance(instance_id):
                    status_ok(_txt(f"实例已删除: {instance_id}", f"Instance deleted: {instance_id}"))
                else:
                    status_warn(_txt(f"未找到实例: {instance_id}", f"Instance not found: {instance_id}"))
        elif choice == 2:
            instance_id = _prompt_instance_id()
            if not instance_id or not manager.instance_exists(instance_id):
                status_warn(_txt("实例不存在", "Instance not found"))
                continue
            existing = {}
            qq_path = manager.qq_config_path(instance_id)
            if qq_path.exists():
                try:
                    with open(qq_path, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception:
                    existing = {}
            qq_config = _prompt_qq_fields(existing)
            if qq_config.get("app_id") and qq_config.get("app_secret"):
                _write_instance_qq_config(instance_id, qq_config)
                status_ok(_txt(f"{instance_id} 的 QQ 配置已保存", f"Saved QQ config for {instance_id}"))
        else:
            return


def _pick_agent_for_quick_setup(available: list, old_backend: str):
    if old_backend:
        for agent in available:
            if agent.name == old_backend:
                return agent
    preferred = ["hermes", "openclaw"]
    for name in preferred:
        for agent in available:
            if agent.name == name:
                return agent
    return available[0]


def _tail_lines(path: str, max_lines: int = 5) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = [line.rstrip() for line in f.readlines()]
        return [line for line in lines[-max_lines:] if line.strip()]
    except OSError:
        return []


def _find_recent_log_summary(workspace: str) -> dict:
    log_dirs = [
        os.path.join(workspace, "logs"),
        os.path.join(workspace, "state/record"),
    ]
    candidates = []
    for log_dir in log_dirs:
        if not os.path.isdir(log_dir):
            continue
        for name in os.listdir(log_dir):
            path = os.path.join(log_dir, name)
            if os.path.isfile(path):
                try:
                    candidates.append((os.path.getmtime(path), path))
                except OSError:
                    pass
    if not candidates:
        return {}

    candidates.sort(reverse=True)
    latest_path = candidates[0][1]
    lines = _tail_lines(latest_path, max_lines=5)
    error_line = ""
    for line in reversed(lines):
        if re.search(r"(traceback|error|exception|failed|crash|cannot|denied|timeout)", line, re.I):
            error_line = line.strip()
            break
    return {
        "path": latest_path,
        "lines": lines[-3:],
        "error_line": error_line,
    }


def _print_instance_list_localized():
    from . import manager
    instances = manager.list_instances()
    if not instances:
        print(f"    {_txt('还没有实例', 'No instances yet')}")
        return
    for instance_id, status in sorted(instances.items()):
        running_text = _txt("在运行", "Running") if status == manager.STATUS_RUNNING else _txt("未运行", "Not running")
        qq_paths = [
            os.path.join(str(resolve_instance_workspace(instance_id)), "config", "qq_config.json"),
            os.path.join(str(resolve_instance_workspace(instance_id)), "qq_config.json"),
        ]
        qq_text = _txt("已配置", "Configured") if any(os.path.exists(p) for p in qq_paths) else _txt("未配置", "Not configured")
        print(f"    {instance_id}  |  {_txt('状态', 'Status')}: {running_text}  |  QQ: {qq_text}")


def interactive_setup(quick: bool = False):
    """Main setup wizard."""
    _choose_language()
    banner()
    if quick:
        status_info("快速模式：使用默认值，尽量减少提问。后续可运行 partner setup 进入完整向导。")

    mode = prompt_choice(
        _txt("请选择要执行的操作：", "Choose what you want to do:"),
        [
            _txt("配置 Partner", "Configure Partner"),
            _txt("管理实例和 QQ 机器人", "Manage instances and QQ bots"),
        ],
        default=0,
    )
    if mode == 1:
        manage_instances()
        return

    # ── Load existing config ──
    existing = _discover_existing_setup_context()
    old_workspace = existing.get("partner_root") or find_workspace()
    old_config = existing.get("workspace_config") or existing.get("instance_config") or {}
    old_qq_cfg = existing.get("qq_config") or {}
    old_wx_cfg = existing.get("wechat_config") or {}
    if old_workspace:
        status_info(_txt(f"发现已有配置: {old_workspace}", f"Existing setup found: {old_workspace}"))
        status_info(_txt("将以上次配置为基础，可逐项修改", "Previous settings were detected and can be updated"))
    else:
        status_info(_txt("未发现已有配置，开始全新配置", "No existing setup found. Starting fresh"))

    # ── Step 1: Detect Agents ──
    section(_txt("检测已安装的 Agent", "Detect Installed Agents"), "🔍")
    
    agents = _detect_supported_setup_agents()
    agents = _offer_install_supported_agents(agents, quick=quick)
    
    available = [a for a in agents if a.available]
    unavailable = [a for a in agents if not a.available]
    
    for a in available:
        info = f"{C.DIM}{a.path}{C.RESET}" if a.path else ""
        status_ok(f"{a.emoji} {a.display_name}  {info}")
    
    for a in unavailable:
        status_fail(f"{a.emoji} {a.display_name}  {C.DIM}{_txt('未安装', 'Not installed')}{C.RESET}")
    
    if not available:
        print()
        status_warn(_txt("没有检测到已安装的 Agent", "No supported agent was detected"))
        status_info(_txt("当前安装向导仅支持 Hermes 和 OpenClaw，请先安装其中一个：", "The setup wizard currently supports only Hermes and OpenClaw. Please install one of them first:"))
        print(f"      • Hermes Agent: {C.UNDER}https://hermes-agent.nousresearch.com{C.RESET}")
        print(f"      • OpenClaw:     {C.UNDER}https://docs.openclaw.ai/cli{C.RESET}")
        print()
        return
    
    # ── Step 2: Select Agent ──
    section(_txt("选择 Agent 后端", "Choose Agent Backend"), "⚙️")

    old_backend = old_config.get("backend") or old_config.get("agent", {}).get("backend")
    old_agent_idx = 0
    if old_backend:
        for i, a in enumerate(available):
            if a.name == old_backend:
                old_agent_idx = i
                break

    if quick:
        selected = _pick_agent_for_quick_setup(available, old_backend)
        status_info(_txt(f"快速模式自动选择: {selected.emoji} {selected.display_name}", f"Quick mode auto-selected: {selected.emoji} {selected.display_name}"))
    elif len(available) == 1:
        selected = available[0]
        status_info(_txt(f"自动选择: {selected.emoji} {selected.display_name}", f"Auto-selected: {selected.emoji} {selected.display_name}"))
    else:
        options = [f"{a.emoji} {a.display_name}" for a in available]
        idx = prompt_choice(_txt("选择要使用的 Agent：", "Choose an agent:"), options, default=old_agent_idx)
        selected = available[idx]
    
    print(f"\n    {C.GREEN}▶{C.RESET} {_txt('使用', 'Using')} {C.BOLD}{selected.emoji} {selected.display_name}{C.RESET}")
    
    # ── Step 3: Agent Config ──
    section(_txt("Agent 配置", "Agent Configuration"), "🔧")
    
    if selected.config_path:
        status_info(_txt(f"配置文件: {selected.config_path}", f"Config file: {selected.config_path}"))
        if selected.version:
            status_info(_txt(f"默认模型: {selected.version}", f"Default model: {selected.version}"))
    else:
        status_warn(_txt("未找到配置文件", "Config file not found"))
    
    # ── Step 4: Workspace ──
    section("创建工作区", "📂")
    
    default_ws = _default_workspace_for_setup(existing, old_config)
    workspace = default_ws if quick else prompt_input("工作区路径", default_ws)
    workspace = os.path.expanduser(workspace)
    
    # Create workspace structure
    os.makedirs(workspace, exist_ok=True)
    ensure_instance_layout(workspace)
    for d in ["state", "logs"]:
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
    elif selected.name == "openclaw":
        status_ok("OpenClawAdapter 已启用，可在配置中作为 agent backend 使用")
    else:
        status_info(f"{selected.display_name} 集成即将推出")
    

    # ── Step 5b: WSL Bridge ──
    from .wsl_bridge import is_wsl, get_windows_drives, get_windows_user_dirs
    
    if is_wsl() and not quick:
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
                    
                    try:
                        choice = input(f"  {C.DIM}选择: {C.RESET}").strip()
                    except (EOFError, KeyboardInterrupt):
                        choice = ""
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


    # ── Internal pulse interval ──
    # This is not the project execution cadence. Partner keeps processing its
    # mind pool continuously; the interval only controls self-pulse/health
    # recovery checks, so setup should not ask ordinary users to tune it.
    old_interval = old_config.get("scheduler", {}).get("interval_minutes", 30)
    try:
        interval_minutes = int(old_interval)
    except Exception:
        interval_minutes = 30
    if interval_minutes <= 0:
        interval_minutes = 30

    # ── Step 6: QQ 官方机器人 ──
    messaging_config = {}

    has_qq = bool(old_qq_cfg.get("app_id"))
    if quick and has_qq:
        messaging_config["qq"] = {
            "type": "official",
            "app_id": old_qq_cfg["app_id"],
            "app_secret": old_qq_cfg["app_secret"],
            "is_sandbox": old_qq_cfg.get("is_sandbox", False),
        }
        status_ok(f"快速模式保留 QQ 配置: {old_qq_cfg['app_id']}")
    elif quick:
        status_info("快速模式跳过 QQ 配置。需要时可稍后运行 partner setup 补充。")
    elif has_qq:
        qq_prompt = f"修改 QQ 机器人配置？（当前: {old_qq_cfg['app_id']}）"
        qq_options = ["修改配置", "保持现有不变", "删除配置"]
    else:
        qq_prompt = "是否连接 QQ 官方机器人？"
        qq_options = ["连接（需要从 q.qq.com 获取 AppID + AppSecret）", "跳过"]

    qq_enable = prompt_choice(qq_prompt, qq_options, default=1 if has_qq else 1)

    if has_qq and qq_enable == 1:
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

    # ── Step 6d: Servers and Ollama ──
    try:
        from . import manager as _manager
        global_cfg_for_servers = _manager.load_global_config()
    except Exception:
        global_cfg_for_servers = {}
    servers_cfg = global_cfg_for_servers.get("servers") if isinstance(global_cfg_for_servers.get("servers"), dict) else {}
    if not quick:
        section(_txt("服务器与本地模型", "Servers and Local Models"), "🖥️")
        servers_cfg = _prompt_server_config(servers_cfg)
        if servers_cfg:
            global_cfg_for_servers["servers"] = servers_cfg
            try:
                from . import manager as _manager
                _manager.save_global_config(global_cfg_for_servers)
                status_ok(_txt("服务器配置已保存到 global_config.json", "Server config saved to global_config.json"))
            except Exception as exc:
                status_warn(_txt(f"服务器配置保存失败: {exc}", f"Failed to save server config: {exc}"))

    old_agent_config = old_config.get("agent", {}) if isinstance(old_config.get("agent"), dict) else {}
    ollama_pool_cfg = _configure_ollama_pool_for_workspace(
        workspace,
        existing_agent=old_agent_config,
        servers=servers_cfg,
        quick=quick,
    )

    # ── Step 7: Save Config ──
    agent_config = _build_agent_config_for_setup(selected, old_agent_config)
    if ollama_pool_cfg:
        agent_config["ollama_pool"] = ollama_pool_cfg
        agent_config["dynamic_ollama"] = {
            **(agent_config.get("dynamic_ollama") if isinstance(agent_config.get("dynamic_ollama"), dict) else {}),
            "enabled": bool(ollama_pool_cfg.get("enabled")) and str(ollama_pool_cfg.get("mode") or "") in {"project", "all"},
        }
    config = {
        "name": "Partner",
        "workspace": {
            "path": workspace,
            "readonly_dirs": readonly_dirs,
        },
        "agent": agent_config,
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
    config_path = resolve_partner_config_path(workspace, prefer_existing=False)
    save_partner_config_data(workspace, config)
    status_ok(f"配置已保存: {config_path}")

    synced_instances = _sync_multi_instance_defaults(
        partner_root=workspace,
        selected_agent=selected,
        interval_minutes=interval_minutes,
        root_config=config,
    )
    if synced_instances:
        status_ok(f"多实例默认设置已同步到 {synced_instances} 个实例")

    if ollama_pool_cfg:
        try:
            from . import manager as _manager
            global_cfg = _manager.load_global_config()
            instances = global_cfg.get("instances", {}) if isinstance(global_cfg.get("instances"), dict) else {}
            for instance_id in instances:
                inst_ws = str(resolve_instance_workspace(str(instance_id)))
                inst_cfg = _load_json_if_exists(resolve_partner_config_path(inst_ws))
                if not inst_cfg:
                    continue
                inst_agent = inst_cfg.get("agent") if isinstance(inst_cfg.get("agent"), dict) else {}
                inst_agent["ollama_pool"] = ollama_pool_cfg
                inst_agent["dynamic_ollama"] = {
                    **(inst_agent.get("dynamic_ollama") if isinstance(inst_agent.get("dynamic_ollama"), dict) else {}),
                    "enabled": bool(ollama_pool_cfg.get("enabled")) and str(ollama_pool_cfg.get("mode") or "") in {"project", "all"},
                }
                inst_cfg["agent"] = inst_agent
                save_partner_config_data(inst_ws, inst_cfg)
            if instances:
                status_ok(_txt("Ollama 配置已同步到所有实例", "Ollama config synced to all instances"))
        except Exception as exc:
            status_warn(_txt(f"Ollama 实例同步失败: {exc}", f"Failed to sync Ollama config to instances: {exc}"))

    # ── 保存 QQ 机器人独立配置 ──
    qq_cfg = messaging_config.get("qq", {})
    if qq_cfg.get("type") == "official":
        qq_cfg_path = os.path.join(workspace, "config", "qq_config.json")
        os.makedirs(os.path.dirname(qq_cfg_path), exist_ok=True)
        with open(qq_cfg_path, "w", encoding="utf-8") as f:
            json.dump({
                "app_id": qq_cfg["app_id"],
                "app_secret": qq_cfg["app_secret"],
                "is_sandbox": qq_cfg.get("is_sandbox", False),
                "auto_reconnect": True,
            }, f, indent=2, ensure_ascii=False)
        status_ok(f"QQ 机器人配置已写入: {qq_cfg_path}")
        try:
            from . import manager
            global_cfg = manager.load_global_config()
            instances = global_cfg.get("instances", {}) if isinstance(global_cfg.get("instances"), dict) else {}
            for instance_id in instances:
                _write_instance_qq_config(str(instance_id), qq_cfg)
        except Exception:
            pass

    # ── 安装 QQ 依赖 ──
    if qq_cfg.get("type") == "official":
        section("安装 QQ 依赖", "📦")
        _ensure_qq_dependencies()
    
    # ── 自动后台启动机器人 ──
    if qq_cfg.get("type") == "official" and not quick:
        auto_start = prompt_choice("是否现在后台启动 QQ 机器人？", [
            "启动（推荐）",
            "稍后手动启动"
        ], default=0)
        if auto_start == 0:
            setup_path = os.path.dirname(os.path.abspath(__file__))
            partner_pkg = os.path.join(os.path.dirname(setup_path))

            if qq_cfg.get("type") == "official":
                import subprocess
                try:
                    from . import manager
                    global_cfg = manager.load_global_config()
                    instances = global_cfg.get("instances", {}) if isinstance(global_cfg.get("instances"), dict) else {}
                    default_id = str(global_cfg.get("default_instance") or next(iter(instances.keys()), "01"))
                    runtime_workspace = str(resolve_instance_workspace(default_id))
                    if not os.path.isdir(runtime_workspace):
                        runtime_workspace = workspace
                except Exception:
                    default_id = "default"
                    runtime_workspace = workspace
                qq_log = os.path.join(runtime_workspace, "logs", "qq_bot.log")
                os.makedirs(os.path.dirname(qq_log), exist_ok=True)
                cmd = [
                    sys.executable, "-m", "partner",
                    "--instance-id", default_id,
                    "--workspace", runtime_workspace,
                ]
                env = os.environ.copy()
                env["PYTHONPATH"] = partner_pkg + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
                proc = subprocess.Popen(
                    cmd, stdout=open(qq_log, "w"), stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    env=env,
                )
                pid_path = os.path.join(runtime_workspace, "state", "qq_bot.pid")
                os.makedirs(os.path.dirname(pid_path), exist_ok=True)
                with open(pid_path, "w") as f:
                    f.write(str(proc.pid))
                try:
                    with open(os.path.join(runtime_workspace, "instance.pid"), "w") as f:
                        f.write(str(proc.pid))
                except OSError:
                    pass
                status_ok(f"QQ 机器人已后台启动 (PID: {proc.pid})")
    elif qq_cfg.get("type") == "official":
        status_info("快速模式保留 QQ 配置；打开 Partner GUI 后会自动确保实例可用。")
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
    print(f"\n  {C.BOLD}{C.GREEN}🎉 Partner {'Quick Setup' if quick else 'Setup'} Complete!{C.RESET}\n")
    print(f"  {C.BOLD}✅ 配置完成 / Setup Complete{C.RESET}\n")
    print(f"  {C.BOLD}下一步 / Next Steps:{C.RESET}")
    print(f"    {C.CYAN}partner gateway start{C.RESET}  {C.DIM}— 启动后台服务 (start background service){C.RESET}")
    print(f"    {C.CYAN}partner tui{C.RESET}             {C.DIM}— 进入交互终端 (enter interactive TUI){C.RESET}")
    print(f"    {C.CYAN}partner status{C.RESET}          {C.DIM}— 查看实例状态 (check instance status){C.RESET}")
    print()
    print(f"  {C.BOLD}Usage:{C.RESET}")
    print(f"    1. Open {selected.emoji} {selected.display_name}")
    print(f"    2. Say: {C.CYAN}'partner, what have you been doing?'{C.RESET}")
    print(f"    3. Or: {C.CYAN}'partner, research XXX'{C.RESET}")
    print(f"    4. Partner will run autonomously in the background\n")
    print(f"  {C.BOLD}Commands:{C.RESET}")
    print(f"    {C.DIM}partner{C.RESET}")
    print(f"    {C.DIM}partner setup{C.RESET}")
    print(f"    {C.DIM}partner status{C.RESET}")
    print(f"    {C.DIM}partner bot start qq{C.RESET}")
    print(f"    {C.DIM}partner bot stop qq{C.RESET}")
    print(f"    {C.DIM}partner update{C.RESET}")
    print(f"    {C.DIM}partner instance list{C.RESET}")
    print()


# ── Status Check ─────────────────────────────────────────────

def show_status(workspace=None):
    """Show Partner status with nice formatting."""
    banner()
    from . import manager

    root = resolve_partner_root()
    cfg = manager.load_global_config()
    instances = cfg.get("instances", {}) if isinstance(cfg.get("instances"), dict) else {}
    if not instances:
        if workspace and workspace_has_partner_config(workspace):
            instance_id = "02" if str(workspace).replace("\\", "/").rstrip("/").endswith("/partner_workspace") else Path(workspace).name
            state_dir = os.path.join(workspace, "state")
            pid_paths = [
                os.path.join(state_dir, "qq_bot.pid"),
                os.path.join(workspace, "instance.pid"),
            ]
            qq_paths = [
                os.path.join(workspace, "config", "qq_config.json"),
                os.path.join(workspace, "qq_config.json"),
            ]
            qq_configured = any(os.path.exists(path) for path in qq_paths)
            running = False
            pid_text = ""
            for pid_path in pid_paths:
                if not os.path.exists(pid_path):
                    continue
                try:
                    pid_text = Path(pid_path).read_text(encoding="utf-8").strip()
                    pid = int(pid_text or "0")
                except Exception:
                    pid = 0
                if not pid:
                    continue
                if os.name == "nt":
                    try:
                        result = subprocess.run(
                            ["tasklist.exe", "/FI", f"PID eq {pid}"],
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            timeout=5,
                            creationflags=_NTFLAGS,
                        )
                        running = str(pid) in (result.stdout or "")
                    except Exception:
                        running = False
                else:
                    try:
                        os.kill(pid, 0)
                        running = True
                    except OSError:
                        running = False
                if running:
                    break

            section(_txt("实例状态", "Instance Status"), "🧭")
            print(f"  {C.BOLD}{_txt('实例', 'Instance')} {instance_id}{C.RESET}")
            print(f"    {_txt('运行状态', 'Runtime')}: {_txt('在运行', 'Running') if running else _txt('未运行', 'Not running')}{f' (PID {pid_text})' if running and pid_text else ''}")
            print(f"    QQ: {_txt('已配置', 'Configured') if qq_configured else _txt('未配置', 'Not configured')}")
            print(f"    {_txt('工作区', 'Workspace')}: {workspace}")
            print()
            line("─", 48, C.DIM)
            print(f"  {C.BOLD}Commands:{C.RESET}")
            print(f"    {C.DIM}partner status --workspace \"{workspace}\"{C.RESET}")
            print(f"    {C.DIM}partner bot start qq --workspace \"{workspace}\"{C.RESET}")
            print(f"    {C.DIM}partner bot stop qq --workspace \"{workspace}\"{C.RESET}")
            print()
            return
        status_warn(_txt("还没有配置任何实例", "No instances are configured yet"))
        status_info(_txt("运行 partner setup 来创建和管理实例", "Run partner setup to create and manage instances"))
        return

    section(_txt("实例状态", "Instance Status"), "🧭")
    for instance_id in sorted(instances.keys()):
        instance_ws = str(resolve_instance_workspace(instance_id))
        state_dir = os.path.join(instance_ws, "state")
        plan_path = os.path.join(state_dir, "active_plan.json")
        heartbeat_path = os.path.join(state_dir, "heartbeat.json")
        stats_path = os.path.join(state_dir, "stats.json")
        qq_paths = [
            os.path.join(instance_ws, "config", "qq_config.json"),
            os.path.join(instance_ws, "qq_config.json"),
        ]
        qq_configured = any(os.path.exists(path) for path in qq_paths)
        runtime_status = manager.get_instance_status(instance_id)
        running_text = _txt("在运行", "Running") if runtime_status == manager.STATUS_RUNNING else _txt("未运行", "Not running")
        hb = ""
        summary = ""
        cycles = 0
        if os.path.exists(plan_path):
            try:
                with open(plan_path, "r", encoding="utf-8") as f:
                    plan = json.load(f)
                hb = plan.get("last_heartbeat", "")
                summary = plan.get("heartbeat_summary", "")
            except Exception:
                pass
        elif os.path.exists(heartbeat_path):
            try:
                with open(heartbeat_path, "r", encoding="utf-8") as f:
                    heartbeat = json.load(f)
                hb = heartbeat.get("last_heartbeat", "")
            except Exception:
                pass
        if os.path.exists(stats_path):
            try:
                with open(stats_path, "r", encoding="utf-8") as f:
                    stats = json.load(f)
                cycles = int(stats.get("total_cycles", 0))
            except Exception:
                pass

        print(f"  {C.BOLD}{_txt('实例', 'Instance')} {instance_id}{C.RESET}")
        print(f"    {_txt('运行状态', 'Runtime')}: {running_text}")
        print(f"    QQ: {_txt('已配置', 'Configured') if qq_configured else _txt('未配置', 'Not configured')}")
        print(f"    {_txt('工作区', 'Workspace')}: {instance_ws}")
        if hb:
            print(f"    {_txt('最近心跳', 'Last heartbeat')}: {_fmt_ts_short(hb)}")
        print(f"    {_txt('研究周期', 'Cycles')}: {cycles}")
        if summary:
            print(f"    {_txt('最近摘要', 'Recent summary')}: {summary[:100]}")
        recent_log = _find_recent_log_summary(instance_ws)
        if recent_log.get("error_line"):
            print(f"    {_txt('最近错误', 'Recent error')}: {recent_log['error_line'][:120]}")
        print()

    # ── Commands ──
    print()
    line("─", 48, C.DIM)
    print(f"  {C.BOLD}Commands:{C.RESET}")
    print(f"    {C.DIM}partner{C.RESET}")
    print(f"    {C.DIM}partner setup{C.RESET}")
    print(f"    {C.DIM}partner status{C.RESET}")
    print(f"    {C.DIM}partner bot start qq{C.RESET}")
    print(f"    {C.DIM}partner bot stop qq{C.RESET}")
    print(f"    {C.DIM}partner update{C.RESET}")
    print(f"    {C.DIM}partner instance list{C.RESET}")

    print()


def find_workspace():
    """Find Partner workspace."""
    # 1. Environment variable
    ws = os.environ.get("PARTNER_WORKSPACE")
    if ws and workspace_has_partner_config(ws):
        return ws

    root = str(resolve_partner_root())
    candidates = [
        root,
        os.path.expanduser("~/partner_workspace"),
        os.path.expanduser("~/.partner_workspace"),
        os.path.join(root, "instances", "default"),
    ]
    for candidate in candidates:
        if candidate and workspace_has_partner_config(candidate):
            return candidate

    return None


def save_workspace_pointer(workspace: str):
    """Save workspace path to ~/.partner_workspace for easy discovery."""
    pointer = os.path.expanduser("~/.partner_workspace")
    with open(pointer, 'w') as f:
        f.write(workspace)

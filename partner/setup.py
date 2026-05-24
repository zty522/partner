"""Partner Setup - beautiful interactive configuration wizard."""

import json
import os
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
    print(f"  {C.BOLD}{C.CYAN}🤝 Partner{C.RESET} {C.DIM}v0.1.0{C.RESET}")
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
    """Ask user to choose from options."""
    print(f"\n  {C.BOLD}{prompt}{C.RESET}")
    for i, opt in enumerate(options):
        marker = f"{C.CYAN}▶{C.RESET}" if i == default else f" {C.DIM}·{C.RESET}"
        print(f"    {marker} {i + 1}. {opt}")
    
    print()
    choice = input(f"  {C.DIM}选择 [{default + 1}]: {C.RESET}").strip()
    if not choice:
        return default
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(options):
            return idx
    except ValueError:
        pass
    return default


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
    home = Path.home()
    
    # Check 1: ~/.hermes/ directory
    hermes_dir = home / ".hermes"
    if not hermes_dir.exists():
        return AgentInfo("hermes", "Hermes Agent", "🔮", False)
    
    # Check 2: hermes binary
    candidates = [
        home / ".local" / "bin" / "hermes",
        hermes_dir / "hermes-agent" / "venv" / "bin" / "hermes",
        Path("/usr/local/bin/hermes"),
    ]
    
    hermes_bin = None
    for c in candidates:
        if c.exists():
            hermes_bin = str(c)
            break
    
    if not hermes_bin:
        # Try which
        try:
            result = subprocess.run(["which", "hermes"], capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                hermes_bin = result.stdout.strip()
        except:
            pass
    
    if not hermes_bin:
        return AgentInfo("hermes", "Hermes Agent", "🔮", False)
    
    # Check 3: config
    config_path = hermes_dir / "config.yaml"
    version = None
    if config_path.exists():
        try:
            with open(config_path) as f:
                content = f.read()
            # Try to extract model info
            for line in content.split("\n"):
                if "default:" in line and "model" not in line.lower():
                    version = line.split(":")[-1].strip()
                    break
        except:
            pass
    
    return AgentInfo(
        name="hermes",
        display_name="Hermes Agent",
        emoji="🔮",
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


# ── NapCat/QQ Detection ───────────────────────────────────────

def _try_ws_connect(ws_url: str, timeout: float = 3.0) -> dict:
    """Try a minimal TCP connection to check if NapCat is alive.

    Returns dict with keys: ok, latency_ms, error
    """
    result = {"ok": False, "latency_ms": 0, "error": ""}
    try:
        from urllib.parse import urlparse
        parsed = urlparse(ws_url if "://" in ws_url else f"ws://{ws_url}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 3001
        t0 = time.time()
        sock = socket.create_connection((host, int(port)), timeout=timeout)
        sock.close()
        result["ok"] = True
        result["latency_ms"] = round((time.time() - t0) * 1000)
    except Exception as e:
        result["error"] = str(e)
    return result


def detect_napcat() -> dict:
    """Detect NapCat QQ bot server.

    Returns dict: installed (bool), ws_url, latency_ms, error
    """
    info = {"installed": False, "ws_url": "", "latency_ms": 0, "error": ""}

    # Common NapCat WebSocket ports
    default_ports = [3001, 6700, 8080]

    for port in default_ports:
        ws_url = f"ws://127.0.0.1:{port}"
        probe = _try_ws_connect(ws_url, timeout=2.0)
        if probe["ok"]:
            info["installed"] = True
            info["ws_url"] = ws_url
            info["latency_ms"] = probe["latency_ms"]
            return info

    info["error"] = "未检测到 NapCat 服务（尝试了端口 3001, 6700, 8080）"
    return info


def test_napcat_connection(ws_url: str, access_token: str = "") -> dict:
    """Test NapCat connection and retrieve bot info via OneBot 11 HTTP API.

    Returns dict: ok, bot_id, bot_name, latency_ms, error
    """
    result = {"ok": False, "bot_id": "", "bot_name": "", "latency_ms": 0, "error": ""}

    # Try TCP connection first
    ws_probe = _try_ws_connect(ws_url, timeout=5.0)
    if not ws_probe["ok"]:
        result["error"] = f"无法连接到 {ws_url}: {ws_probe['error']}"
        return result
    result["latency_ms"] = ws_probe["latency_ms"]

    # Try HTTP API to get login info
    try:
        import urllib.request
        from urllib.parse import urlparse
        parsed = urlparse(ws_url if "://" in ws_url else f"ws://{ws_url}")
        http_base = f"http://{parsed.hostname}:{parsed.port}"

        headers = {"Content-Type": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        req = urllib.request.Request(
            f"{http_base}/get_login_info",
            headers=headers,
            method="GET",
        )
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode(), strict=False)
        if data.get("retcode") == 0:
            result["ok"] = True
            result["bot_id"] = str(data.get("data", {}).get("user_id", ""))
            result["bot_name"] = data.get("data", {}).get("nickname", "")
        else:
            result["ok"] = True
            result["bot_id"] = "unknown"
            result["bot_name"] = "(HTTP API 不可用，但连接正常)"
    except Exception:
        # WS works, HTTP doesn't - still OK
        result["ok"] = True
        result["bot_id"] = "unknown"
        result["bot_name"] = "(HTTP API 不可用，但连接正常)"

    return result


def setup_qq_config(workspace: str) -> dict:
    """Interactive QQ/NapCat configuration wizard.

    Returns dict with QQ config to merge into partner_config.json
    """
    section("QQ 集成配置 (NapCat)", "🐧")

    napcat = detect_napcat()
    if napcat["installed"]:
        status_ok(f"NapCat 服务已检测到  {napcat['ws_url']}  ({napcat['latency_ms']}ms)")
    else:
        status_warn(napcat["error"])
        status_info("请先安装并启动 NapCat: https://github.com/NapNeko/NapCatQQ")

    # ── WebSocket URL ──
    default_ws = napcat["ws_url"] if napcat["installed"] else "ws://127.0.0.1:3001"
    ws_url = prompt_input("NapCat WebSocket 地址", default_ws)
    if not ws_url.startswith(("ws://", "wss://")):
        ws_url = f"ws://{ws_url}"

    # ── Access Token ──
    access_token = prompt_input("Access Token (可选，直接回车跳过)", "")

    # ── Test Connection ──
    status_info("正在测试连接...")
    test_result = test_napcat_connection(ws_url, access_token)
    if test_result["ok"]:
        status_ok(f"连接成功! Bot: {test_result['bot_name']} (QQ: {test_result['bot_id']})  延迟: {test_result['latency_ms']}ms")
    else:
        status_fail(f"连接失败: {test_result['error']}")
        retry = prompt_choice("连接失败，是否仍要保存配置？", [
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

    # ── Voice ──
    voice_mode = prompt_choice("语音功能：", [
        "启用语音识别 + 文字回复（推荐）",
        "启用语音识别 + 语音回复",
        "禁用语音功能"
    ], default=0)
    voice_enabled = voice_mode in (0, 1)
    voice_reply = (voice_mode == 1)

    # ── Friend requests ──
    friend_mode = prompt_choice("好友请求处理：", [
        "手动审核（推荐）",
        "自动通过"
    ], default=0)
    auto_approve_friend = (friend_mode == 1)

    qq_config = {
        "enabled": True,
        "ws_url": ws_url,
        "access_token": access_token,
        "group_at_only": group_at_only,
        "voice_enabled": voice_enabled,
        "voice_reply": voice_reply,
        "auto_approve_friend": auto_approve_friend,
        "bot_id": test_result.get("bot_id", ""),
        "bot_name": test_result.get("bot_name", ""),
    }

    status_ok(f"QQ 配置已保存: ws={ws_url}, group_at_only={group_at_only}, voice={voice_enabled}")
    return qq_config


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
    with open(skill_path, 'w') as f:
        f.write(skill_content)
    
    return str(skill_path)


# ── Cron Setup ──────────────────────────────────────────────

def setup_cron_hermes(workspace: str):
    """Auto-create Hermes cron job for Partner."""
    import subprocess
    
    # Check if cron already exists
    try:
        result = subprocess.run(["hermes", "cron", "list"], capture_output=True, text=True, timeout=10)
        if "partner" in result.stdout.lower() or "autonomous-researcher" in result.stdout.lower():
            status_ok("Cron job 已存在，跳过创建")
            # Extract job ID
            for line in result.stdout.split("\n"):
                if "[" in line and "active" in line:
                    job_id = line.split("[")[0].strip()
                    status_info(f"Job ID: {job_id}")
                    return
    except:
        pass
    
    # Create cron job
    cron_prompt = f"""你是 Partner 的执行引擎。在 {workspace} 下工作。

执行步骤：
1. 用 execute_code 读取 {workspace}/state/task_queue.json，获取最高优先级的 pending 任务
2. 根据任务类型执行：literature_search 用 web_search，project_scan 用 read_file，其他用 web_search
3. 用 execute_code 更新状态：标记完成、添加知识、记录日志、生成新任务

⚠️ task_queue.json 中每个任务必须是字典对象，格式：
{{"id": "task_xxxxxxxx", "type": "deep_dive", "title": "任务标题", "description": "描述", "priority": 5, "status": "pending", "created_at": "ISO时间", "tags": []}}
绝对不要写入纯字符串！所有新任务必须包含以上字段。

只在 {workspace} 内写文件。用中文。"""
    
    try:
        # Try using hermes CLI to create cron
        result = subprocess.run(
            ["hermes", "cron", "create", 
             "--schedule", f"every {interval_minutes}m",
             "--name", "partner-research-cycle",
             "--prompt", cron_prompt],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            status_ok("Cron job 已自动创建 (每 30 分钟)")
        else:
            status_warn("Cron 自动创建失败，请手动设置")
            print(f"    {C.DIM}在 Hermes 中说：'设置 partner 的自动研究 cron'{C.RESET}")
    except Exception as e:
        status_warn(f"Cron 创建失败: {e}")
        print(f"    {C.DIM}在 Hermes 中说：'设置 partner 的自动研究 cron'{C.RESET}")


# ── Main Setup Flow ─────────────────────────────────────────



def detect_openclaw() -> AgentInfo:
    """Detect OpenClaw installation and gateway status."""
    import json as _json
    home = Path.home()
    config_dir = home / ".openclaw"
    
    if not config_dir.exists():
        return AgentInfo("openclaw", "OpenClaw (小龙虾)", "🦞", False)
    
    config_path = config_dir / "openclaw.json"
    config = {}
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = _json.load(f)
        except:
            pass
    
    # Check binary (may be in n-managed dir or npm-global)
    bin_path = None
    for candidate in [
        home / ".n" / "bin" / "openclaw",
        home / ".npm-global" / "bin" / "openclaw",
        Path("/usr/local/bin/openclaw"),
    ]:
        if candidate.exists():
            bin_path = str(candidate)
            break
    
    if not bin_path:
        try:
            result = subprocess.run(["which", "openclaw"], capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                bin_path = result.stdout.strip()
        except:
            pass
    
    model = config.get("agents", {}).get("defaults", {}).get("model", "")
    
    # Check gateway health
    gateway_ok = False
    try:
        import socket
        sock = socket.create_connection(("127.0.0.1", 18789), timeout=2)
        sock.close()
        gateway_ok = True
    except:
        pass
    
    status_emoji = "🟢" if gateway_ok else "🟡"
    display = f"OpenClaw (小龙虾) {status_emoji}"
    
    return AgentInfo(
        name="openclaw",
        display_name=display,
        emoji="🦞",
        available=bool(config_dir.exists() and bin_path),
        path=bin_path,
        version=model or None,
        config_path=str(config_path) if config_path.exists() else None,
    )


def detect_crewai() -> AgentInfo:
    """Detect CrewAI installation."""
    try:
        import crewai
        return AgentInfo("crewai", "CrewAI", "👥", True)
    except ImportError:
        pass
    try:
        result = subprocess.run(["which", "crewai"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0:
            return AgentInfo("crewai", "CrewAI", "👥", True, path=result.stdout.strip())
    except:
        pass
    return AgentInfo("crewai", "CrewAI", "👥", False)


def detect_autogpt() -> AgentInfo:
    """Detect AutoGPT installation."""
    for name in ["autogpt", "auto-gpt"]:
        try:
            result = subprocess.run(["which", name], capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                return AgentInfo("autogpt", "AutoGPT", "🤖", True, path=result.stdout.strip())
        except:
            pass
    return AgentInfo("autogpt", "AutoGPT", "🤖", False)


def detect_openhands() -> AgentInfo:
    """Detect OpenHands installation."""
    try:
        result = subprocess.run(["docker", "ps", "--filter", "name=openhands", "-q"], capture_output=True, text=True, timeout=5)
        if result.stdout.strip():
            return AgentInfo("openhands", "OpenHands", "👐", True)
    except:
        pass
    return AgentInfo("openhands", "OpenHands", "👐", False)


def detect_gptme() -> AgentInfo:
    """Detect gptme installation."""
    try:
        result = subprocess.run(["which", "gptme"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0:
            return AgentInfo("gptme", "gptme", "💻", True, path=result.stdout.strip())
    except:
        pass
    return AgentInfo("gptme", "gptme", "💻", False)



def interactive_setup():
    """Main setup wizard."""
    banner()
    
    # ── Step 1: Detect Agents ──
    section("检测已安装的 Agent", "🔍")
    
    agents = [
        detect_hermes(),
        detect_openclaw(),
        detect_crewai(),
        detect_autogpt(),
        detect_openhands(),
        detect_gptme(),
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
    
    if len(available) == 1:
        selected = available[0]
        status_info(f"自动选择: {selected.emoji} {selected.display_name}")
    else:
        options = [f"{a.emoji} {a.display_name}" for a in available]
        idx = prompt_choice("选择要使用的 Agent：", options)
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
    
    default_ws = os.path.expanduser("~/partner_workspace")
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
    interval_idx = prompt_choice("Partner 多久做一次研究？", interval_options, default=1)
    interval_minutes = interval_values[interval_idx]
    status_info(f"研究频率: 每 {interval_minutes} 分钟")
    
    # ── Step 6b: QQ Integration ──
    qq_config = {}
    enable_qq = prompt_choice("是否启用 QQ 聊天集成？", [
        "跳过（稍后在 partner_config.json 中手动配置）",
        "配置 QQ 集成"
    ], default=0)
    
    if enable_qq == 1:
        qq_config = setup_qq_config(workspace)
    
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
        "qq": qq_config if qq_config else {"enabled": False},
        "setup_time": datetime.now().isoformat(),
        "agent_path": selected.path,
    }
    config_path = os.path.join(workspace, "partner_config.json")
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    status_ok(f"配置已保存: {config_path}")
    
    # Save pointer for easy discovery
    save_workspace_pointer(workspace)
    section("设置自动研究", "⏰")
    
    if selected.name == "hermes":
        setup_cron_hermes(workspace)
    
    # ── Done ──
    print()
    line("━", 50, C.GREEN)
    print(f"\n  {C.BOLD}{C.GREEN}🎉 Partner 配置完成！{C.RESET}\n")
    print(f"  使用方法：")
    print(f"    1. 打开 {selected.emoji} {selected.display_name}")
    print(f"    2. 直接说：{C.CYAN}'partner 最近在研究什么？'{C.RESET}")
    print(f"    3. 或者说：{C.CYAN}'让 partner 去研究 XXX'{C.RESET}")
    print(f"    4. Partner 会在后台自主运行\n")
    print(f"  管理命令：")
    print(f"    {C.DIM}partner status    查看 Partner 状态{C.RESET}")
    print(f"    {C.DIM}partner setup     重新配置{C.RESET}")
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
    status_info(f"工作区: {config.get('workspace', workspace)}")
    status_info(f"后端: {config.get('backend', 'unknown')}")
    
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
    
    hb_path = os.path.join(state_dir, "heartbeat.json")
    if os.path.exists(hb_path):
        with open(hb_path) as f:
            hb = json.load(f)
        print(f"    💓 最后心跳: {hb.get('last_heartbeat', 'unknown')[:16]}")
        print(f"    📶 状态:     {hb.get('status', 'unknown')}")
    
    section("使用方法", "💡")
    backend = config.get('backend', 'hermes')
    if backend == 'hermes':
        print(f"    打开 Hermes，说：{C.CYAN}'partner 最近在研究什么？'{C.RESET}")
    else:
        print(f"    在 {backend} 中说：{C.CYAN}'partner 最近在研究什么？'{C.RESET}")
    
    print()


def find_workspace():
    """Find Partner workspace."""
    # 1. Environment variable
    ws = os.environ.get("PARTNER_WORKSPACE")
    if ws and os.path.exists(ws):
        return ws
    
    # 2. Pointer file at ~/.partner
    pointer = os.path.expanduser("~/.partner")
    if os.path.exists(pointer):
        with open(pointer) as f:
            path = f.read().strip()
        if path and os.path.exists(os.path.join(path, "partner_config.json")):
            return path
    
    # 3. Common locations
    candidates = [
        os.path.expanduser("~/partner_workspace"),
        os.path.expanduser("~/.partner_workspace"),
    ]
    for c in candidates:
        if os.path.exists(os.path.join(c, "partner_config.json")):
            return c
    return None


def save_workspace_pointer(workspace: str):
    """Save workspace path to ~/.partner for easy discovery."""
    pointer = os.path.expanduser("~/.partner")
    with open(pointer, 'w') as f:
        f.write(workspace)

"""Partner Setup - beautiful interactive configuration wizard."""

import json
import os
import shutil
import subprocess
import sys
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
description: "Partner 🤝 - 自主研究伙伴。用户说 'partner' 关键词时激活。可查询研究进展、添加任务、搜索知识。"
version: 0.1.0
author: Partner Team
tags: [partner, autonomous, research, companion]
---

# Partner 🤝 - Your AI Research Companion

当用户提到 "partner"、"研究伙伴"、"最近研究了什么" 时，进入 Partner 模式。

## 工作区
Partner 数据在 `{workspace}/state/` 下。

## 交互方式

### 查询进展
用户说 "partner 最近在研究什么"、"研究进展" 时：
用 execute_code 读取 `{workspace}/state/journal.jsonl`（最近10条）和 `stats.json`，用中文汇报。

### 搜索知识
用户说 "partner 知道关于 X 的什么" 时：
用 execute_code 搜索 `{workspace}/state/knowledge.json`。

### 添加任务
用户说 "让 partner 去研究 X" 时：
用 execute_code 向 `{workspace}/state/task_queue.json` 添加任务。

### 执行研究
用户说 "让 partner 做一次研究" 时：
用 execute_code 读取最高优先级任务，用 web_search/read_file 执行，更新状态。

## 注意
- 只在 `{workspace}` 内写文件
- 用中文对话
- 不暴露 JSON 文件路径等内部细节
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
    """Detect OpenClaw installation."""
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
    
    # Check binary
    bin_path = None
    for candidate in [home / ".npm-global" / "bin" / "openclaw", Path("/usr/local/bin/openclaw")]:
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
    
    return AgentInfo(
        name="openclaw",
        display_name="OpenClaw (小龙虾)",
        emoji="🦞",
        available=bool(config_dir.exists()),
        path=bin_path,
        version=model,
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

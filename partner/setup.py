"""Setup - configure Partner to work with an existing agent.

This module handles first-time setup:
1. Detect available agents (Hermes, Claude Code, Codex)
2. Configure workspace
3. Register Partner as a skill/plugin in the chosen agent
4. Set up background cron job for autonomous research
"""

import json
import os
import subprocess
import sys
from pathlib import Path


def find_hermes() -> dict:
    """Check if Hermes Agent is installed and configured."""
    info = {"available": False, "path": None, "version": None}
    try:
        result = subprocess.run(["hermes", "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            info["available"] = True
            info["version"] = result.stdout.strip()
            info["path"] = subprocess.run(["which", "hermes"], capture_output=True, text=True).stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return info


def find_claude_code() -> dict:
    """Check if Claude Code is installed."""
    info = {"available": False, "path": None}
    try:
        result = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            info["available"] = True
            info["path"] = subprocess.run(["which", "claude"], capture_output=True, text=True).stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return info


def get_hermes_skills_dir() -> str:
    """Find Hermes personalized skills directory."""
    home = Path.home()
    candidates = [
        home / ".hermes" / "skills" / "personalized",
        home / ".hermes" / "skills",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    # Create it
    target = home / ".hermes" / "skills" / "personalized"
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


def create_partner_skill(workspace: str) -> str:
    """Create a Hermes skill that makes Partner accessible through Hermes conversation."""
    skill_content = f'''---
name: partner
description: "Partner - 自主研究伙伴。用户可以通过自然语言与 Partner 对话，查看研究进展、调整方向、搜索知识。"
version: 0.1.0
author: Partner Team
tags: [partner, autonomous, research, companion]
---

# Partner 🤝 - Your AI Research Companion

## 概述
Partner 是一个自主研究伙伴，驻扎在 `{workspace}`。它在后台自主运行：搜索文献、分析项目、积累知识、生成想法。用户可以随时通过自然语言与它对话。

## 核心交互

当用户提到 "Partner"、"研究伙伴"、"最近研究了什么"、"partner" 等关键词时，进入 Partner 模式。

### 查看研究进展
当用户问 "最近在研究什么"、"partner 做了什么"、"研究进展" 时：
1. 用 execute_code 读取 `{workspace}/state/journal.jsonl`（最近 10 条）
2. 读取 `{workspace}/state/stats.json`
3. 读取 `{workspace}/state/knowledge.json`（最近 5 条）
4. 用中文向用户汇报：完成的任务、关键发现、待探索方向

### 搜索知识库
当用户问 "关于 X partner 知道什么"、"partner 的知识" 时：
1. 用 execute_code 搜索 `{workspace}/state/knowledge.json`
2. 返回匹配的知识条目

### 添加研究任务
当用户说 "让 partner 去研究 X"、"给 partner 添加任务" 时：
1. 用 execute_code 向 `{workspace}/state/task_queue.json` 添加任务
2. 确认任务已添加

### 调整研究方向
当用户说 "暂停 X，让 partner 集中做 Y" 时：
1. 用 execute_code 修改任务优先级
2. 确认调整完成

### 执行研究周期
当用户说 "让 partner 现在做一个研究"、"执行一次" 时：
1. 用 execute_code 读取 `{workspace}/state/task_queue.json` 获取最高优先级任务
2. 用 web_search / read_file 执行任务
3. 用 execute_code 更新状态（complete task, add knowledge, log journal）

## 文件位置
- 任务队列: `{workspace}/state/task_queue.json`
- 知识库: `{workspace}/state/knowledge.json`
- 日志: `{workspace}/state/journal.jsonl`
- 统计: `{workspace}/state/stats.json`
- 心跳: `{workspace}/state/heartbeat.json`

## 注意事项
- Partner 的文件只能在 `{workspace}` 内修改
- 可以读取其他目录的项目文件（只读）
- 所有对话用中文
- 不要暴露内部实现细节（JSON 文件路径等），用户只需要自然语言对话
'''
    return skill_content


def setup_hermes(workspace: str):
    """Set up Partner as a Hermes skill + cron job."""
    print("🔧 配置 Hermes 集成...")
    
    # 1. Create skill
    skills_dir = get_hermes_skills_dir()
    skill_dir = os.path.join(skills_dir, "partner")
    os.makedirs(skill_dir, exist_ok=True)
    
    skill_md = os.path.join(skill_dir, "SKILL.md")
    with open(skill_md, 'w') as f:
        f.write(create_partner_skill(workspace))
    print(f"  ✅ 技能已注册: {skill_md}")
    
    # 2. Create cron setup script
    cron_script = os.path.join(workspace, "setup_cron.py")
    with open(cron_script, 'w') as f:
        f.write(f'''"""Set up Partner cron job for autonomous research."""
import subprocess
import sys

# This script should be run inside a Hermes session
# It uses hermes cronjob to schedule periodic research cycles

CRON_PROMPT = """你是 Partner 的执行引擎。在 {workspace} 下工作。

执行步骤：
1. 用 execute_code 读取 {workspace}/state/task_queue.json，获取最高优先级的 pending 任务
2. 根据任务类型执行：literature_search 用 web_search，project_scan 用 read_file，其他用 web_search
3. 用 execute_code 更新状态：标记完成、添加知识、记录日志、生成新任务

只在 {workspace} 内写文件。用中文。"""

print("Cron prompt ready. Run this inside Hermes to set up:")
print(f"  hermes cronjob create --schedule 'every 30m' --prompt '...'")
print()
print("Or ask Hermes: '请设置 Partner 的自动研究 cron，每 30 分钟执行一次'")
''')
    print(f"  ✅ Cron 脚本已创建: {cron_script}")
    
    # 3. Save config
    config = {
        "workspace": workspace,
        "backend": "hermes",
        "setup_time": __import__('datetime').datetime.now().isoformat(),
        "skill_path": skill_md,
    }
    config_path = os.path.join(workspace, "partner_config.json")
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    print(f"  ✅ 配置已保存: {config_path}")
    
    print()
    print("🎉 Partner 已配置完成！")
    print()
    print("使用方法：")
    print("  1. 打开 Hermes")
    print("  2. 直接说：'partner 最近在研究什么？'")
    print("  3. 或者说：'让 partner 去研究 XXX'")
    print("  4. Partner 会在后台自主运行，你随时可以问它")
    print()
    print("设置自动研究：")
    print("  在 Hermes 中说：'请设置 Partner 的自动研究 cron'")


def interactive_setup():
    """Interactive setup wizard."""
    print("🤝 Partner - 首次配置")
    print("=" * 40)
    print()
    
    # Detect agents
    hermes = find_hermes()
    claude = find_claude_code()
    
    print("检测已安装的 Agent：")
    agents = []
    if hermes["available"]:
        print(f"  ✅ Hermes Agent ({hermes['version']})")
        agents.append("hermes")
    else:
        print("  ❌ Hermes Agent 未安装")
    
    if claude["available"]:
        print(f"  ✅ Claude Code")
        agents.append("claude_code")
    else:
        print("  ❌ Claude Code 未安装")
    
    if not agents:
        print()
        print("没有检测到已安装的 Agent。")
        print("请先安装 Hermes Agent: https://hermes-agent.nousresearch.com")
        return
    
    print()
    
    # Select agent
    if len(agents) == 1:
        selected = agents[0]
        print(f"自动选择: {selected}")
    else:
        print("选择要使用的 Agent：")
        for i, a in enumerate(agents, 1):
            print(f"  {i}. {a}")
        choice = input("请输入编号: ").strip()
        selected = agents[int(choice) - 1] if choice.isdigit() else agents[0]
    
    # Workspace
    print()
    default_workspace = os.path.expanduser("~/partner_workspace")
    workspace = input(f"工作区路径 [{default_workspace}]: ").strip() or default_workspace
    workspace = os.path.expanduser(workspace)
    os.makedirs(workspace, exist_ok=True)
    
    # Create workspace structure
    for d in ["state", "knowledge", "ideas", "logs"]:
        os.makedirs(os.path.join(workspace, d), exist_ok=True)
    
    # Setup based on selected agent
    if selected == "hermes":
        setup_hermes(workspace)
    elif selected == "claude_code":
        print("Claude Code 集成即将推出，敬请期待！")


if __name__ == "__main__":
    interactive_setup()

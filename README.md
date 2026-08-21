<div align="center">

# Partner — 伙伴（AI 自主研究伙伴）

### 面向长周期科研工作的事件驱动 AI 伙伴

Partner 不是聊天机器人，也不是普通的 Agent 运行器。它是一个**分阶段执行**的运行时系统，集成了持久记忆、习惯学习、成长演化，并支持**外部 Agent 编排**。

[![Latest Release](https://img.shields.io/github/v/release/zty522/partner?label=Latest&style=flat-square)](https://github.com/zty522/partner/releases)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

</div>

---

## 快速开始

### Linux / WSL

```bash
curl -fsSL https://raw.githubusercontent.com/zty522/partner/main/scripts/install.sh | bash
partner setup
```

### Windows

PowerShell 5.1+：

```powershell
powershell -Command "& { iwr -useb https://raw.githubusercontent.com/zty522/partner/main/scripts/install.ps1 } | iex"
partner setup
```

也可从 [GitHub Releases](https://github.com/zty522/partner/releases) 下载 Windows 安装包。

### 从源码安装

```bash
git clone https://github.com/zty522/partner.git
cd partner
pip install -e .
partner setup
```

---

## Partner 是什么

Partner 的定位不是替代现有 AI 工具，而是让它们能**协同工作**。它底层可以调度 OpenClaw、Hermes 等多种通用 Agent，也可以调度生物信息等领域的小众 Agent；上层提供事件驱动的自主推进能力，让研究任务不再依赖人工反复提示。部署层面支持多实例隔离、QQ 机器人桥接和 Windows 桌面界面——让 Agent 不只是命令行工具，而是一个可交互、可成长的研究伙伴。

传统 AI 系统是**被动响应式**的——用户提问→模型回答→会话结束。Partner 则不同：

```
LLM:     用户问 → 模型答 → 会话结束
Agent:   用户命令 → 调用工具 → 等待下一个命令
Partner: 上下文/记忆/习惯 → 选择器决定下一个事件 → 执行一步
         → 分析结果、更新状态、学习、选择下一个事件
```

每个阶段包含目标、事件、动作、结果和后续决策。后端 Agent 负责具体工作，而 Partner 在 Agent 调用之外维持连续性。

Partner 同时具备**自我认知与自进化**能力：它盘点自己会什么、不会什么（能力清单），任务开始前先写总设计，执行失败后自动诊断、修复、验证，并把经验沉淀为可复用技能——长期运行中持续变强。

## 核心功能

| 功能 | 说明 |
|------|------|
| **自动安装 Agent** | 需要某个专用 Agent 但未安装时，自动下载、安装、配置——无需手动操作 |
| **LLM 凭证统一管理** | API 凭证集中在 workspace `config/api.json`：对话默认走 DeepSeek，图片理解走 Qwen 视觉模型；所有 Agent 子进程自动继承 |
| **API 调用日志** | 每次 API 调用（DeepSeek / Qwen 等）以 JSONL 记录到 workspace `state/logs/api_calls.jsonl`，含耗时、状态、字符数，便于核对调用与成功率 |
| **统一 Agent 接口** | 任何 CLI/HTTP/Python Agent 均可通过 JSON manifest 注册、统一调用；内置 13 个 Agent（Hermes、OpenClaw、Cline、生物信息学等） |
| **事件驱动流水线** | 102 个 Harness 事件（浏览器、屏幕、代码、conda、搜索、推送、能力盘点等 9 大类），支持依赖关系、并行执行、自动重试 |
| **强制总设计** | 任务执行前先由 LLM 生成软件项目式总设计文档（目标/现状/方案/模块/接口/验收），再按设计逐步执行 |
| **能力盘点** | 自动盘点"会什么 / 不会什么 / 需学什么"，生成能力清单，接新任务前先判断缺什么 |
| **深度研究循环** | 研究类任务自动多轮迭代：每轮产出归档到累积知识库，下一轮基于上轮成果生成有增量的下一步（5 轮上限、多样性控制、产出验证） |
| **自进化与自愈** | 执行失败自动根因诊断 → 生成修复 → 沙箱验证 → 生效 → 沉淀为技能库（Skill Bank）；OODA 引擎带超时断路器防止死循环 |
| **沙箱验证** | 候选代码/改动先在隔离沙箱中跑通再落地，避免破坏运行环境 |
| **浏览器自动化** | 9 个原子操作（打开/点击/输入/截图/提取等），由独立 worker 进程运行，与主进程隔离 |
| **世界模型模拟** | 执行前模拟计划，预测风险并建议优化 |
| **持续成长** | 用户的纠正转化为可复用的习惯，Partner 学习你的工作方式并调整行为 |

## 架构概览

```
┌─────────────────────────────────────────────────────┐
│ 交互层：QQ Bot 桥接 / 桌面 GUI / TUI / CLI          │
├─────────────────────────────────────────────────────┤
│ 决策层：消息路由（快速通道 → 选择器 → 计划器）      │
│         batch_planner 生成事件计划（先写总设计）     │
│         OODA 引擎 + 超时断路器                      │
├─────────────────────────────────────────────────────┤
│ 执行层：Harness 事件运行时（102 事件，依赖/并行）   │
│         v2 事件：browser / screen / code / conda /  │
│         search / push / capability / design         │
│         Research Loop 深度研究循环                  │
├─────────────────────────────────────────────────────┤
│ 自进化层：失败诊断 → 修复 → 沙箱验证 → 技能沉淀     │
│         能力盘点 / 自愈引擎 / Skill Bank / 知识库    │
├─────────────────────────────────────────────────────┤
│ 记忆层：习惯、成长事件、项目记忆、累积知识库         │
│         （跨实例共享，随实例重启保持一致）           │
├─────────────────────────────────────────────────────┤
│ 编排层：Agent 注册表 + 13 个内置 Agent manifest      │
│         （Hermes / OpenClaw / Cline / 生物信息学）   │
└─────────────────────────────────────────────────────┘
```

### 外部知识借鉴

Partner 持续从外部前沿工作学习并转化为自身能力：

| 借鉴来源 | 转化为 |
|----------|--------|
| SESA（自进化搜索 Agent） | Skill Bank 技能沉淀机制 |
| ERA（Nature, AI 科研系统） | 树搜索式的自主实验推进 |
| Polar（Agentic RL on Any Harness） | API Proxy 架构 |
| VeriSkill | 技能生成后的验证机制 |
| PocketFlow / CytoBridge / ViSNet / AI2BMD / Amber | 生物信息学工具链集成 |

## 常用命令

```bash
partner setup                  # 配置工作空间、后端、QQ Bot 和实例
partner status                 # 查看运行时和 Bot 状态
partner doctor                 # 检查本地环境（Python、Git、配置等）
partner agent list             # 列出已注册的外部 Agent
partner agent register <path>  # 注册新的 Agent manifest
partner agent call <name> ...  # 调用指定 Agent 执行任务
partner bot start qq           # 启动 QQ Bot 及 Mind 运行时
partner bot stop qq            # 停止 QQ Bot
partner tui                    # 进入交互式 TUI 模式
partner update                 # 拉取最新代码并重装
partner instance list          # 列出所有实例
partner ollama setup           # 配置可选的 Ollama 本地/远程模型池
partner server add             # 添加远程 SSH 服务器
```

## 外部 Agent 架构

Partner 提供一套标准化的外部 Agent 调用体系。每个 Agent 通过 JSON manifest 描述自身（名称、版本、能力、端点类型、安装信息、LLM 凭证配置）：

- **参数替换**：manifest 中的 `{input}`、`{output}`、`{question}` 等占位符由计划器填充，`{__llm_*__}` 由 Partner 自动注入凭证
- **自动发现**：按优先级搜索内置 manifest、工作空间配置、全局配置、用户注册目录
- **自动安装**：首次使用时自动通过 pip/git/npm/go/cargo/script 安装，安装后自动运行 `--help` 校验参数
- **配置注入**：安装后自动写入 LLM 配置文件，Agent 无需手动设置 API Key

**添加一个外部 Agent 只需要三步：**

```bash
# 1. 创建 manifest JSON
partner/agents/manifests/my-agent.json

# 2. 注册
partner agent register --manifest my-agent.json

# 3. 调用
call_agent_skill(agent="my-agent", task="分析这份数据")
```

### Agent 后端

| 后端 | 说明 |
|------|------|
| `hermes` | 主后端，支持工具调用的分阶段执行 |
| `codex` | 适用于代码密集型任务和仓库编辑 |
| `openclaw` | 可选的 OpenClaw Gateway 后端 |
| `direct` | 最小内置回退方案（直连 DeepSeek API，带硬超时保护） |

## 设计理念

- **事件优先**：始终在执行前决定下一个事件
- **LLM 选择器优先**：语义路由由选择器负责，而非硬编码规则
- **一次事件，一个闭环**：每次后端调用完成一个可验证的动作
- **证据先于断言**：文件、路径、来源和指标必须是真实的才能成为证据
- **成长优于修补**：用户的纠正成为可复用的习惯或成长，而非一次性 if 分支
- **修复根因而非压制症状**：问题诊断到根因再改，不靠去重字典、关键词过滤等 workaround 掩盖
- **先设计后执行**：研究任务先写总设计，明确目标、方案与验收标准再动手
- **外部 Agent 只配 manifest，不 fork 仓库**：外部 Agent 代码在其自己的仓库，Partner 只携带描述文件
- **安装时验证，而非运行时**：CLI 参数在安装时通过 `--help` 校验
- **显式停止**：项目执行应通过 `stop_project` 事件明确停止

---

## 第三方代码声明 / Third-Party Notices

本项目借鉴了以下开源项目的设计模式和代码：

- **Hermes Agent** (MIT) — https://github.com/nousresearch/hermes-agent
- **Hermes Desktop** (MIT) — https://github.com/fathah/hermes-desktop
- **OpenClaw** (MIT) — https://github.com/openclaw/openclaw
- **OpenClaw Windows Hub** (MIT) — https://github.com/openclaw/openclaw-windows-node

详见 `NOTICE.md`。

---

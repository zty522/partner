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

## 核心定位

传统 AI 系统是**被动响应式**的——用户提问→模型回答→会话结束。Partner 则不同：

```
LLM:     用户问 → 模型答 → 会话结束
Agent:   用户命令 → 调用工具 → 等待下一个命令
Partner: 上下文/记忆/习惯 → 选择器决定下一个事件 → 执行一步
         → 分析结果、更新状态、学习、选择下一个事件
```

Partner 围绕**分阶段执行**构建：每个阶段包含目标、事件、动作、结果和后续决策。后端 Agent 负责具体工作，而 Partner 在 Agent 调用之外维持连续性。

---

## 核心功能

| 功能 | 说明 |
|------|------|
| **自动安装 Agent** | 需要某个专用 Agent 但未安装时，自动下载、安装、配置——无需手动操作 |
| **LLM 凭证共享** | 所有 Agent 子进程自动继承 Partner 的 API Key 和端点配置——一次配置，随处可用 |
| **CLI 参数校验** | 安装 Agent 后自动运行 `--help`，对照 manifest 参数逐项验证，安装即发现配置错误 |
| **统一 Agent 接口** | 任何 CLI/HTTP/Python Agent 均可通过 JSON manifest 注册，统一调用 |
| **持续成长** | 用户的纠正转化为可复用的习惯，Partner 学习你的工作方式并调整行为 |
| **事件驱动流水线** | 任务拆解为事件，支持依赖关系、并行执行、自动重试 |
| **世界模型模拟** | 执行前模拟计划，预测风险并建议优化 |

---

## 常用命令

```bash
partner setup                  # 配置工作空间、后端、QQ Bot 和实例
partner status                 # 查看运行时和 Bot 状态
partner doctor                 # 检查本地环境（Python、Git、配置、Hermes 等）
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

---

## 外部 Agent 架构

Partner v1.0.0 引入了一套标准化的外部 Agent 调用体系：

**每个 Agent 通过 JSON manifest 描述自身**，包含名称、版本、能力、端点类型（cli/http/python_api/mcp）、安装信息、LLM 凭证配置。支持以下特性：

- **参数替换**：manifest 中的 `{input}`、`{output}`、`{question}` 等占位符由计划器填充，`{__llm_*__}` 由 Partner 自动注入凭证
- **自动发现**：AgentRegistry 按优先级搜索内置 manifest、工作空间配置、全局配置、用户注册目录
- **自动安装**：如果 manifest 包含 `install_info`，首次使用时自动通过 pip/git/npm/go/cargo/script 安装，安装后自动运行 `--help` 校验参数
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

---

## Agent 后端支持

| 后端 | 说明 |
|------|------|
| `hermes` | 主后端，支持工具调用的分阶段执行 |
| `codex` | 适用于代码密集型任务和仓库编辑 |
| `openclaw` | 可选的 OpenClaw Gateway 后端 |
| `direct` | 最小内置回退方案 |

---

## 设计理念

- **事件优先**：始终在执行前决定下一个事件
- **LLM 选择器优先**：语义路由由选择器负责，而非硬编码规则
- **一次事件，一个闭环**：每次后端调用完成一个可验证的动作
- **证据先于断言**：文件、路径、来源和指标必须是真实的才能成为证据
- **成长优于修补**：用户的纠正成为可复用的习惯或成长，而非一次性 if 分支
- **外部 Agent 只配 manifest，不 fork 仓库**：外部 Agent 代码在其自己的仓库，Partner 只携带描述文件
- **安装时验证，而非运行时**：CLI 参数在安装时通过 `--help` 校验
- **显式停止**：项目执行应通过 `stop_project` 事件明确停止

---

## 交流群

欢迎加入交流群获取最新动态和帮助：

| 微信群 | QQ 群 |
|--------|-------|
| ![微信群](../data/2/微信群.jpg) | ![QQ群](../data/2/qq群.jpg) |

---

## 第三方代码声明 / Third-Party Notices

本项目借鉴了以下开源项目的设计模式和代码：

- **Hermes Agent** (MIT) — https://github.com/nousresearch/hermes-agent
- **Hermes Desktop** (MIT) — https://github.com/fathah/hermes-desktop
- **OpenClaw** (MIT) — https://github.com/openclaw/openclaw
- **OpenClaw Windows Hub** (MIT) — https://github.com/openclaw/openclaw-windows-node

详见 `NOTICE.md`。

---

## 贡献者

| 名字/昵称 | 来自哪里 | 贡献类型 | 备注 |
|-----------|----------|----------|------|
| zty | 四川大学 | 代码修改 | 项目创建者 |
| | | | |
| | | | |
| | | | |
| | | | |

*欢迎提交 PR 或 Issue 贡献代码、文档、建议或测试！*

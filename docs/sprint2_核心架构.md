\# Sprint 2: 核心架构重构

**时间**: 2026-06-17 \~ 2026-06-28

**目标**: 重构 Partner 代码包结构、建立 Agent
接口体系、实现集中式项目管理

## 1. 设计内容

### 1.1 包重组（51 → 25 根 .py 文件）

**问题**: \`partner/\` 根目录堆积了 51 个 .py
文件，职责不清，循环导入风险高。

**设计**: 将根目录文件按职责分入 9 个子包：

  -----------------------------------------------------------------------
  **子包**                **迁移的文件**          **职责**
  ----------------------- ----------------------- -----------------------
  \`core/\`               config,                 核心基础设施
                          workspace_layout,       
                          instance_root 等        

  \`adapters/\`           adapter.py,             LLM 适配器层
                          hermes_adapter.py 等    

  \`state/\`              setup, task_queue,      状态管理
                          checkpoint 等           

  \`dialogue/\`           dialogue_logger,        对话记录
                          qq_chat_history 等      

  \`tasks/\`              task_instance,          任务实例
                          task_manager 等         

  \`knowledge/\`          learning,               知识/学习
                          knowledge_base 等       

  \`data/\`               event_execution 配置    内置数据

  \`harness_core/\`       robust_executor,        Harness 核心原语
                          task_instance,          
                          artifact_validator,     
                          remediation_handler     

  \`msg_queue/\`          queue → msg_queue       消息队列（避免 stdlib
                                                  冲突）
  -----------------------------------------------------------------------

Shell 层独立为 \`shells/frontend/\` 包：\`qq_bot/\`, \`desktop_gui/\`,
\`tui/\`。

### 1.2 Agent 接口体系

建立标准化的外部 Agent 集成层：

-   \`AgentManifest\` --- JSON/YAML 标准格式，定义 Agent
    的调用方式、能力、依赖

-   \`AgentRegistry\` --- 三级发现：内置 manifest → workspace
    \`config/agents/\` → \`\~/.partner/agents/\`

-   \`AgentDispatcher\` --- 四种调用方式：CLI / HTTP API / Python API /
    MCP

-   内置 7 个 Agent manifest：hermes, codex, openclaw, cline,
    cognitive-kernel, julius-ai, skyvern

-   自动发现系统：从 GitHub 搜索并生成
    manifest（\`discover_and_register_agent()\`）

### 1.3 集中式项目池

**之前**: 每个实例有各自的 \`projects/\` 目录，文件分散。

**之后**: 所有项目统一在 \`shared_projects/\` 下，通过 \`.lock\`
文件做实例间协调：

-   \`claim_project()\` → 写入 \`.lock\`（instance_id + timestamp）

-   \`release_project()\` → 删除 \`.lock\`

-   \`check_project_availability()\` → 返回锁持有者

### 1.4 CLI 重构

从单文件 1463 行 \`cli.py\` → 7 文件模块树：

-   \`cli/main.py\` --- 子命令分发

-   \`cli/onboard.py\` --- 环境初始化

-   \`cli/gateway.py\` --- Agent 网关

-   \`cli/world_model_cli.py\` --- 世界模型管理

-   \`cli/common.py\` --- 共享工具函数

### 1.5 世界模型集成

-   \`partner/world_model/client.py\` --- WorldModelClient，支持
    AETHER/LLM/Heuristic 三层回退

-   \`partner/world_model/server.py\` --- REST API 服务器 (port 8100)

-   配置：\`world_model.yaml\` 支持 hybrid / aether_only / llm_only 模式

### 1.6 工作区指针文件

\`\~/.partner_workspace\` --- 跨平台工作区路径指针，支持 WSL ↔ Windows
双向转换。

## 2. 关键文件

  -----------------------------------------------------------------------------------
  **文件**                            **行数**                **功能**
  ----------------------------------- ----------------------- -----------------------
  \`partner/agents/manifest.py\`      \~200                   AgentManifest 数据类

  \`partner/agents/registry.py\`      \~250                   AgentRegistry 三级发现

  \`partner/agents/dispatcher.py\`    \~300                   AgentDispatcher
                                                              调用分发

  \`partner/agents/discoverer.py\`    \~400                   GitHub 自动发现

  \`partner/world_model/client.py\`   \~200                   WorldModelClient

  \`partner/world_model/server.py\`   \~150                   World Model REST Server

  \`partner/cli/main.py\`             \~150                   CLI 子命令入口
  -----------------------------------------------------------------------------------

## 3. 完成情况

  ---------------------------------------------------------------------------
  **功能**                **状态**                **验证方式**
  ----------------------- ----------------------- ---------------------------
  包重组                  ✅ 完成                 import 全部通过，0 循环引用

  Agent 接口              ✅ 完成                 329/333 个 manifest
                                                  成功加载

  项目池                  ✅ 完成                 \`.lock\`
                                                  机制正常，8个项目成功迁移

  CLI 重构                ✅ 完成                 \`partner benchmark list\`
                                                  等命令正常

  世界模型                ✅ 完成                 AETHER API 健康检查通过

  指针文件                ✅ 完成                 WSL/Windows
                                                  双向路径转换正常
  ---------------------------------------------------------------------------

## 4. 遗留问题

-   世界模型集成仅限 partner05（独立代码库），partner03 未接入

-   Agent 自动发现返回 327 个 manifest，但 4 个格式不兼容

-   \`codex\` binary 存在但 call_agent_skill 返回 \"no usable output\"

-   shadow Hermes home (\`system/hermes_home/\`) 有 207MB 垃圾文件

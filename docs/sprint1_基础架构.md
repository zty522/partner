\# Sprint 1: 基础架构与工作区治理

**时间**: 2026-06-14 \~ 2026-06-17

**目标**: 建立 Partner 的基础运行框架：统一工作区、配置中心化、QQ Bot
消息通道

## 1. 设计内容

### 1.1 工作区统一布局

**问题**: 之前数据散落在
\`\~/.partner/\`、\`workspace/state/\`、\`workspace/user/\`
等多个位置，实例间配置混乱。

**设计**: 建立规范的工作区根目录结构：

**清理项**: 删除了 \`ideas/\`, \`knowledge/\`, \`logs/\`, \`scripts/\`,
\`common/\`, \`external/\` 等僵尸目录。

### 1.2 配置加载链

统一配置加载路径为 \`workspace_root/config/\`，废弃实例级和仓库级
fallback：

-   \`config.py:\_config_root()\` 从实例目录自动推导 workspace_root

-   \`qq_config.json\` 增加 \`instance_id\` 字段防止多实例共享同一凭证

-   \`batch_planner.yaml\`, \`external_calls.yaml\`,
    \`routing_rules.yaml\` 全部走统一路径

### 1.3 QQ Bot 消息通道

-   实现 \`qq_official_bridge.py\`：WebSocket 连接 QQ Bot API

-   消息路由：\`approval_classify → intent_classify → routing_classify →
    batch_plan\`

-   支持群@消息（member_openid）和私聊消息

-   群被动回复 5次/5分钟配额管理

### 1.4 对话日志系统

双写机制：

-   \`qq_chat_history.jsonl\` --- JSONL 格式，GUI 主要读取

-   \`dialogue/YYYY-MM-DD.log\` --- 纯文本格式，人类可读

### 1.5 Desktop GUI 基础

-   传统 tkinter GUI → PySide6 现代 GUI (ModernMainWindow)

-   页面：Chat / Tasks / Instances / Settings / Logs

-   通过 \`desktop_inbox.jsonl\` 与 Bot 通信

-   WSL ↔ Windows 路径双向转换

## 2. 关键文件

  --------------------------------------------------------------------------------------------
  **文件**                                                **功能**
  ------------------------------------------------------- ------------------------------------
  \`partner/workspace_layout.py\`                         工作区目录解析

  \`partner/config.py\`                                   配置加载统一入口

  \`partner/monitoring/instance_root.py\`                 实例根目录解析（含跨平台路径转换）

  \`shells/frontend/qq_bot/qq_official_bridge.py\`        QQ Bot WebSocket 桥接

  \`shells/frontend/desktop_gui/modern/main_window.py\`   PySide6 主窗口

  \`partner/mind/executor.py\`                            执行引擎（43KB+）
  --------------------------------------------------------------------------------------------

## 3. 完成情况

  -------------------------------------------------------------------------
  **功能**                **状态**                **验证方式**
  ----------------------- ----------------------- -------------------------
  工作区布局              ✅ 完成                 partner03 正常运行

  配置加载链              ✅ 完成                 3个实例各自使用正确配置

  QQ Bot 连接             ✅ 完成                 群@消息正确路由

  对话日志                ✅ 完成                 \`dialogue/\`
                                                  每日日志正常写入

  Desktop GUI             ✅ 完成                 PySide6
                                                  窗口可启动，WSL互通

  路径转换                ✅ 完成                 \`\_wsl_to_windows\` /
                                                  \`\_windows_to_wsl\` 双向
  -------------------------------------------------------------------------

## 4. 遗留问题

-   \`queue/\` 目录与 Python stdlib \`queue\` 模块冲突 → Sprint 2
    修复（重命名为 \`msg_queue/\`）

-   \`dialog_history.jsonl\` 旧格式 → Sprint 2 迁移到 \`.log\` 格式

-   \`\~/.partner/\` 路径仍被部分模块引用 → Sprint 2 统一为
    \`PARTNER_DATA_DIR\`

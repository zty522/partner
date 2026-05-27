# Changelog

## [0.4.0] - 2026-05-28

### 🏗️ 架构重构
- **代码瘦身 33%**：从 13,715 行减至 9,158 行，删除 10 个冗余文件
- **新三层架构**：Shell(CLI/GUI/QQ) → State JSON → Engine(Hermes cron)，移除旧 Python 内核编排器
- **Event Bus 推送系统**：`event_bus.jsonl` 统一管理所有推送事件（研究突破/卡死/自检发现）
- **轻量自检引擎**：替代 603 行理论自进化引擎，每次心跳做 3 步自检（知识冲突、卡死、数据泄漏）
- **统一 QQ 桥接**：删除 NapCat 模式，只保留 QQ Official Bot
- **单点事实源**：`find_workspace()` 三合一，统一由 `setup.py` 提供
- **cron 频率升级**：30 分钟→15 分钟，响应更快

### 🔥 删除的模块
- `event.py` + `event_engine.py` + `event_templates.py` — 被 active_plan 取代
- `self_evolution.py` — 被轻量 `self_check.py` 取代
- `napcat_bridge.py` + `napcat_onebot.py` + `napcat_proxy.py` — 统一为官方 Bot
- `hermes_adapter.py` + `other_adapters.py` + `openclaw_adapter.py` + `openclaw_bridge.py` — 重复适配器
- `idea_generator.py` — 未连接任何模块
- `wsl_bridge.py` — 从未使用
- `cycle_runner.py` + `heartbeat.py` + `test_qq_bot.py` + 三个迁移脚本 — 一次性/测试代码

### 新增模块
- `event_bus.py` — 基于 jsonl 的推送事件系统（push/pop/peek/clear）
- `self_check.py` — 轻量自检（知识冲突 + 卡死 + 数据泄漏检测）
- `ARCHITECTURE-v2.md` — 新架构设计文档
- `proactive_notifier.py` — 精简为 Event Bus 阅读器（419→70 行）

### 配置变更
- cron 默认间隔从 30 分钟改为 15 分钟
- QQ 设置向导改为官方 Bot 模式（AppID + AppSecret）
- 移除 OpenClaw/CrewAI/AutoGPT/OpenHands/gptme Agent 检测

### 🚀 新增功能
- **即时 QQ 回复**：用户发消息时立即回复"请等待，正在思考..."，然后后台处理后再发送实际回复（解决"回消息太慢"问题）
- **自进化引擎**：每 2 小时自动运行三层进化（策略学习 + 记忆清理 + 能力退化防护）
- **研究后自检查**：研究计划完成后自动分析结果、判断瓶颈、提出下一步方向
- **通知系统增强**：重大突破/瓶颈时自动创建通知文件，下次用户对话时汇报

### 🐛 Bug 修复
- **QQ Bot 看门狗**：增加最大重启次数限制（10次/5分钟），防止无限重启循环
- **PID 清理**：启动时清理上一轮关机残留的 PID 文件
- **Hermes Adapter 超时**：从 300s 增加到 600s，减少"请求超时"错误
- **任务队列重复**：后台清理重复任务，防止同一指令多次入队
- **active_plan.json 双份修复**：删除 state/ 目录下的过期副本，统一使用工作区根目录版本
- **send_qq_report.py 路径修正**：心跳报告从读 state/active_plan.json 改为读根目录版本，解决 QQ 汇报永远滞后的问题
- **heartbeat.json 冗余文件清理**：删除 state/ 目录下的过期心跳文件

### ⚙️ 改进
- **消息处理异步化**：QQ 消息处理改为后台线程，不阻塞 Bot 事件循环
- **心跳汇报优化**：抑制任务刚入队后 3 分钟内的重复心跳汇报
- **主动通知**：研究里程碑、高置信度发现、用户关注领域更新自动推送
- **Cron 提示词重构**：Partner 从"执行器"升级为"研究规划引擎"
  - 计划完成后自动复盘结果 + 搜索最新文献 + 创建延续计划
  - 完全空闲时扫描知识空白，主动生成新研究方向
  - 每 4 轮或每次计划完成后自动触发自进化
- **NapCat 机器人支持**：新增 napcat_bridge.py，支持通过 NapCat(本地 DLL 注入) 接收 QQ 消息
- **GUI 完整重写**：GitHub Dark 风格、i18n 中英文切换、简化 QQ Bot 配置页
- **router.py GREETING 意图**：新增问候检测和自然回复

### 📦 v0.4.0 组件
- `scripts/self_evolution.py` — 自进化引擎（三层）
- Cron Job: `partner-self-evolution` — 每 2 小时运行
- 更新: `qq_official_bridge.py` — 即时回复 + 异步处理
- 更新: `bot_watchdog.py` — 重启限制 + PID 清理
- 更新: `adapter.py` — 超时增加
- 更新: `partner-research` skill — 自检查 + 自推进

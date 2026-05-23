---
title: "Partner：一个在你睡觉时自动做科研的 AI"
tags: AI, 科研工具, 开源, 生物信息学
---

# Partner：一个在你睡觉时自动做科研的 AI

**LLM 生成文本，Agent 执行任务，Partner 做科研——完全自主。**

## 背景

作为一个科研工作者，我总有太多想法没时间去实现。论文要读，实验要跑，代码要review，还要找不同项目之间的关联。不管我多努力，待办事项永远在增长。

如果有一个研究伙伴，能独立工作——读论文、分析代码库、积累知识库、提出新想法——而我可以专注于最难的问题，会怎样？

## AI 的三个层次

我们已经见过两层 AI 工具：

- **LLM**：你提问 → 它回答 → 结束（被动式）
- **Agent**：你下达指令 → 它执行 → 等待下一步（反应式）

LLM 是被动的，需要 prompt 才能工作。Agent 是反应式的，需要命令才会执行。

我想要的是**主动式** AI：打开就开始工作，随时间积累知识，你问的时候它给你汇报。

**Partner 就是这样：它自己工作 → 你问"你最近在研究什么" → 它汇报。**

## 什么是 Partner

Partner 是一个**自主研究实体**，搭建在现有 Agent 框架之上（就像 Agent 搭建在 LLM 之上一样）。

核心交互非常简单：

> **"Hey Partner, 你最近在研究什么？"**

它会告诉你它在你离开期间发现的所有东西。

## 工作原理

Partner 在后台运行，每 30 分钟执行一次研究周期（可配置）。每个周期：

1. 从任务队列中选取最高优先级的任务
2. 通过 Agent 后端执行（网页搜索、代码分析等）
3. 将发现记录到知识库
4. 基于新知识生成新任务
5. 永远循环

Partner 的架构由三个核心模块组成：
- **任务队列**：优先级排序，自动生成 + 用户注入
- **知识库**：研究发现、方法、工具、跨项目关联
- **日志系统**：活动记录，带时间戳

## Event：Partner 的心脏

一个 **Event** 就是一次完整的研究周期——就像 Agent 有 Skill，Partner 有 Event。

每个 Event 遵循结构化流程：
- 📖 文献搜索 → 搜索和阅读论文
- 🔬 项目扫描 → 分析你的代码库
- 💡 想法生成 → 提出改进方案
- 🧭 探索方向 → 尝试新方向
- 📝 知识记录 → 记录发现
- 🌱 衍生事件 → 创建新 Event

Event 会**自己生长**——一个 Event 的发现会自动衍生出新 Event，研究永不停止。

## 真实效果

我在生物信息学项目上跑了一整夜 Partner，早上起来发现：

- **29 个研究周期**自主完成
- **34 个任务**执行完毕
- **48 条知识**积累入库
- **94 个任务**排队等待探索

Partner 自主发现的关键成果：

| 发现 | 影响 |
|------|------|
| scGPT 需要针对衰老任务做领域微调 | 避免了一条死路 |
| 扩散模型正在取代 VAE 做分子生成 | 找到 25+ 篇新论文 |
| 批次校正使跨数据集泛化提升 52.8% | 量化了改进效果 |
| ProDCARL RL 对齐将 AMP 命中率从 2% 提升到 6.3% | 发现了新的抗菌肽设计方法 |

## 多 Agent 支持

Partner 不重新发明轮子，它搭建在现有 Agent 框架之上：

- 🔮 Hermes Agent：✅ 完全支持
- 🦞 OpenClaw：✅ 支持
- ⚡ OpenAI Codex：✅ 支持
- 👥 CrewAI：✅ 支持
- 💻 gptme：✅ 支持

运行 `partner setup` 自动检测已安装的 Agent。

## 跨平台

Partner 运行在 **Linux、macOS、Windows 和 WSL** 上。在 WSL 上，它可以通过 WSL Bridge 自动访问 Windows 文件。

## 开始使用

```bash
git clone https://github.com/zty522/partner.git
cd partner
pip install -e .
partner setup
```

然后打开你的 Agent（Hermes、OpenClaw 等），说：

> **"Hey Partner, 你最近在研究什么？"**

## 下一步计划

- **微信/QQ 集成** — 通过语音消息和 Partner 对话
- **社区 Event** — 分享和安装 Event 模板
- **多 Partner 协作** — 多个 Partner 一起工作
- **更多 Agent 后端** — Claude Code、Cursor 等

---

**Partner：因为研究不应该等你。**

GitHub: https://github.com/zty522/partner

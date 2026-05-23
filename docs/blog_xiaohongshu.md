---
title: "我做了个 AI，它在我睡觉时自动做科研 🤯"
---

# 我做了个 AI，它在我睡觉时自动做科研 🤯

## 起因

作为一个生信狗，每天有看不完的论文、跑不完的实验、review 不完的代码。经常想：**能不能有个 AI 帮我做科研？**

不是那种"你问它答"的 ChatGPT，而是**真正自主工作**的 AI。

## 于是 Partner 诞生了

Partner 是一个**自主研究实体**，搭建在现有 Agent 框架之上。

**核心交互超简单：**
> "Hey Partner, 你最近在研究什么？"

它就会告诉你它在你离开期间发现的所有东西。

## 它怎么工作的？

Partner 在后台**每 30 分钟自动执行一次研究周期**：

1. 从任务队列选最高优先级任务
2. 通过 Agent 后端执行（搜索论文、分析代码等）
3. 把发现记录到知识库
4. 基于新知识生成新任务
5. 永远循环 🔄

**Event 系统**是它的核心：
- 📖 文献搜索
- 🔬 项目扫描
- 💡 想法生成
- 🧭 探索方向
- 📝 知识记录
- 🌱 衍生新 Event

**Event 会自己生长**——一个 Event 的发现会自动衍生出新 Event，研究永不停止！

## 跑了一夜的真实效果

我在生信项目上跑了一整夜，早上起来发现：

- **29 个研究周期**自主完成 ✅
- **34 个任务**执行完毕 ✅
- **48 条知识**积累入库 ✅
- **94 个任务**排队等待 📋

**Partner 自主发现的关键成果：**

1️⃣ scGPT 需要针对衰老任务做领域微调 → 避免了一条死路
2️⃣ 扩散模型正在取代 VAE 做分子生成 → 找到 25+ 篇新论文
3️⃣ 批次校正使跨数据集泛化提升 52.8% → 量化了改进效果
4️⃣ ProDCARL RL 对齐将 AMP 命中率从 2% → 6.3% → 发现新方法

## 支持的 Agent

Partner 不重新发明轮子，它搭建在现有 Agent 框架之上：

- 🔮 Hermes Agent ✅
- 🦞 OpenClaw ✅
- ⚡ OpenAI Codex ✅
- 👥 CrewAI ✅
- 💻 gptme ✅

## 开始使用

```bash
git clone https://github.com/zty522/partner.git
cd partner
pip install -e .
partner setup
```

然后打开你的 Agent，说：
> "Hey Partner, 你最近在研究什么？"

## 下一步

- 📱 微信/QQ 集成
- 🤝 社区 Event 分享
- 👥 多 Partner 协作
- 🔌 更多 Agent 后端

---

**Partner：因为研究不应该等你。**

🔗 GitHub: https://github.com/zty522/partner

#AI #科研工具 #开源 #生信 #Agent #自动化

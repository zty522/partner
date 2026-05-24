# Changelog

All notable changes to Partner will be documented in this file.

## [v0.1.0] - 2026-05-24

### ✨ Features

#### Event 系统（自主研究）
- 自主研究周期：每 15-30 分钟自动执行研究任务
- Event 模板：8 种预定义研究流程
- 知识空白检测：自动识别需要深入研究的领域
- Event 统计：追踪完成数、衍生数、执行阶段数

#### 对话引擎 V2
- 多轮上下文：记住对话历史和上下文
- 响应生成器：支持"第 3 个是什么"、"下一页"等指令
- 主动通知：发现重要结果时主动提醒
- 用户偏好学习：自动适应研究风格

#### 自我进化引擎
- 策略学习器：学习哪种任务类型最成功
- 记忆清理器：自动清理过时知识
- CPE 守护：监控核心能力，降级时告警

#### 策略地图
- DAG 结构：可视化研究路线图
- 分支发现：自动发现新研究方向
- 策略选择：5 因子评分决定下一步

#### 质量保证
- 100+ 单元测试
- 知识库自动审查
- 端到端集成测试

### 🐛 Bug Fixes
- 修复 task_queue 字符串处理问题
- 修复任务详情打印问题
- 添加 partner skill 工具限制钩子

### 📦 Infrastructure
- pyproject.toml：支持 pip install partner
- 可选依赖：partner[wechat]、partner[qq]、partner[voice]
- CHANGELOG.md：版本历史追踪
- release.sh：自动发布脚本

### 🤖 Supported Agents
- 🔮 Hermes Agent (完全支持)
- 🦞 OpenClaw (支持)
- ⚡ OpenAI Codex (支持)
- 👥 CrewAI (支持)
- 💻 gptme (支持)

---

## Planned

### v0.2.0 (In Progress)
- [ ] QQ 集成 (NapCat)
- [ ] 微信集成 (跨平台方案)
- [ ] OpenClaw 集成检查
- [ ] 科研 Agent 适配 (CytoBridge 等)
- [ ] 一键式消息平台配置
- [ ] Partner 自动升级机制

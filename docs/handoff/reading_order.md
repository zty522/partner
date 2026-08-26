# LLM / Agent 接手阅读顺序

## 必读

1. 根目录 `AGENTS.md`。
2. `docs/README.md`。
3. `docs/current_status.md`。
4. `docs/product_principles.md`，确认不可因新优化回退的产品基本原则。
5. `docs/architecture/manual_stable_core.md`，确认当前唯一生产执行路径和禁用能力。
6. `docs/catalog.yaml`，根据当前实例和任务选择 L2/L3 内容。

## 按任务选读

- 01/小红书：`docs/playbooks/xiaohongshu_browser.md`。
- 02/分子研究：`docs/projects/molecular_generation.md`。
- 手动消息、逐步回执和五实例轮换：`docs/architecture/manual_stable_core.md`。
- 项目续跑、自进化、RL、Campaign：当前都是暂停的实验方向；只有用户明确要求重新研究时，
  才按 `docs/catalog.yaml` 选择对应文档，不得把历史设计当当前运行要求。
- 外部资料/RL 借鉴：`docs/external_learning.md`；涉及 Harness、事件证据或 trace 时再读
  `docs/architecture/harness_reference_adoption.md`。
- 上下文/文档库：`docs/architecture/knowledge_system.md`。
- 实例切换：`docs/architecture/instance_scheduler.md`。
- 连续数小时/整夜运行：`docs/architecture/continuous_campaign.md`、
  `docs/architecture/autonomous_scheduler.md`、`docs/architecture/recovery_and_watchdog.md`、
  `docs/architecture/cost_and_safety_budget.md` 和 `docs/operations/overnight_run.md`。
- 诊断 2026-08-23 两小时失败样本：`docs/operations/campaign_2h_audit_2026-08-23.md`。
- Sprint 11–13 是历史实验记录，不是当前启动说明。

## 历史文档规则

Sprint、`change_log.md` 和 `evolution_journal.md` 仅用于追溯。不得从历史记录推断当前实例活性或当前能力。
需要历史经验时，先通过目录/标签缩小范围，再读取对应片段，不整库注入。

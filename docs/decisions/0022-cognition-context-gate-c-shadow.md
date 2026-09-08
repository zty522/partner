# ADR 0022：Cognition Context Gate C Shadow

**日期**：2026-08-28  
**状态**：Accepted for shadow；禁止生产生效

## 背景

Gate B 已能把真实 Episode 映射为可验证认知影子。下一步不是让认知系统修改代码，而是选择一个低风险、
可隔离的决策：上下文排序。现有 `context_selector.py` 已实现 catalog、mandatory L1、预算与 provenance，
因此 Candidate 不能复制加载器，只能改变排序信号。

## 首版失败及纠正

实验 `experiment_780b5cc8351d` 把 cognition 推导出的 `external_learning` 当作 hard requested ID。形式检查
一度显示 7/7，但人工核对发现它挤掉最新 Receipt 3/7、挤掉 query 相关 Harness 文档 4/7。该实验已记录
`rejected` PromotionDecision，Candidate revision 2 为 `rejected`；历史未覆盖。

## 决策

1. 修正版只给 cognition 文档 ID 加有界 `+10` soft boost，显式用户 request 仍具有更高优先级。
2. Candidate 使用共享 selector 的 `reserve_receipt_chars=1200`，保证项目 L3 承接不会被新 L2 文档挤掉。
3. baseline 调用的默认参数和输出保持不变；生产 planner/executor 不导入 `cognition_context.py`。
4. 机械门新增：mandatory L1、同预算上限、确定性、有效认知来源、最新 Receipt、baseline query 相关文档、
   至少一个 cognition route 真正入选、两条路径 digest 不同。
5. 只消费 adapter 再次核验过的 archive：bundle digest、import id/status、未 invalidate、精确
   project/instance、权威 source Episode 全部成立。

## 结果与边界

修正版实验 `experiment_acfec34e9d7b` 的 7 个机械匹配对全部通过；3/7 实际加入 cognition-ranked
`external_learning`，其余保留更强的 query-ranked Harness 文档；7/7 Candidate 都带最新 Receipt。
Candidate revision 3 为 `shadow`、`production_effective=false`。

这不是任务质量实验：没有运行 14 个独立 baseline/candidate 业务任务，没有 truth/reward/latency 对照，
因此 causal status 为 `not_executed_no_quality_claim`，不允许 canary 或 promotion。

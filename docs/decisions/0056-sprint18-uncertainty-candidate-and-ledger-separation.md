# ADR 0056：Sprint 18 不确定性 Candidate 与学习/项目账本隔离

**日期**：2026-09-01  
**状态**：Accepted for isolated learning；production promotion 未发生

## 背景

三 seed 主动采样与 OOB slice-calibration Candidate 均未达到稳健门。下一步不能继续换权重，而应先回答
“当前 uncertainty 是否真的能识别高误差”。同一轮实跑还暴露了两种框架假阴性和一种账本串线：学习
Event 把证据写入共享治理目录而 Campaign 只查 task-local；Campaign report 被旧生产策略当作业务任务；
内层已标为 isolated learning 的结果又被 Campaign 外层写成 Project Receipt 并触发业务 continuation。

## 决策

1. 新增 `targetdiff_uncertainty_diagnostic` Event，在 official test 上仅后验评估 raw RF variance 的
   Spearman、top-error enrichment/recall 与 RMSD slice；test label 不进入训练或选择。
2. 对 `uncertainty_miscalibration` 使用 `recipe_uncertainty_estimator_comparison_v1`，新增
   `targetdiff_uncertainty_candidate` matched Event：baseline 为 tree variance，Candidate 为仅从已标注
   样本 cross-fitted OOF residual 学得的 uncertainty；三 seed、预算和预测模型冻结。
3. `[sprint18=true]` 的 allowlisted learning Event 只写学习 observation/trajectory，不写 Project Receipt，
   不取得 production-policy 资格，也不触发 `propose_continuation`。本地 durable artifact 可满足隔离学习验收，
   但不能取得 delivery credit。
4. Campaign shared-artifact 收集只接受已知学习 Event 的 output-like 字段，且文件必须位于
   `share/mind/governance/{active_learning,research_learning}`；输入 source path 永远不能冒充产物。
5. Campaign report 永远是 control-plane monitor，Reward 为 0，不能应用业务 production policy 或修改项目状态。
6. 旧误投影以 append-only correction/Receipt invalidation 修正；原始 Task、Event、失败轨迹和真实额外业务
   产物全部保留。

## 真实结果

- Raw uncertainty diagnosis：平均 Spearman `-0.0624746`、top-error lift `1.03472`、recall `0.238095`；
  三项预设门全失败，结论 `uncertainty_weak_or_miscalibrated`。
- Cross-fitted residual Candidate：三 seed 赢 `2/3`；平均 Spearman `-0.130647 → -0.018113`，top-error
  recall `0.238095 → 0.333333`。决策仅为 `accept_for_acquisition_shadow`；相关性仍接近 0，不足以进入生产。
- Four Harness context v3：绑定有效 Receipt `receipt_4fd68f29fc41`，选入 DeepSeek projection.ts、Hermes
  context_compressor.py 两份当前 SHA-256 证据及 3 条同项目轨迹；`production_effective=false`。
- Campaign 最终 `22 completed / 7 blocked / 0 active`，无非终态 WorkItem，进入 `WAITING_EVIDENCE`。
- Observation 最终 `677 total / 611 learning / 94 promotion / 440 positive / 237 negative`。
- 全仓 `716 passed, 2 warnings in 140.78s`。

## 边界与后续

本 ADR 证明的是：真实负结果能改变下一诊断、产生领域匹配的 R0 Candidate、完成 matched shadow，并正确
分离学习与项目账本。它不证明 cross-fitted uncertainty 已改善最终 active acquisition，更不证明长期 RL
成熟。下一实验必须把该 estimator 放入冻结的 equal-budget acquisition shadow，再跨 seed 比较最终 RMSE；
若不优则拒绝。三真实日期、20/arm、Wilson、anchor、false-success=0 和 rollback 门不降低。

## 回滚

两个 TargetDiff Event 与 context Candidate 均 `production_effective=false`。停止 Campaign 即停止新任务；
生产 `manual_stable` 和 `control_policy.json` 不变。投影 correction 可由追加 reinstate 事件回滚，但不得删除
历史记录。

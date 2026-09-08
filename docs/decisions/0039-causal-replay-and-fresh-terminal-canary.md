# ADR 0039：依赖图因果回放与 fresh terminal canary

**日期**：2026-08-30  
**状态**：Accepted as evidence correction and fresh shadow validation；production/Campaign 未启动

## 为什么 ADR 0038 的 9/10 还不够

ADR 0038 的 evaluator v1 只把 `remediation_triggered.failures` 中明确写出
`skipped: required dependencies failed` 的步骤认作 repairable，因此将 10 个 unmatched generate_text 中的
9 个归为 dependency skip，剩余 1 个暂列未知中断。

复核该 Episode `episode_d8b2d6a014d22a95` 后发现：

- `step1` 已明确失败；
- `step4 generate_text` 依赖 step1/2/3，且参数直接引用 step1；
- 当前 PlanExecutor 对这种 required failed reference 必然早退为 skip；
- 同批的独立 `step_6 → step_7` 分支成功生成 `report.md`，整体 artifact validation 通过，因此没有生成
  remediation 事件；
- trace 中的 `smart_llm_structured_action` 模型成功属于独立 sibling，不是 step4 generate_text 的模型调用。

所以最后 1 个同样是 skip，只是 v1 evaluator 把“没有 remediation”误当“没有 skip 证据”。旧 9/10 结果
保留为 evaluator 局限证据，不覆盖。

## evaluator v2

历史只读回放现在同时使用：

1. `batch_plan_created` / `iteration_started` 的 plan DAG；
2. 每步 `depends_on`；
3. 参数中的 `$step...` required reference；
4. 上游 terminal `failed/skipped`；
5. remediation（如果存在）。

它按实际早退条件递归传播 skip，但不根据任务 prose 猜测。正式 Experiment
`experiment_b00fdeb632e2` 得到：9 个 Episode、baseline 10 个 unclosed、10 个均有直接或依赖图 skip
证据、remaining 0、历史文件 byte-identical。Policy 仍 inconclusive，因为这是历史证据归因修正。

## fresh runtime canary

为避免只靠反事实，新建完全隔离、无网络和无模型的真实 PlanExecutor 五步 DAG：

```text
source(failed) ─→ compose(generate_text, must-not-run) ─→ deliver(push, must-not-run)
sibling(success) ─→ artifact(success)
```

- Experiment：`experiment_25880910b688`
- Candidate：`candidate_skipped_fresh_25880910b688`
- Event：`agent_active_learning_skipped_terminal_fresh_canary`
- source=`failed`；sibling/artifact=`completed`；compose/deliver=`skipped`；
- compose/deliver handler 未调用，每步 terminal count=1；
- model calls=0；六项 criteria 全过；
- feedback 写入 `lifecycle.unclosed_tool/generate_text|bounded_repair`；
- Evolution ledger 100 events，head
  `766c9e610f708a0cd3a2732d6fe8392c001e60f5440b47653511accedc9fbcf5`，验证通过。

Policy 仍 `inconclusive`：fresh canary 证明当前 runtime 的并行终态语义，不证明原始业务任务、外部 Agent、
生成质量、文件交付或生产长期运行已经改善。

Partner 完整回归：`537 passed in 202.27s`。

## 工程经验

首版 fresh test 使用同步 handler，PlanExecutor 按设计将其放入 worker thread；测试退出时出现线程/asyncio
等待，造成 canary 无意义变慢。canary handler 改为 async 本地确定性函数后约 4 秒完成。这里没有扩大超时，
也没有关闭真实 PlanExecutor 控制流。

下一步不应继续围绕该已闭合 failure class 重复诊断。应让全局 selector 重新选择下一高价值失败类，并为它
建立独立 diagnosis；只有 fresh 业务任务给出可信 outcome，才进入 action-value/RL 学习。

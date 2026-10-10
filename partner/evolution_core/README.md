# Partner Evolution Core (L5: 进化与治理)

L5 负责 partner **如何改进自己、如何保证质量**。

## 组成（逻辑归属）

| 组件 | 物理位置（过渡期） | 说明 |
|---|---|---|
| 自进化 | `partner/events/self_evolution.py`、`evolution*.py`、`partner/event_flows/cycle.py` | autonomous_evolution / supervision_cycle / learning_improvement_cycle / self_improvement_cycle |
| 改进循环 | `partner/events/improvement.py` | observe → evidence → experiment → settle → consolidate |
| Benchmark/评估 | `partner/events/benchmark.py`、`evaluation.py`、`v4_benchmark.py`、`v5_research.py`、`loop_bench.py`、`meta_cycle.py`、`partner/event_flows/benchmark.py` | 374 次引用，回归保障 |
| 验收/承诺 | `partner/events/acceptance.py`、`commitment.py`、`goal/acceptance_criteria.py` | acceptance_criteria 现 0 引用，待接线 |
| 治理/规则 | `partner/events/governance/`、`context/layered_rules.py`、`rules/` | layered_rules 现 0 引用，待接线 |
| 监督 | `partner/events/supervision.py`、`system_evolution.py`、`long_run_controller.py`、`fair_scheduler.py`、`token_aggregator.py` | 运行期监督 |

## 与 L4 的协作

- L5 自进化修复的"问题"来自 L4 笔记库（issue_note / pending）与运行时监督事件池。
- L5 每次进化尝试必须产出 `EvolutionExperiment` + `PromotionDecision`，
  并经 notes.promote 同步笔记状态（失败记 dismissed，成功记 active/confirmed）。
- L4 学习到的新 idea 通过 notes.judge 的 action_now 转成 L5 的候选改进。

## 使用纪律

- 生产行为变更必须有：聚焦测试 + 比例回归 + 文档更新。
- 自进化候选先 isolate，经 baseline/candidate 匹配证据才 promote。
- 新 L5 代码一律落 `partner/evolution_core/`，禁止散落新文件。

# Commitment 内核：会赌的自闭环

状态：本轮新增实现（2026-09-19），离线可测、独立于生产自主拓扑
代码：`partner/commitment/`、`partner/events/commitment.py`、`partner/application/commitment_adapter.py`
测试：`tests/commitment/`（79 项通过）
样本：`benchmarks/commitment_loop/molecular_vertical_sample.py`
benchmark 骨架：`benchmarks/commitment_loop/`

## 一句话定义

内核只负责一个闭合回路：**读取冻结状态 → 提出一个可证伪问题 → 提出有限候选并明确舍弃项 →
冻结预期/失败条件/评价协议/预算 → 请求一个有界动作 → 独立测量 → 证据结算 → 形成经验并停止**。

它不调度、不恢复、不播种后续工作。调度是 Event Fabric 的职责；内核只决定"什么为真"。

## 与现有 Partner 的边界

现有 Partner 保持不动，作为真实环境与将来对照基线。新内核是**同仓库内的独立包**，不是
`partner_v2` 分叉：它不复制 Application、Event、QQ、Web、索引或领域执行器，只通过
`commitment_adapter.py` 这一层薄适配接触既有能力（点读快照、RDKit 分子执行、确定性测量）。

本包不启动 Application 队列、watchdog、共享 worker 或任何渠道，也不读写生产的 Job 存储。

## 对象（`models.py`）

| 对象 | 承载什么 | 刻意不承载什么 |
|---|---|---|
| `BetRecord` | 问题、有限候选、选择与舍弃理由、冻结预期、失败条件、评价协议、baseline、预算、承诺策略、版本字段 | 不承载状态推进；语义字段一旦 COMMITTED 不可原地改 |
| `ExecutionReceipt` | 请求/实际动作、执行器身份版本、起止时间、exit/status、产物与哈希、预算实耗、幂等键 | 不承载"是否成功"的判断（那是结算的事） |
| `OutcomeMeasurement` | 指标名/值/方向/单位、测量者与版本、证据引用、缺失原因、有效性、产物未变证明 | 不承载任何 LLM 自评分 |
| `SettlementDecision` | 五类裁决、逐条预期结果、baseline/candidate 匹配证明、新增回归、既有失败、发布资格、下一步、机器规则 | LLM 解释只占 `llm_explanation` 一个非权威字段 |
| `ExperienceRecord` | 完整"状态—动作—结果—评价"链、作用域、置信度 | `level` 只能是 `experience`；不能自称 habit/growth/权威 |

**结构性区分**（不是靠措辞）：

- `proposed_by`（谁判断）vs `measured_by`（谁测量）
- `improvement_observed`（预期是否移动）vs `publish_eligible`（是否允许发布）
- `new_regressions`（baseline 通过而 candidate 失败）vs `pre_existing_failures`（两者都失败）
- 项目结果与 Partner 自身进化分属不同台账，`BetRecord.project_id` 与 `partner_id` 不混用

## 状态机（`state_machine.py`）

```
DRAFT → PROPOSED → COMMITTED → EXECUTING → MEASURED → SETTLED → CLOSED
异常终态：INVALID / BLOCKED / BUDGET_EXHAUSTED / CANCELLED
SETTLED → COMMITTED 仅在一次策略允许且带新证据的"转向"时出现
```

`ALLOWED_TRANSITIONS` 是唯一真值：没有边的跳转（`DRAFT→COMMITTED`、`EXECUTING→SETTLED` …）
不是"不推荐"，而是不可能。LLM 与 Event runner 都不能越过它写终态。

## 12 条不变量与实现位置

| # | 不变量 | 位置 |
|---|---|---|
| 1 | 一个 bet 只有一个活动执行所有者、一个当前 revision | `runner._claim()`（append-only owner claim，先写者胜）+ `state_machine.advance` 的 revision 更新 |
| 2 | COMMITTED 后语义不可原地改，只能追加 revision 并引用父 revision | `store.save_bet()`（比对语义字段 + 强制 `revision+1` / `parent_revision`）+ `models.SEMANTIC_FIELDS` / `freeze_hash()` |
| 3 | 无有效 ExecutionReceipt 不能进入 MEASURED | `state_machine.advance`（要求 receipt 且 `is_valid`，bet_id 必须匹配） |
| 4 | 无独立 OutcomeMeasurement 不能进入 SETTLED | `state_machine.advance`（metric_violation 类结算要求 `validity=valid`） |
| 5 | SETTLED 幂等，重复消息不产生第二次经验或下一轮 | `state_machine.advance`（已 settled 即拒）+ `runner.run()` 终态即 replay + `bet_id` 作用域的 experience 只写一次 |
| 6 | baseline 与 candidate 同输入、同评价器、同预算口径、同冻结版本 | `settlement.settle()` 的 `ComparisonProof`（五项逐项比对，不匹配即 inconclusive） |
| 7 | baseline 失败且 candidate 同样失败 = 既有阻塞，不是新增回归 | `settlement.settle()` 的 `pre_existing_failure` 分支 |
| 8 | 预期达到但发布门失败 → 允许记录"有改善但不发布" | `SettlementDecision` 允许 `improvement_observed=True` + `publish_eligible=False` + `publish_blockers` |
| 9 | 预算按绝对 deadline 与累计调用计，阶段转换不重置 | `Budget.deadline_epoch`（绝对）+ `BudgetUsage` 随 lifecycle 持久化 + `policy.may_spend` 逐资源判定 + `state_machine.deadline_passed` |
| 10 | 缺失/污染/评分异常不得记成 0 分或成功 | `evaluator`（missing/invalid + 原因）+ `settlement`（blocked/invalid 优先于任何成绩读数） |
| 11 | 无新证据不得反复生成候选或无限转向 | `state_machine.assert_turn_allowed`（turn 预算 + 最早时点 + 必须有新证据） |
| 12 | runner 到达终态或预算边界即停止，不自行播种无界 Job | `runner.run()` 只走一个 bet；`policy.post_settlement_decision` 默认 CLOSED；Event 层不生成后续 Event |

## 端口（`ports.py`）

`StateReader` / `CandidateProposer` / `ActionExecutor` / `IndependentEvaluator` /
`ExternalLearningRequester` / `ConsequenceForecaster`。

后两者**本轮只提供 `Null` / `Shadow` 实现**：`NullExternalLearningRequester` 永远不授权，
`ShadowConsequenceForecaster` 返回 `authoritative=False` 且不产生预测。
预留端口是为了让边界被类型化声明，不是为了让世界模型或主动学习大循环从侧门进来。

## 独立测量如何成立

`evaluator.py` 三重保证：

1. 只读 ExecutionReceipt 明确点名的产物；
2. 拒绝自报告类文件名（`verdict`/`self_score`/`success_claim` …）与声明根之外的路径；
3. 测量前后对产物取哈希并要求不变——一边测一边被改写的产物不构成测量。

缺失/不可解析/哈希不符 → `missing`/`invalid` 并写明原因，绝不静默记 0 或记成功。
若 receipt 的产物哈希与磁盘不符（产物被执行后被改动），同样判 invalid。

## 与 Event Fabric 的边界（`partner/events/commitment.py`）

| 归属 | 负责 |
|---|---|
| Event Fabric | 何时运行、重试、恢复、投递 |
| 内核 | bet 的含义、何时可测、结果结算成什么 |

事件是薄的：`commitment.bet_run` 读声明式 bet spec → 交给内核 runner → 把终态投影回 Event summary；
`commitment.bet_state` / `commitment.bet_settlement` 只读投影。
事件处理器不写 lifecycle，也不生成后续 Event。`KERNEL_STATE_EVENTS` 只是一张映射表，不是调度器。

## 存储与读取纪律（`store.py`）

`<workspace>/state/commitments/<run_id>/<bet_id>/` 下：append-only `events.jsonl`（哈希链）、
物化 `state.json`、`bet.json` 与 `revisions/`、`receipts/`、`measurements/`、`settlement/`、
`experience/`、`context/`、`manifest.json`、`issues.jsonl`。

- 幂等：`append` 以 `event_id` 去重，重放 Event Fabric 消息不会产生第二次执行/经验/轮次。
- 恢复：`state.json` 丢失时从哈希链重放；`state.json` 存在但不可读则 **fail closed**（不猜）。
- 完整性：`verify_chain()` 发现断裂即记录 Issue 并拒绝继续写；历史从不被重写。
- 读取：全部是已知路径的点读，本模块不 `os.walk`，一次状态转换不会退化成全树扫描。

## 本轮明确不做

世界模型、多 Partner 协同、QQ/Web、PDF、长期无人值守、生产 Job 迁移、旧自主循环改造、
生产代码自动晋升。`partner_id` 只是身份字段。

## 何时允许接入生产

见 `docs/decisions/0104-commitment-kernel-sidecar.md`：需要（a）生产 Job 存储只读投影接入，
（b）一次 canary bet 在真实项目快照上通过并留下可审计产物，（c）与 `instance_native` 的
续跑所有权做显式交接，三者齐备后另开一轮评审。


---

## 语义纠偏（2026-09-19，schema commitment/2）

上一版本交付后复测发现四个会**给出错误结论**的语义问题，已修复；实现位置在此列出，详见 ADR 0105。

### 1. 执行环境决定可发布性

`BetRecord.environment`（冻结、进语义哈希）取值
`synthetic_fixture` / `isolated_sample` / `shadow` / `production_canary` / `production`，
默认 `synthetic_fixture`。前三种在**合同层**产生不可绕过的发布阻塞
（`environment_not_publishable:<env>` 加上 `isolated_sample_only` / `synthetic_fixture_only` /
`shadow_only`）；`SettlementDecision.__post_init__` 直接拒绝"非生产环境 + publish_eligible"。
发布还要求 `EvaluationProtocol.replicates >= 2`（否则 `single_episode_only`）。
分子隔离切片因此是"观察到改善但不可发布"。

### 2. 达标 ≠ 改善

预期带 `kind`，结算按 kind 计算，产出三个独立字段：

| kind | candidate 侧规则 | 相对 baseline 的规则 | `supported_claim` |
|---|---|---|---|
| `absolute_threshold` | `threshold`（按 direction） | 无 | `absolute_attainment` |
| `delta_over_baseline` | `threshold` | 冻结方向上提升 ≥ `min_delta`（且必须 > 0） | `improvement_over_baseline` |
| `non_inferiority` | 无绝对规则 | 不得低于 baseline 超过 `tolerance` | `non_inferiority` |
| `guardrail` | 停留在 `threshold` 内 | 无 | `guardrail_held` |

- `expectations_met`：candidate 是否达到预期；
- `improvement_over_baseline`：是否**按冻结方向和最小效应量**优于 baseline；
- `baseline_already_satisfied`：baseline 本已满足绝对阈值。

`improvement_observed` 保留为 `improvement_over_baseline` 的**严格别名**（不一致即 ContractError），
不再是"所有预期达到"。旧产物的含义因此不被静默改写（见 §5 迁移策略）。

### 3. baseline 是证据

`BaselineEvidence`（`partner/commitment/evidence.py` + `models.py`）要求：真实 receipt 与独立
measurement 及其哈希、输入/数据哈希、执行器与评价器身份版本、协议与预算哈希、环境指纹、
harness 版本、处理变量、产物哈希**与测量前后不变证明**，以及来源
（本轮新执行 / 复用历史冻结证据 + 兼容性证明）。runner 通过 `BaselineEvidenceProvider`
端口获取；没有 provider、provider 失败、或证据不可采纳 → **BLOCKED**，不执行 candidate、
不产生结算。`admissibility()` 对 6 项控制变量逐项校验，缺失即不可采纳（"缺少证明"不等于"兼容"）。

### 4. 控制变量相同 + 处理变量显式不同

`ComparisonProof` 用 6 项控制变量（输入/评价器/协议/预算口径/环境/harness）替代
`code_version_identical` 硬门，并记录 `baseline_treatment`、`candidate_treatment`、
`treatment_diff_hash`、`expected_treatment_diff_hash`，以及分别的baseline/candidate 代码版本。
`TreatmentSpec`（run 前声明）→ `TreatmentContract`（冻结时确定）：
- 项目动作实验：代码相同、动作不同 → matched；
- 代码自进化实验：`allows_code_change=True` + `declared_paths`，实际改动集合必须**完全等于**
  声明集合，多一个文件 → `harness_version_identical=False` → unmatched；
- 未声明处理变量（`treatment=None`）→ 永远 unmatched（`treatment_contract_missing`）。

### 5. schema 迁移策略

`SCHEMA_VERSION = "commitment/2"`，`LEGACY_SCHEMA_VERSIONS = ("commitment/1",)`。
旧记录**只读可读**（`from_dict` 填充安全默认、`schema_legacy=True` 跳过新增跨字段门），
**新代码不写 v1、不回写历史、不伪造旧基线缺失的证据字段**。

### 6. legacy（commitment/1）记录的发布资格归零

v1 记录没有可信 `environment`。**不得**由任何声明反推环境：统一进入 `legacy_unknown`
（属 `NON_PUBLISHABLE_ENVIRONMENTS`）。`schema_legacy=True` 是不可绕过的发布/晋升阻塞
（`legacy_schema_untrusted`）；旧 `publish_eligible` 仅以 `legacy_publish_claim` 保留供审计；
v1 的 `ComparisonProof` 标记 `legacy_untrusted`、`matched` 恒为 False；`build_experience`
拒绝 legacy 结算，`ExperienceRecord.assert_promotable()` 是所有晋升路径的唯一闸门。
读取历史不改字节、不改哈希链。

> 初版曾把"按旧 `publish_eligible` 反推 production"当作安全默认值——那是错的，它让旧隔离实验
> 的自我声明变成了当前生产权威。见 ADR 0105 的同日修订。

# commitment 内核：四项语义纠偏验收（2026-09-19）

## 现场

- worktree `/mnt/e/work/partner_commitment`，分支 `feature/commitment-kernel`，基线 HEAD `ba786c79060ba031d991cdee9ed0f2b06acc9ac2`
- 生产进程数 **0**；生产 Job DB `/home/os/.local/share/partner/runtime/83d524ab71b932a6/jobs.db`
  计数 queued 139 / completed 71 / failed 33 / running 20（cancelled 0），mtime `2026-09-18 23:46:09` **未变**
  （精确路径核验，未使用 runtime 目录 glob）
- 本轮未启动生产拓扑、未领取/取消/改写任何积压 Job、未接入 `instance_native`

## 命令与结果

| # | 命令 | 退出码 | 结果 |
|---|---|---|---|
| 1 | `PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider tests/commitment/` | 0 | **109 passed in 4.95s**（上一轮 79；本轮新增/改写 30） |
| 2 | `python3 -c "from partner.events import builtin_definitions; builtin_definitions()"` | 0 | 135 条定义、无重名，含 3 条 `commitment.*` |
| 3 | `python3 benchmarks/commitment_loop/run_benchmark.py --workspace benchmark_runs/_ws_v2 --out benchmark_runs/commitment_loop/skeleton_v2` | 0 | 9 次运行（3 臂 × 3 seed），`infrastructure_only: true` |
| 4 | `python3 benchmarks/commitment_loop/molecular_vertical_sample.py --workspace benchmark_runs/_slice_ws_v2 --out benchmark_runs/commitment_loop/isolated_slice_v2` | 0 | 新 schema 切片，观察到改善但不可发布 |

## 四个问题的根因与修复位置

### 修复一：隔离/合成/shadow 样本禁止发布

- **根因**：`environment` 概念不存在，发布门只看"达成预期 + 比较 matched + 无新增回归"，
  于是隔离 workspace 的单轮样本也能得到 `publish_eligible=True`。
- **修复位置**：`models.BetRecord.environment`（冻结、进 `SEMANTIC_FIELDS`、
  `EXECUTION_ENVIRONMENTS`/`NON_PUBLISHABLE_ENVIRONMENTS`）、
  `models.SettlementDecision.__post_init__`（非生产环境 + publishable 直接 ContractError）、
  `settlement.settle()` 的发布门（`environment_not_publishable:*`、`isolated_sample_only`、
  `synthetic_fixture_only`、`shadow_only`、`replicates<2 → single_episode_only`）、
  `models.EvaluationProtocol.replicates`、`freezer.assert_falsifiable`（生产环境必须声明处理变量）。

### 修复二：达到阈值 ≠ 相对 baseline 改善

- **根因**：`settlement.settle()` 把 `improvement_observed` 定义为"所有预期 met"，
  在 baseline 本就达标、candidate 只是持平时会误报改善。
- **修复位置**：`models.ExpectedEffect.kind/min_delta/tolerance` + `improvement_over()`；
  `settlement.settle()` 按 kind 计算 `expectations_met` / `improvement_over_baseline` /
  `baseline_already_satisfied`，并由 `_claim()` 给出 `supported_claim`；
  `models.SettlementDecision` 强制 `improvement_observed == improvement_over_baseline`。

### 修复三：baseline 必须是可验证证据

- **根因**：`runner._baseline_observation()` 从冻结快照读 `baseline_metrics`，再补上与 candidate
  相同的输入/评价器/协议/预算/代码版本，使 "matched" 依赖声明。
- **修复位置**：新增 `partner/commitment/evidence.py`（`BaselineEvidence` 构建与
  `admissibility()`/`compatibility_report()` 六项控制变量校验）、
  `ports.BaselineEvidenceProvider`、`runner._walk` 第 5a 步（无证据即 BLOCKED，不执行 candidate）、
  `store.save_baseline_evidence()`（receipt/measurement/evidence 一并进入 append-only 存储与 manifest）；
  删除 `settlement.BaselineObservation` / `CandidateObservation`。

### 修复四：匹配实验允许处理变量不同

- **根因**：`ComparisonProof.code_version_identical` 作为硬门，使"冻结 patch 是唯一处理变量"的
  代码自进化实验在语义上不可能 matched。
- **修复位置**：`models.ComparisonProof` 改为 6 项控制变量 + 处理变量对 + 差异哈希 + 分列代码版本；
  `models.TreatmentSpec`（run 前声明）/`TreatmentContract`（冻结时确定，含 `declared_paths`、
  `allows_code_change`）；`settlement.treatment_descriptor()` /
  `treatment_diff_hash()` / `make_treatment_contract()`；
  `runner` 在冻结时构造 contract，并对"声明外文件改动"判 `harness_version_identical=False`。

## schema 版本与旧产物兼容策略

`commitment/1 → commitment/2`。旧记录**只读兼容**：`from_dict` 接受 v1 并填安全默认
（环境缺失按 `publish_eligible` 反推、`improvement_over_baseline` 回落 `improvement_observed`、
`budget_identical`/`code_version_identical` 映射到新字段），`schema_legacy=True` 的旧记录跳过新增
跨字段门；**新代码只写 v2、不回写历史、不伪造旧基线缺失的证据字段**。由
`test_legacy_v1_records_stay_readable_and_are_never_rewritten` 覆盖。

## 新增隔离切片（run_id `molecular_run_llm`，目录 `benchmark_runs/commitment_loop/isolated_slice_v2`）

名称更正为**隔离化学垂直切片**（不再是"真实科研项目纵向样本"）。

- 池：8 个公开常见芳香化合物（固定）；**代理指标**（QED/SA），不代表生物活性
- baseline：**正式 `BaselineEvidence`**，由 `MolecularIdentityBaselineProvider` 真实执行 identity
  变换并用同一确定性评测器测量：qed_mean **0.494351**、sa_mean 1.108327、valid 1.0，
  `immutable=True`、`provenance=fresh_execution`、evaluator `deterministic-molecular-evaluator 1.0.0`、
  executor `allow-listed-molecule-executor 1.0.0`；receipt + measurement + 证据三者均在
  append-only 存储与 manifest 中（`baseline/base_bet_molecular_llm.json`）
- 候选（3 个，LLM 提案）→ 选择 `cand_hydroxyl`（**候选先验** expected_gain 0.06 / risk 0.45，
  非已验证预测）；测量仍由确定性评测器独立完成
- 冻结预期：`kind=delta_over_baseline`，threshold = baseline+0.03，`min_delta=0.03`
- candidate 测量：qed_mean **0.527759**、sa_mean 1.532461、valid_fraction 1.0 → **delta +0.033408**
- 结算（schema commitment/2）：`supported`，`expectations_met=True`，
  `improvement_over_baseline=True`，`baseline_already_satisfied=False`，
  `supported_claim=improvement_over_baseline`，comparison `matched=True`（6 项控制变量全 true，
  `treatment_as_frozen=True`，`code_changed=False`）
- **`publish_eligible=False`**，`publish_blockers = [environment_not_publishable:isolated_sample,
  isolated_sample_only, single_episode_only]`
- experience：1 条，`level=experience`、`authoritative=False`；未晋升 habit/growth/生产策略
- 旧 v1 切片产物 `benchmark_runs/commitment_loop/sample_final/` 原样保留为历史，未覆盖

## benchmark 三臂各自实际执行了什么

| 臂 | 实际执行 | 定义行为是否执行 |
|---|---|---|
| `fixed_pipeline` | 按声明顺序跑满 3 轮，无冻结预期、无结算 | 是（确定性臂，无需替代） |
| `free_llm_loop` | **无真实 LLM client，跑的是确定性贪心重放** | **否**（manifest 记录 `defining_behaviour_executed=false` 并写入 `limitations`） |
| `commitment_loop` | 一个 bet：控制臂真实执行 → 冻结 → 候选执行 → 独立测量 → 机器结算 → 1 轮即终止 | 是 |

三臂仍只是**基础设施验证**（`infrastructure_only: true`、`research_claim: none`），不支持臂间比较。

## LLM 调用计数

- **本轮总尝试：1 次**（`deepseek-v4-flash`，成功），用于验证结构化提案路径；
- **最终切片记录：1 次调用**，latency 4060 ms，usage prompt 281 / completion 683 / total 964
  （reasoning 590）；
- LLM 只提出候选 id 与理由；**选择**由候选先验 + 守卫选择器完成，**测量与裁决**全部由确定性
  评测器与机器规则完成，LLM 自评分未参与结算。

## 关于 provider 配置路径

本轮记录的那一次切片运行，其 `--config-workspace` 由当时的默认值解析为
`/mnt/e/work/partner_workspace`。该硬编码默认值已移除：现在改为从 `$PARTNER_WORKSPACE` 解析，
未设置时**跳过 LLM 提案路径并记录原因**（回落到确定性 proposer），避免把私人绝对路径写进 Git。

## 剩余限制

- 切片仍是**单 bet、单轮、固定 8 分子、代理指标**：不支持研究结论，也不能发布；
- benchmark 的 free LLM 臂未按定义行为执行；三臂不构成性能比较；
- 生产接入仍需 ADR 0104 的三项前置条件（只读 Job 投影、canary bet 通过、续跑所有权交接），
  且旧主链队列仍为 139 queued，未清理；
- 本轮未验证：多 bet 并发、跨实例迁移、长期无人值守、真实项目两轮 Bet canary。

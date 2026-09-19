# ADR 0105：commitment 内核的四项语义纠偏（schema commitment/1 → commitment/2）

- 状态：已接受（纠偏完成，生产接入仍未授权）
- 日期：2026-09-19
- 关联：ADR 0104、`docs/architecture/commitment_loop.md`、
  `docs/testing/commitment_kernel_acceptance_20260919.md`

## 背景

ADR 0104 交付的内核在复测中暴露四个语义问题，均是"看起来对、实际会给出错误结论"的类型：

1. 隔离/合成样本也能得到 `publish_eligible=True`；
2. `improvement_observed` 用"所有预期达到"计算，把 baseline 本就达标、candidate 只是保持
   的情况写成改善；
3. baseline 只是快照里的一个数，比较的"matched"依赖声明而非执行证据；
4. `code_version_identical` 作为硬门，使"代码 patch 就是唯一处理变量"的实验在语义上不可能通过。

## 决策

**一、执行环境进入冻结合同。** 新增冻结字段 `environment ∈ {synthetic_fixture,
isolated_sample, shadow, production_canary, production}`，默认值是最保守的 `synthetic_fixture`。
前三种环境**在合同层**产生不可绕过的发布阻塞理由（`environment_not_publishable:<env>`、
`isolated_sample_only`、`synthetic_fixture_only`、`shadow_only`）；
`SettlementDecision` 构造函数直接拒绝"非生产环境 + publish_eligible=True"。发布还要求重复证据
（`EvaluationProtocol.replicates >= 2`，否则 `single_episode_only`）。环境字段属于语义哈希，
COMMITTED 之后不能原地改。

**二、达标、改善、非劣拆成三个机器判断。** 预期带 `kind`：
`absolute_threshold` / `delta_over_baseline`（需 `min_delta`）/ `non_inferiority`（需 `tolerance`）/
`guardrail`。结算按 kind 计算，产出 `expectations_met`、`improvement_over_baseline`、
`baseline_already_satisfied` 三个独立字段，并给出 `supported_claim` ∈ {improvement_over_baseline,
absolute_attainment, non_inferiority, guardrail_held, none}。`improvement_observed` 保留但被**严格
定义为 `improvement_over_baseline` 的别名**，两者不一致即报错。

**三、baseline 是可验证证据。** 新增 `BaselineEvidence` 合同（receipt/measurement 引用与哈希、
输入与数据哈希、执行器与评价器身份版本、协议与预算哈希、环境指纹、harness 版本、处理变量、
产物哈希与测量前后不变证明、来源 `fresh_execution`/`reused_frozen_evidence` 及兼容性证明）。
runner 不再从快照读 `baseline_metrics` 合成 matched：baseline 由
`BaselineEvidenceProvider` 端口**真实执行并测量**得到，缺失即 BLOCKED，绝不补 0 或补声明值。
历史 baseline 可复用，但必须通过输入/评价器/协议/环境/harness 五项控制变量校验并带兼容证明。

**四、"控制变量相同 + 处理变量显式不同"取代"整体代码版本相同"。**
`ComparisonProof` 改为 `inputs_identical` / `evaluator_identical` / `protocol_identical` /
`budget_comparable` / `environment_identical` / `harness_version_identical` 六项控制变量，
加上 `baseline_treatment` / `candidate_treatment` / `treatment_diff_hash` /
`expected_treatment_diff_hash` 与分别记录的代码版本。项目动作实验在相同代码下比较不同动作；
代码自进化实验允许代码不同，但必须由冻结的 `TreatmentContract`（`declared_paths` +
`allows_code_change`）声明，且实际变化的文件集合必须与声明完全一致，多一个文件即 unmatched。

**五、schema 迁移策略。** `commitment/1 → commitment/2`。旧产物**只读兼容**：`from_dict` 接受 v1
并填充安全默认值（环境缺失时**不得**由任何声明反推，统一进入 `legacy_unknown`，见下方修订，
`improvement_over_baseline` 回落到旧的 `improvement_observed`，ComparisonProof 的
`budget_identical`/`code_version_identical` 映射到新字段），`schema_legacy=True` 的旧记录跳过
新增的跨字段门。**新代码不写 v1、不回写历史、不伪造旧基线缺失的证据字段**。

## 理由

- 前两个问题会让内核**输出错误结论**，而不只是措辞不当；
- 第三个问题让"matched"退化为声明，双盲性的全部价值都建立在这上面；
- 第四个问题会让内核在它最该服务的场景（自进化）里结构性失灵；
- 只读兼容而非重写历史，是为了让已有审计链条继续可用。

## 后果

- 内核行为测试 109 项（上一轮 79 项 + 30 项新增/改写），覆盖环境门、四类预期、三概念拆分、
  baseline 证据链、控制/处理变量比较、旧 schema 只读兼容；
- 分子隔离切片在观察到真实改善（QED 0.494351 → 0.527759）的同时
  `publish_eligible=False`，阻塞理由 `isolated_sample_only` + `single_episode_only`；
- 生产接入仍需 ADR 0104 的三项前置条件，本 ADR 不构成授权。

---

## 修订（2026-09-19，同日第二轮）：legacy 记录的发布资格必须归零

### 被修正的错误

ADR 0105 初版在"schema 迁移策略"里写了一句**错误且危险**的话：v1 记录缺 environment 时
"按 `publish_eligible` 反推为 production/synthetic_fixture"。实现出来的代码是：

```python
environment = "production" if bool(payload.get("publish_eligible")) else "synthetic_fixture"
```

这等于把旧记录**自己声明**的发布资格升级成**当前的环境权威**。而恰恰是旧隔离实验最可能带着
`publish_eligible=true`（这正是修复一要解决的问题），于是修复一修好之后，历史记录又被读回成
`production`——安全修复在迁移路径上被绕过。**"缺少证据"从不等于"生产可用"，更不等于
"按它自己说的算"。**

### 修订后的语义（数据合同层，不是展示层）

1. v1 缺少可信 `environment` 时统一进入 `legacy_unknown`（新增枚举值，正式进入
   `EXECUTION_ENVIRONMENTS` 与 `NON_PUBLISHABLE_ENVIRONMENTS`），**永不**推断为
   `production` / `production_canary`；
2. `schema_legacy=True` 是不可绕过的发布/晋升阻塞条件，blocker 代号
   `legacy_schema_untrusted`；
3. 旧记录原始的 `publish_eligible` 只作为历史声明保留在 `legacy_publish_claim`，
   不构成当前发布资格；构造层面另有硬门：`schema_legacy` 且 `publish_eligible=True`
   直接 `ContractError`；
4. v1 的 `ComparisonProof` 标记 `legacy_untrusted=True`，`matched` 恒为 False
   （它没有 baseline 证据引用，也没有 treatment contract），因此旧记录不可能"matched"；
5. `build_experience` 拒绝 legacy 结算；`ExperienceRecord.assert_promotable()` 成为
   所有晋升消费者（habit / growth / 生产策略 / 代码晋升）必须调用的唯一闸门；
6. 旧记录只读可解析：不改写旧 JSON、不伪造缺失证据、不破坏 append-only 哈希链，
   原始历史含义仍可审计（`legacy_publish_claim`）。测试
   `test_reading_legacy_history_changes_no_bytes_and_no_chain` 断言读取前后文件
   sha256 与链头不变。

### 修正了被保留下来的错误预期

`test_legacy_v1_records_stay_readable_and_are_never_rewritten` 原先断言
"旧 `publish_eligible=true` 不降级"。该预期本身错误，已改为断言
`publish_eligible is False` + `legacy_publish_claim is True` + `environment == "legacy_unknown"`
+ blocker 存在。新增 `tests/commitment/test_legacy_publish_safety.py`（10 项）覆盖
v1 带/不带发布声明、不得映射为 production、缺失 baseline 证据不得 matched、
legacy 不得被经验/晋升消费、v2 生产路径不受影响、新对象不再序列化 v1、历史字节与哈希不变。

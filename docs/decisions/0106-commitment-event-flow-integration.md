# ADR 0106：commitment 内核接入 02 Event 流（接入点、两段式冻结与续跑）

- 状态：已接受（机制验证完成；“科学假设检验级别”的第一次真实闭环仍未做；生产接入未授权）
- 日期：2026-09-19
- 关联：ADR 0104（sidecar）、ADR 0105（语义纠偏 v2）、`docs/architecture/commitment_loop.md`、
  `docs/testing/instance02_commitment_event_binding_20260919.md`（record-only 轮）、
  `docs/testing/instance02_commitment_loop_20260919.md`（执行到结算轮）

## 背景

内核在 0104/0105 后已经完备（冻结、状态机、独立评测、结算、经验），但**只被 Hermes 独立调用**，
02 实例自己的 Event 流不经过它。上一轮把接入点接通了，但只到 record-only：BetRecord 冻结后停在
COMMITTED，没有执行、测量、结算。本轮的目标是让 02 的真实 Event 流把一条 bet 从 COMMITTED
推到 CLOSED。

## 决策

**一、接入点固定为 `understand_3` 之后的两个节点，且必须同时装进两条消息类 Flow。**
节点：`commitment`（事件 `commitment.bet_record`，记录）与 `commitment_execute`
（事件 `commitment.bet_execute`，执行 + 结算），前者依赖 `understand_3`，后者依赖前者。
**不新造 Flow 类型**：`direct_answer` 升到 1.2.0，`project_iteration` 升到 2.6.0。

这条决策的第二半是被真实失败逼出来的：第一轮只把节点装进 `direct_answer`，结果该消息被 02 自身
的意图路由判为 `project_iteration`，**节点根本没被走到**。路由是实例自己的判断，不由我们控制，
因此只在一条 Flow 上装节点等于内核不可达。

**二、记录与执行分离，但共用同一条冻结代码路径。**
`BetRunner._freeze_bet()` 从 `_walk` 中提取出来，`freeze_only()` 与 `_walk` 共用它。原因：
`CommitmentStore.save_bet()` 在 revision 不变时逐字段比对，**任何语义字段不同都会以
“拒绝编辑冻结字段”报错**。只有两次冻结的输入逐字节一致（同一 spec、同一 snapshot、
同一 proposer/selector、同一 budget/protocol/treatment），“先记录再执行”才成立。
Event 层负责“何时”，内核负责“记什么、怎么判”。

**三、续跑语义（COMMITTED → CLOSED 的机制）。**
`_walk` 在 lifecycle 为 `COMMITTED/EXECUTING/MEASURED/SETTLED` 时**不再重新冻结**（那会产生非法的
`COMMITTED -> PROPOSED`），而是从持久化记录恢复 candidates/selection 后继续执行、测量、结算。
两条附加不变量：
- **持久化用量不因重启退还**：续跑前同样过 `ledger.may_spend(model_calls=1)` 与
  `may_spend(actions=1)`，额度已耗尽则停在 `BUDGET_EXHAUSTED`（提案器不会被再次调用）；
- **缺 `bet.json` 时 fail closed**：状态为 COMMITTED 但记录不存在 → `INVALID`，不猜测、不补写。

**四、有界动作必须是确定性、同仪器测量、隔离环境的。**
新增 `partner/application/commitment_bounded_adapter.py`：对消息做确定性文本统计
（`text_len`/`word_count`/`unique_words`/`unique_word_ratio`），control=`identity`、
treatment=`lowercase`（唯一且已冻结的处理差异）；baseline 由**同一个执行器真实执行 control 臂**、
由**同一评测器测量**，落为 `BaselineEvidence(provenance=fresh_execution)`；环境固定
`isolated_sample`，因此**结构性不可发布**。不调 LLM、不碰项目数据、不调外部工具。

**五、旧拓扑必须真正保留可解析。**
1.0.0（9 节点）、1.1.0（10 节点，只有记录节点）、以及 **pre-integration 的 2.5.0（21 节点，不含
commitment 节点）** 都注册进 registry。注意陷阱：`LEGACY_PRESENTATION_FLOWS = tuple(DEFINITIONS)`
是在**节点插入之后**捕获的，所以它里面的 2.5.0 已经带上了新节点；必须再用 `replace()` 显式注册
被过滤掉的旧拓扑，且放在最后（同名同版本键取最后写入者）。

**六、审计轨不借状态转换表达。**
`JobRepository.note(job_id, actor, kind, detail)` 追加 `from_status == to_status` 的
`job_history` 行，使内核动作在 Web timeline 可见，同时**不可能被误认为生命周期转换**。

## 后果

- 现在有两条已跑通的链路：`commitment.bet_record`（记录，停在 COMMITTED）与
  `commitment.bet_execute`（执行到 CLOSED）。事件定义 137 条，其中 `commitment.*` 5 条。
- 当前闭环的真实结果：`supported`，且 `expectations_met=True` 而
  `improvement_over_baseline=False` ⇒ `supported_claim=absolute_attainment`；发布被
  `environment_not_publishable:isolated_sample` / `isolated_sample_only` / `single_episode_only`
  诚实阻塞。
- **这不是科学假设检验**：动作是文本统计，不含任何科学内容。它只证明“机制”成立。

## 已知陷阱（都在本轮真实踩过）

1. 新增 `EventDefinition` 的 series 必须先注册进 `EVENT_SERIES`，否则 **flow 启动即崩**：
   `checkpoint_crashed: ValueError: unknown Event series: <name>`（已有先例
   `partner/observe/precedents.py` `case_05_event_series_unknown`）。
2. bet 与 baseline 必须声明**同一个 `environment_fingerprint`**，否则比较不可采纳、结算退化为
   `inconclusive`（blocker 表现：`control_mismatch:environment_identical`）。spec 里漏设字段就会踩到。
3. budget 必须为“冻结检查”留额度：`_walk` 第一步就 `may_spend(model_calls=1)`，`model_calls=0`
   会让记录节点直接 `BUDGET_EXHAUSTED`。
4. baseline 臂会改写 `bet_id`（`<bet_id>__baseline`），**任何按 id 推导路径的读取都会失效**；
   执行器必须优先用 `context_snapshot_ref`（绝对路径）。
5. 有界 worker 会对同一个 Job 反复 check-point（本轮实测：worker 层 20 次重入、timeline 上
   **247 条 `claim` 行**）。因此 commitment 节点**必须幂等**——`freeze_only()` 幂等、`run()` 对终态走
   `_replay_result`，所以 247 次重入只产生 **1 份 settlement 与 1 份 experience**。这是既有 flow 的
   检查点语义问题，不是本接入引入的，但值得后续单独治理。

## 不做的事（边界）

不新造消息系统；不复活 `desktop_inbox.jsonl`；不扫描或领取历史 Job（本轮运行前后 269→270，
旧记录逐条 status/updated_at 不变）；不接生产；不跑分子实验、不生成 PDF、不发 QQ；不改 commitment
内核既有语义（`freeze_only` 与续跑路径是**加性**改动，141 项原有内核测试保持全绿）。

## 验证

| 项 | 值 |
|---|---|
| root Job | `job_70d0745302684b70`（instance 02，`project_iteration` 2.6.0） |
| trace token | `runtime_trace_commit_02_1789812520` |
| BetRecord | `bet_job_70d0745302684b70`（revision 1，env `isolated_sample`） |
| settlement | `stl_bet_job_70d0745302684b70_r1` → `supported` |
| ExperienceRecord | `exp_bet_job_70d0745302684b70_r1`（level=experience，authoritative=False） |
| lifecycle | `CLOSED`，`settled=True`，`experience_emitted=True` |
| timeline | seq 2604 `commitment_bet_recorded`、seq 2605 `commitment_bet_settled`（均 `running -> running`），两行都带 token |
| Job DB | 269 → 270，旧 269 条逐条不变，仅新增该 root Job（无子 Job） |
| 测试 | `tests/runtime/` 19 passed；`tests/commitment/` 141 passed；事件定义 137；`git diff --check` clean |

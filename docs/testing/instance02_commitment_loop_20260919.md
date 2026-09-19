# 02 Event 流内 commitment 闭环验收（2026-09-19）

## 结论

**通过。** 实例 02 通过正式 Event 流消费消息后，Event 流在 `commitment` 节点记录 BetRecord、
在 `commitment_execute` 节点把它从 **COMMITTED 推进到 CLOSED**：执行一个有界确定性动作、
独立测量、与冻结预期比较、机器结算，并产生 ExperienceRecord。

## 关键 ID

| 项 | 值 |
|---|---|
| trace token | `runtime_trace_commit_02_1789812520` |
| instance | `02` |
| root Job | `job_70d0745302684b70`（status=completed） |
| Flow | `project_iteration` 2.6.0（本次由 02 自身意图路由选中；`direct_answer` 1.2.0 同样含该对节点） |
| BetRecord | `bet_job_70d0745302684b70` |
| settlement | `stl_bet_job_70d0745302684b70_r1` |
| ExperienceRecord | `exp_bet_job_70d0745302684b70_r1` |
| receipt / measurement / baseline | `rcpt_bet_job_70d0745302684b70_r1` / `meas_…_r1` / `base_bet_job_70d0745302684b70` |

## 闭环结果

```
lifecycle      : CLOSED   settled=True   settled_class=supported
               experience_emitted=True
settlement     : supported | expectations_met=True | improvement_over_baseline=False
publish_eligible: False
publish_blockers: ['environment_not_publishable:isolated_sample',
                   'isolated_sample_only', 'single_episode_only']
experience     : exp_bet_job_70d0745302684b70_r1 | level=experience | authoritative=False
token_in_bet   : True
timeline       : create → commitment_bet_recorded → commitment_bet_settled → save
```

`expectations_met=True` 而 `improvement_over_baseline=False`：绝对阈值达成、相对 baseline 未改善，
因此 `supported_claim=absolute_attainment` —— 这正是"达标 / 相对改善 / 是否值得继续"三概念拆分
在真实运行中的表现。发布被诚实阻塞（隔离环境 + 单次证据）。

## 有界动作（非分子实验）

- **动作**：对消息做确定性文本统计（`text_len` / `word_count` / `unique_words` /
  `unique_word_ratio`），写入真实产物 `text_stats.json`（带 sha256）。
- **control**：`identity`；**treatment**：`lowercase`（唯一且已冻结的处理差异）。
- baseline 由同一个执行器真实执行 control 臂并用同一评测器测量，落为 `BaselineEvidence`
  （`fresh_execution` + 不可变哈希）。
- 环境 `isolated_sample`：结构性不可发布。

## 本轮改动

1. **`partner/commitment/runner.py`**（内核，加性）：
   - 抽出 `_freeze_bet()`，供 `_walk` 与新的 `freeze_only()` 共用一条代码路径
     —— 这保证"记录"与"执行"两次冻结的语义逐字节一致；
   - 新增 `freeze_only()`：只冻结并记录 bet（停在 COMMITTED），不执行任何动作；
   - `_walk` 支持**从已冻结的 bet 继续**：状态为 COMMITTED/EXECUTING/MEASURED/SETTLED 时不再
     重新冻结（那会触发非法的 COMMITTED→PROPOSED），而是从持久化记录恢复 candidates/selection
     后继续执行、测量、结算。这就是 COMMITTED → SETTLED 的机制。
2. **`partner/application/commitment_bounded_adapter.py`**（新）：非分子有界动作的执行器、
   确定性评测器、baseline 提供者，以及 `bounded_spec` / `write_bounded_snapshot` /
   `build_bounded_runner`。
3. **`partner/events/commitment.py`**：`commitment.bet_record` 改为走 `freeze_only()`；
   新增 `commitment.bet_execute`（执行 + 独立测量 + 机器结算 + ExperienceRecord），
   两者共用 `_prepare_bounded` 以保证 spec 一致。
4. **`partner/event_flows/builtins.py`**：`direct_answer` 1.2.0 与 `project_iteration` 2.6.0
   各加 `commitment` + `commitment_execute` 节点；1.0.0 / 1.1.0 / 2.5.0 均保留可解析。
5. **`partner/runtime/shared_worker.py`**：有界循环在指定 root Job 到达终态时立即停止。

## 失败与修复记录

| # | 失败点 | 原始错误 | 分类 | 修复 |
|---|---|---|---|---|
| 1 | 记录节点报 bet 不存在 | `StoreIntegrityError: no bet.json recorded` | wiring | budget `model_calls=0` 使 `may_spend(model_calls=1)` 直接 BUDGET_EXHAUSTED；给冻结检查留额度 |
| 2 | 执行节点无法重新冻结 | `IllegalTransition: refusing COMMITTED -> PROPOSED` | 接口缺失 | `_walk` 支持从已冻结 bet 恢复继续（本轮核心改动） |
| 3 | baseline 不可采纳 | `NotIndependentError: receipt does not name the declared artifact` | wiring | baseline 臂改过 bet_id 导致按 id 推导的 snapshot 路径失效；改用 `context_snapshot_ref` |
| 4 | 结算 inconclusive | `control_mismatch:environment_identical` | wiring | spec 未设 `environment_fingerprint`，bet 与 baseline 指纹不一致；统一 |
| 5 | 节点完全没被走到 | job 的 `flow_type=project_iteration`，不含 commitment 节点 | **接口缺失（重要）** | 路由由 02 自身意图判定，可能选 direct_answer 也可能选 project_iteration → 两条 flow 都加该对节点 |
| 6 | 记录节点 ok 判定错误 | `ok=False` 而状态为 COMMITTED | wiring | 把 COMMITTED 视为"已记录"成功 |

## 测试

```
PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider tests/runtime/     # 19 passed
PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider tests/commitment/  # 141 passed
python3 -c "from partner.events import builtin_definitions; print(len(...))"  # 137
git diff --check                                                          # clean
```
`tests/runtime/test_commitment_event_binding.py` 新增 5 项：两条 flow 都可达 commitment 节点且旧版本
仍可解析；两个 handler 把同一 bet 从 recorded 推到 settled（只产生 1 份 settlement 与 1 份 experience，
timeline 两行都不改状态）；重放不产生新结算；`freeze_only()` 后 `run()` 是**续跑**而非冻结字段编辑
（freeze_hash 与 revision 不变）；有界动作确定性且可比。

## Job DB 前后差异

| | 值 |
|---|---|
| 运行前 | queued 140 / running 20 / completed 75 / failed 34 = **269** |
| 运行后 | 旧 269 条 status 与 updated_at **逐条不变**（changed_old=0） |
| 新增 | 仅 `job_70d0745302684b70`（无子 Job） |

## 遗留与边界

- 三个承诺内核事实保持不变：未修改承诺语义/既有测试/证据结构（`freeze_only` 与恢复路径是加性改动，
  141 项原有测试全绿）。
- 有界 worker 会**多次 check-point 同一个 flow**（本次 worker 层 20 次重入 / 81.8s；该 Job 的
  timeline 上共有 247 条 `claim` 行）：这是既有 flow 的
  checkpoint/等待语义，不是 20 个 bet——同一 bet 只有 1 份 settlement 与 1 份 experience。
  这是效率问题，不是正确性问题，但值得后续优化（例如 flow 内的等待节点不应触发整循环重入）。
- 本轮消息被 02 自己的路由判为 `project_iteration`，因此该 flow 的其它节点（inspect/plan/execute/
  reflect 等）也按其原有语义执行了。这不在本轮改造范围内，但说明：**只要消息被路由到任一 flow，
  commitment 节点就会跑到**，这是设计意图。
- 未做分子实验、未生成 PDF、未发 QQ、未改 legacy 语义、未扩 benchmark、未接生产、未碰
  140 queued / 20 running 历史 Job。

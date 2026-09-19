# 经验消费轮：同类历史 settlement 改变本次决策（2026-09-19）

## 结论

**通过。** 02 在同一次 Event 流里先读取同类历史的 settlement/ExperienceRecord，再由**确定性
纯 Python 规则**改动了本次 bet 的决策变量，改动的**前后值可审计**且被冻结进 bet；反事实（禁用
prior 节点）让决策变量回到第一次的值；第二次运行的回复正文与 S2 **逐字段一致**。

## 第一步：同类判据（纯函数，无 LLM）

```python
def task_class_key(*, project_id: str, action_id: str, metric_signature: str) -> str:
    """cls_ + sha256("|".join([project_id, action_id, metric_signature]))[:16]"""
```

- `action_id`：真实任务为 `real_task:<task_id>`，文本统计动作为 `bounded_metric`
- `metric_signature`：声明的预期按 `metric:kind:direction` 排序拼串，本例
  `tests_passed:delta_over_baseline:increase`
- **flow_type 刻意不入键**：意图路由是实例自己的判断，不该把类劈成两半；消息正文与 trace token
  也从不参与
- **落点**：键写进冻结的 bet —— `data_version = "class:<key>"`，任何后续回合只读 `bet.json`
  即可检索，不需要分类器
- 实现：`partner/application/experience_prior.py::task_class_key` /
  `class_key_from_bet`；本轮的类 `cls_fb8b1f2ced4bec6a`

## 第二步：prior 读取节点（只读，可审计）

新节点 `experience_prior`（事件 `commitment.prior_recall`）插在 `understanding_3` 之后、
`commitment` 之前；`commitment` 的依赖改为它，保证 prior 一定先于冻结写定。

- 只扫 `state/commitments/*/*/{bet.json,state.json,settlement/*,experience/*}`，**只取同类**；
  没有 settlement 的目录不是证据，跳过而不是补一个值
- 空 prior 显式记 `prior_adjusted=false` / `reason=prior_empty`，**不填任何默认值**
- 输出落进 flow 的 node outputs 与 event ledger（`commitment_prior_recalled` 审计行）；唯一的
  写操作是 `context/prior.json`（在**本次** bet 的 store 内），历史记录**零写入**（有哈希不变的测试）
- 读侧代码：`scan_settled_bets` / `select_same_class` / `summarize_prior`

## 第三步：确定性调整规则（全部纯 Python）

`adjust_declared(declared, prior) -> (adjusted, audit)`，按顺序：

| # | 条件（来自 prior 的机器计数） | 动作 |
|---|---|---|
| 1 | 有 `refuted` | `min_delta = base * (1 + refuted)`（提高门槛） |
| 2 | 否则有 `supported` 且 base > 0 | `min_delta = max(0.25, base / 2**supported)`（降低门槛，地板 0.25） |
| 3 | 有 `improvement_over_baseline=False` | 强制 `require_baseline_rerun=True` |
| 4 | 有 `single_episode_only` 阻塞 | `replicates = 2`，且候选臂**真的执行两次** |

每条规则都产出 `{name, before, after, rule, evidence, why}`，汇总为 `prior_adjusted_parameter`。
**没有任何一处由 LLM 决定怎么调。**

## 第四步：三次真实运行（同一类，真实任务，真实测试运行器）

任务 `fix_dgm_lineage_contract`：`partner/research/adapters/dgm.py::lineage_edges` 的签名与契约
不符（真实失败：`TypeError: lineage_edges() missing 1 required positional argument: 'run_id'`），
control 1 passed/1 failed → candidate 2 passed/0 failed（delta = +1，外部不可自生成）。

| | A（同类第一次） | B（同类之后） | C（反事实 prior=off） |
|---|---|---|---|
| trace token | `expA_trace_02_1789825001` | `expB4_trace_02_1789830001` | `expC_trace_02_1789827001` |
| root Job | `job_806c0be25a604619` | `job_1b6ab8b77ef640e0` | `job_d5e45c4871644953` |
| prior 条数 | **0**（扫了 32 条已结算 bet，同类 0） | **6**（同类：supported 6 / improvement_true 6 / single_episode_only 3） | **0**（节点被禁用；同类其实已有 3 条） |
| min_delta | **1.0**（声明值） | **0.25**（1.0 → 0.25） | **1.0**（= A 的值） |
| replicates | **1** | **2**（候选臂真的跑了 2 次，`replicates_executed=2, agreed=True`） | **1**（= A 的值） |
| `prior_adjusted` | False / `prior_empty` | **True**，两条规则均触发 | False / `prior_empty` |
| settlement S | S1 supported，`delta=1.0>=1.0` | S2 supported，`delta=1.0>=0.25` | supported，`delta=1.0>=1.0` |
| publish_blockers | 含 `single_episode_only` | **不含** `single_episode_only` | 含 `single_episode_only` |
| Job 终态 | failed（见下"已知限制"） | **completed**，回复行 1 条 | completed，回复行 1 条 |

- **第二次的决策变量与第一次不同**：`min_delta 1.0 → 0.25`、`replicates 1 → 2`，且规则/证据都在
  `prior_adjusted_parameter` 里逐条可查
- **与 trace token 内容无关**：类键只由 (project_id, action_id, metric_signature) 决定；把 B 的
  token 换成任何字符串，调整结果不变（有纯函数测试）
- **反事实验证**：C 的类历史非空（3 条），但禁用新节点后决策变量**回到 A 的 1.0 / 1**
- A 的 flow 在**结算之后**挂在既有易挫节点 `message_critic`（项目自带的先例
  `case_02_message_critic_reject_realtime`），因此 A 没有回复行；S1 与冻结值都完整

## 第五步：内核裁决 → 实例叙述接通

新节点 `commitment_reconcile`（事件 `commitment.reply_reconcile`）插在 `deduplicate` 之后、
`channel`/`send` 之前；`delivery.send_text` 优先采用它的正文：

- 有 settlement → 回复正文以 settlement 为事实来源，逐字段写成
  `settlement_class=… improvement_over_baseline=… supported_claim=… publish_eligible=… publish_blockers=…`，
  外加 `bet=/settlement=/expectations_met=`
- 草稿与裁决矛盾（声明的字面 token 判定，非 LLM）→ 丢弃草稿表述、以裁决为准、把矛盾与命中词写进
  event log（`commitment_reply_contradiction`）与 `reply_reconciliation.json`
- 不矛盾的草稿会附在下面，但**显式标注「流内草稿·未经裁决背书」**，不会被误当裁决背书
- B4 那一行正文与 S2 的逐字段对照：8/8 全部命中；且
  `reply_reconciliation.json.body_digest == sha256(实际发送正文)` = **True**（审计链自洽）

## 测试（要求项 → 实现）

`tests/runtime/test_experience_prior.py`（17）+ `tests/runtime/test_reply_reconciliation.py`（6）：

1. 同类判据是纯函数、不依赖 LLM（含"token 换了键不变"）
2. prior 只在同类里检索（另造一个异类 bet 不会被读进）
3. prior 为空 → 不调整，且显式 `prior_empty`
4. prior 非空 → 按规则调整，before/after 可审计
5. 规则覆盖：历史 refuted / 历史 supported / 历史 improvement=False / 历史 single_episode_only
6. 反事实：禁用 prior 节点 → 决策变量回到声明值（`prior_adjusted=false`）
7. 读历史不改写历史（前后哈希逐一相同）
8. 错类的残留 prior 不泄漏进本次 bet
9. send 节点在有 settlement 时以 settlement 为准（**用 flow 的真实输出形状**测，正是它抓出了接线 bug）
10. 矛盾时以裁决为准、草稿不被回显、矛盾写进 timeline
11. 工件必须写出且摘要自洽（失败要显式暴露，不许静默吞）
12. 影子仓库 sibling 回归（见下）

```
PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider tests/runtime/       → 61 passed, exit 0
PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider tests/commitment/   → 141 passed, exit 0
python3 -c "from partner.events import builtin_definitions; print(len(...))"  → 139
git diff --check                                                          → exit 0, clean
```

## 本轮发现并修掉的 4 个真实缺陷（都不是"顺手加功能"）

1. **影子仓库 sibling 缺失**（上一轮的 harness 缺陷）：`stage_shadow_repo` 只软链了目标路径的各级
   *祖先*，没链目标文件**所在目录**的其他条目 → 目标包里只有被替换的那一个文件，任何 import 同包
   兄弟模块的测试都会 `ModuleNotFoundError`，把 baseline 从 1 failed/1 passed 变成 2 failed。
   本轮任务恰好 `-k lineage` 命中两条测试才暴露。已修 + 回归测试。
2. **`send_text` 读不到裁决正文**：flow 把 handler 的**整个返回 dict**存在节点名下（正文在
   `semantic_output` 里），而它只在顶层找 `settlement_message` → 静默丢掉裁决块、只发草稿
   （run B 第一次实测：正文里没有 settlement 字段）。已修（两种形状都认）+ 测试改用 flow 真实形状。
3. **`mentions` 在使用后才赋值**：裁决工件写入抛 `NameError`，被裸 `except` 静默吞掉 → handler 报
   的 files 路径下根本没有文件。已修（提前计算 + 失败写进 `artifact_error`、不再静默）。
4. **正文重复拼接**：`send_text` 在 `draft_replaced=False` 时把草稿**又拼了一遍**（裁决正文里已经
   含它），导致实际发送正文与 `reply_reconciliation.json` 的 `body_digest` 不一致。已修，且测试断言
   两者逐字节相等。

## Job DB 差异

```
302 → 309 行
旧 Job 的 (status, updated_at) 逐条不变：changed = 0（每个 driver 都做了 before/after 快照比对）
新增 7 个 root Job（本轮的 A/B/C 三角色 + 因真实缺陷与既有 critic 偶发导致的重试）：
  job_806c0be25a604619 (A, failed@message_critic)  job_a4e58ab0258e4f50 (B 重试, completed)
  job_e7d28ed50cc6411b (C 重试, completed)         job_d5e45c4871644953 (C, completed)
  job_8cc6fda05fcc47a7 (B 重试, completed)         job_5a7078e79e1b4941 (B 重试, completed)
  job_1b6ab8b77ef640e0 (B 最终, completed)
未碰任何历史 queued / running Job；未复活 desktop_inbox；未建第二套消息系统
```

## 仍然存在的限制（如实）

- 类判据是"声明字符串的哈希"：同项目 + 同动作 + 同指标形状即同类，不做语义相似度（刻意的，因为
  不能依赖 LLM）。换 task_id 就是另一个类。
- `min_delta` 的地板 0.25 使信号在连续多次 supported 后**饱和**（本例 6 条 → 1/64 被地板截住）。
  想继续区分就得引入新的决策变量，而不是把地板降下去。
- `replicates=2` 的两次执行是**同一机器上的两次独立进程**（同一影子仓库机制），不是独立实例；结算里
  `isolated_sample_only` / `environment_not_publishable:isolated_sample` 仍在，**没有任何东西变可发布**。
- 星内草稿是 LLM 产物，可能整段与任务无关（本轮就幻出一段分子 pK 分析）；它只作为**明确标注的未背书
  草稿**附在裁决之后，裁决部分才是机器生成的事实。
- A 的 flow 挂在既有的 `message_critic`（先例 case_02，LLM 审查硬规则偶发拒绝），发生在**结算之后**，
  与本轮机制无关；本轮**未修**（不修 legacy 语义）。
- 跑 C/B 之间命中过一次既有的索引层崩溃（`write_request_receipts` →
  `stream_projection.summaries` → `sqlite3.OperationalError: Could not decode to UTF-8 column
  'payload'`），发生在 Job 到达终态**之后**，只影响 worker 收尾；按本轮规则**未做加固**。
- prior 读取是全量扫 `state/commitments`（O(已结算 bet 数)），随历史增长会变慢；本轮 32 条无感。

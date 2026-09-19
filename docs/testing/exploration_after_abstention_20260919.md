# 弃权后的有界探索：新证据如何回到同类决策（2026-09-19）

## 结论

**通过。** 结算为 ABSTAINED 后 02 **自动**触发一次有界探索：走正式 Application Service 提交一个
新任务，类键与触发它的弃权任务**完全相同**，只尝试一个**历史未尝试过的动作变体**，预算独立且只有
声明值的 1/4，单动作、无多候选选择。探索结算作为普通同类证据落进统计，被下一个同类 prior 节点读到，
并把后续赌注的决策从"弃权"变成"真实下注"。`explore=off` 时弃权不探索（`explore_skipped /
explore_disabled`），窗口上限/待结算互斥/无变体拒绝都按常量执行。

## 探索触发条件（常量，纯 Python）

`partner/commitment/exploration.py`：

```python
EXPLORE_BUDGET_RATIO    = 0.25   # 探索预算 = 声明预算的 1/4（独立，不侵蚀弃权赌注的预算）
EXPLORE_MIN_ACTIONS     = 2      # 下限：一次有界动作需要两个臂（control + candidate）
EXPLORE_MIN_ROUNDS      = 1
EXPLORE_MIN_MODEL_CALLS = 1
EXPLORE_MAX_PER_WINDOW  = 1      # 每个类每个窗口最多 1 次
EXPLORE_WINDOW_SECONDS  = 7200
EXPLORE_TOKEN_PREFIX    = "explore_trace_"   # 声明形状 explore_<class>_<ts>，且必须含 "trace"
EXPLORE_CHANNEL         = "log"               # 投递层白名单内的渠道；身份在 sender_id 前缀

def should_explore(*, enabled, budget, explored_in_window, pending, variant, max_per_window)
判定顺序（原因可复现）：explore_disabled → no_budget → explore_already_pending →
window_limit → explore_no_variant_available → 否则 exploration_triggered
```

变体来源（无 LLM）：`task.json::candidate_space` 声明的真实补丁列表；
`choose_variant(candidates, tried)` 取声明顺序里第一个 `selected_action` 不在该类已结算历史中的变体；
`tried_actions` 用扫描行的 `selected_action`（= `patch_<stem>`）。无可用变体 → **不探索**，记
`explore_no_variant_available`。

## 完整链路（真实运行）

任务 `explore_after_abstention_probe` / `_off`（两个新类）；变体 `patch_partial.diff`（真实部分修复）
与 `patch_full.diff`（真实完整修复）；未修改模块 3 passed / 4 failed，partial 4/3，full 6/1；
声明 threshold=6.0、min_delta=2.0（同一类键，变体只改具体动作）。

| run | root Job | selected_action | settlement | met | class evidence | abstain |
|---|---|---|---|---|---|---|
| H1       | job_03034d8e4d2b41e2   | patch_patch_partial | falsified | False     | falsified=0 supported=0 abstained=0 | abstain=False |
| H2       | job_b99d25587f7f4f61   | patch_patch_partial | falsified | False     | falsified=1 supported=0 abstained=0 | abstain=False |
| A        | job_a1a58ccefb72473b   | abstain        | abstained | False     | falsified=2 supported=0 abstained=0 | abstain=True |
| EXPLORE  | job_190dda71759446d9   | patch_patch_full | supported | True      | falsified=2 supported=0 abstained=1 | abstain=False |
| C        | job_8332756899fe48e9   | patch_patch_partial | falsified | False     | falsified=2 supported=1 abstained=1 | abstain=False |
| H3       | job_b80b7906ee9d4596   | patch_patch_partial | falsified | False     | falsified=0 supported=0 abstained=0 | abstain=False |
| H4       | job_96edfe067c084137   | patch_patch_partial | falsified | False     | falsified=1 supported=0 abstained=0 | abstain=False |
| A_off    | job_69f7c2df21df41b8   | abstain        | abstained | False     | falsified=2 supported=0 abstained=0 | abstain=True |
| H5       | job_4cc531353ac540c0   | patch_patch_partial | falsified | False     | falsified=2 supported=0 abstained=1 | abstain=False |
| A2       | job_3d34d44c5b6a4fae   | abstain        | abstained | False     | falsified=3 supported=0 abstained=1 | abstain=True |
| EXPLORE2 | job_ca0169f134754172   | patch_patch_full | supported | True      | falsified=3 supported=0 abstained=2 | abstain=False |

- **弃权（trace A）**：`job_a1a58ccefb72473b`，类 `cls_454d3fd77f88e0e0`，`abstain_reason` 见赌注
  冻结记录；`selected_action = "abstain"`，`rejection`：2 条同类 falsified / ratio 1.0 / supported 0 /
  尾部 falsified。
- **探索触发**：事件日志 `commitment.explore_triggered`（job `job_a1a58ccefb72473b` 的 timeline），
  探索 root Job **`job_190dda71759446d9`**，trace token `explore_trace_cls_454d3fd77f88e0e0_1789834337`，
  变体 `full_contract`（tried = ['patch_patch_partial']），
  声明的变体池 ['partial_propose', 'full_contract']，
  预算 声明 {"wall_clock_seconds": 600, "model_calls": 1, "actions": 4, "rounds": 1} →
  探索 {"wall_clock_seconds": 150, "model_calls": 1, "actions": 2, "rounds": 1, "ratio": 0.25}，
  窗口 max/len = 1/7200s，
  pending=0，explored_in_window=0。
- **探索执行（第一次，trace token `explore_trace_cls_454d3fd77f88e0e0_1789834337`）**：
  root Job `job_190dda71759446d9`，变体 `full_contract`，预算 150s（=600×0.25，动作数 2、轮次 1、
  模型调用 1），结算 **supported**（expectations_met=True，baseline 3 → observed 6，threshold 6.0；
  improvement_over_baseline=False → claim=absolute_attainment）。该赌注自身被抑制弃权
  （`blocked_by=['exploration_bet_declines_no_wager']`，prior 记录 `would_have_abstained=true`）。
  它的 **flow** 后来因渠道缺陷（缺陷 3）终态 failed —— 结算与后续读取不受影响，未回写记录；
  修复后第二次探索（EXPLORE2，log 渠道）job 正常 **completed** 并产出 log 回复行。
- **第二次探索（同一链条在反事实类上重跑，验证渠道修复）**：A2 弃权
  （falsified 3 / ratio 1.0 / supported 0）→ 自动触发 root Job `job_ca0169f134754172`
  （trace token `explore_trace_cls_e4a85de614cac5c3_1789835974`，父赌注 `bet_job_3d34d44c5b6a4fae`，
  tried=['abstain','patch_patch_partial'] → 选中未尝试变体 `full_contract`），
  冻结审计（快照 `exploration_audit`）含 schema/class/parent_bet/trigger_reason/tried_actions/
  variant/budget/window；结算 **supported**（met=True），job completed，回复行 1 条。
- **后续决策（trace C）**：`job_8332756899fe48e9` 读到同类 4 行
  （falsified 2 /
   supported 1 /
   abstained 1），
  refuted 比例 1.0 → 0.6667，弃权条件被打破（blocked_by=['refuted_ratio 0.6667 < 0.75', 'supported 1.0 > 0 (success evidence exists)', 'latest settlement is supported, not falsified']），
  `selected_action` 由 abstain 变为 **patch_patch_partial**（真实下注）。

## 反事实验证（explore=off）

```
A_off  job_69f7c2df21df41b8  结算 None（弃权仍然成立）
       timeline: commitment.explore_skipped, reason=explore_disabled
       探索渠道 job 数 = 2（只有真实那一条）
```

## 有界性证据

```
预算独立：弃权赌注 frozen budget = {"actions": 4, "deadline_epoch": 1789834921.873598, "model_calls": 1, "rounds": 1, "schema_version": "commitment/2", "started_epoch": 1789834321.873598, "wall_clock_seconds": 600}
          探索赌注 frozen budget = {"actions": 2, "deadline_epoch": 1789834673.8656454, "model_calls": 1, "rounds": 1, "schema_version": "commitment/2", "started_epoch": 1789834523.8656454, "wall_clock_seconds": 150}
窗口上限：EXPLORE_MAX_PER_WINDOW=1；测试覆盖 window_limit / explore_already_pending / no_budget
无变体：测试覆盖 explore_no_variant_available（不新建探索赌注、不伪造变体）
渠道：探赜走 log（投递白名单内），身份在 sender_id = commitment_explore:<class_key>
探索 job 记录：[["job_190dda71759446d9", "failed", "explore", "commitment_explore:cls_454d3fd77f88e0e0"], ["job_ca0169f134754172", "completed", "log", "commitment_explore:cls_e4a85de614cac5c3"]]
Job DB 行数：339
```

## 测试（本轮新增 16 项，共 248 项）

`tests/runtime/test_exploration_after_abstention.py`：
```
常量与判据；预算是 1/4 且不侵蚀声明预算；无预算/窗口/待结算/无变体/关闭各自的拒绝原因；
变体选择（声明顺序第一个未尝试；全试过 → None；池空 → None；变体不参与类键）；
窗口只数该类且只数窗口内；tried_actions 稳定排序；
事件层闭环（弃权 → 自动提交一次探索：sender_id/route/请求里的 real_task+patch=未尝试变体+explore=on+
explore_parent+explore_tried；事件日志记 explore_triggered；旧 Job 未被改动；只新增探索 Job）；
反事实 explore=off；窗口上限；无变体拒绝；待结算不重复提交；
证据回流（探索结算被下一次 prior 读到：计数 +1、supported 1、refuted 比例下降、阻塞原因变为 supported、
      下一个赌注由 abstain 变为真实动作）；
探索赌注自身不得弃权（suppressed_for + would_have_abstained + 预算 150s/2 actions）；
渠道在投递白名单内；同类/相似类判据回归。
```

## 本轮发现并修掉的 3 个真实缺陷

```
1 _prepare_bounded 没有把探索字段带出去（variants/explore_disabled）⇒ execute 节点看到"无变体"，
  真实触发会永远以 explore_no_variant_available 收场（测试直接抓到）
2 声明的 token 形状 explore_<class>_<ts> 不含字面 "trace"，而仓库 _TRACE_RE 只认含 "trace" 的标识符
  ⇒ 探索任务自己的 flow 会以 "no trace token found" 失败。修：前缀 explore_trace_<class>_<ts>
3 探索用私有渠道名 channel="explore" ⇒ 投递层 channel_route 白名单不含它，探索 flow 在自己的 send
  节点失败（"unsupported delivery channel"，结算之后）。修：探索走 log 渠道，身份改由
  sender_id=commitment_explore:<class_key> 承担 + 加白名单测试。首条探索 job 的渠道已持久化，
  未改记录（保持 failed），其结算与后续读取不受影响；修复由第二次探索端到端验证
```

## 仍然存在的限制

```
· 变体必须由任务预先声明为真实补丁；没有声明池就没有探索（拒绝，不伪造）
· 每个类每窗口 1 次且待结算互斥：探索失败不会立即重试（等下一条新证据或下一个窗口）
· "无可用变体"时没有可跑的赌注，因此不存在 blocked 结算；弃权保持终态、原因记入事件日志
· 探索预算线性折算，动作数下限 2（一次动作=两臂），更小的预算不会执行半个动作
· 探索结果不被保证为 supported：本轮完整变体实测满足声明门槛（6 >= 6），是测量结果而非编排
· 首条探索 job 的 flow 因渠道缺陷终态 failed（结算已完成）；未回写记录
· 既有 message_critic 偶发与索引层 UTF-8 崩溃按本轮范围未修

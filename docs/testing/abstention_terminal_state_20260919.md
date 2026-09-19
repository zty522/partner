# 弃权：ABSTAINED 作为独立终态（2026-09-19）

## 结论

**通过。** 同类历史"反复失败且没有成功证据"时，02 选择**不下注**：走完整 commitment 闭环、
结算为 **ABSTAINED**（内核正式注册的新终态，不是 blocked / inconclusive 的变体）、
`replies.log` 有回复行且正文写明"本次选择不下注"与证据；弃权**可由后续 supported 证据打破**
（同一类里引入真实成功结算后不再弃权）；`prior=off` 时不弃权（反事实成立）。

## 第一步：弃权规则（纯函数，阈值是模块常量）

```python
ABSTAIN_MIN_EVIDENCE = 2        # 至少 2 条"带证据"的同类结算
ABSTAIN_REFUTED_RATIO = 0.75    # 其中 falsified 占比 >= 0.75
ABSTAIN_RULE = "abstention_rule_refuted_history_without_success"

def decide_abstention(prior: Mapping[str, Any]) -> dict[str, Any]
    # 返回 {"abstain", "reason", "blocked_by", "rule", "evidence"}
```

四个条件**全部成立**才弃权：

1. `evidence_total >= ABSTAIN_MIN_EVIDENCE`（一次失败不是趋势）
2. `falsified / evidence_total >= ABSTAIN_REFUTED_RATIO`
3. `supported == 0`（同类里没有任何成功证据）
4. **最近一条**同类结算为 `falsified`

第 4 条同时承担"不能连续弃权"：若尾部已经是弃权，说明该类上次已拒绝下注、其后没有新证据 →
不再弃权（有新 falsified 涌入则允许再次弃权）。**弃权本身不计入比例分母**：不下注不是关于任务
的证据，既不该抬高也不该压低失败率。

## 第二步：弃权的闭环语义

- **内核注册**：`partner/commitment/models.py::SETTLEMENT_CLASSES` 增加 `"abstained"`（与内核现有
  五个终态并列）；新增 `BLOCKER_ABSTAINED = "abstained"`；`ExperienceRecord.level` 允许
  `"abstention"`（`LEVEL_ABSTENTION`），habit/growth 依旧被拒。
  语义独立性由数据结构强制，不靠叙述：
  - `'abstained'` 不允许携带任何 `expectation_outcomes`
  - `expectations_met / improvement_over_baseline / baseline_already_satisfied` 必须为 False
  - `supported_claim` 必须为 `"none"`；`publish_eligible` 必须为 False
  - `state_machine`：弃权**必须**携带"未测量"（`validity != valid`），携带有效测量直接拒绝
  - `policy.post_settlement_decision` 为 `abstained` 单列分支（不再落到 inconclusive 文案）
- **冻结 bet**：`selected_action="abstain"`，`candidate_id="abstain"`，理由与 prior 证据写进 bet 的
  上下文快照（其摘要即 `context_snapshot_hash`，审计被密码学绑定）
- **不执行任何动作**：不改用 LLM 提议者、不生成补丁、不跑 baseline/candidate、不读指标
  （`partner/commitment/runner.py::BetRunner.abstain()`）
- **唯一产物**：`abstention/abstention.json`（决定 + 证据 + `executed={model_calls:0, actions:0,
  arms_run:0, patch_proposed:false, metric_read:false}`）；receipt 声明
  `requested_action=executed_action="abstain"`、`budget_consumed` 全 0
- **结算**：`partner/commitment/abstention.py::settle_abstained` 构造 ABSTAINED 决策，
  machine_rules 里逐条记录 `abstain_reason=` 与 `abstain_evidence[...]`
- **经验**：`build_abstention_experience` → `level="abstention"`、`confidence=0.5`、不可晋升
  （`assert_promotable` 要求 supported）
- **可被下一轮 prior 读到**：结算落在同一 store，`scan_settled_bets` 直接计数为 `abstained`
- **回复**：`commitment.reply_reconcile` 以裁决为准重写正文（"本次选择不下注，原因如下：…"+
  "不下注证据：…"）；任何声称"已取得进展"的草稿在弃权时被判为矛盾并丢弃

## 第三步：一条真实弃权

```
trace token   abstABb1_trace_02_1789851001
root Job      job_56cc31d9bbcd43b4          （类 cls_95bc4202aeeea132）
prior 证据    total=10 / falsified=7 / supported=0 / abstained=3，evidence_total=7，ratio=1.0
              refuted_bet_ids 全部来自真实结算（上一轮 3 条 + 本轮反事实/补跑）
abstain_reason  same-class history is 7/7 refuted (ratio 1.0 >= 0.75) with no supported
               settlement, and the latest settlement (bet_job_0cb2708ddb804951) is falsified:
               the harness declines to wager again without new evidence
结算 S        settlement_class = abstained | expectation_outcomes = [] | expectations_met=False
              improvement_over_baseline=False | supported_claim=none | publish_eligible=False
              publish_blockers=["abstained"] | state=CLOSED | experience level=abstention
回复行        1 条（replies.log）：
  [commitment] settlement_class=abstained publish_eligible=False publish_blockers=abstained
  bet=bet_job_56cc31d9bbcd43b4 settlement=stl_bet_job_56cc31d9bbcd43b4_r1
  本次选择不下注，原因如下：same-class history is 7/7 refuted (ratio 1.0 >= 0.75) ...
  不下注证据：abstain_evidence[class_key]="cls_95bc4202aeeea132"
  不下注证据：abstain_evidence[falsified]=7 …
  （本次未执行任何动作：未生成补丁、未调用 LLM、未测量；expectations_met /
   improvement_over_baseline 一律不作声明）
```
（本轮先后出现 4 条真实弃权：AB1/AB2/AB3/AB-B1，token 见 Job 表；AB3 的 flow 被 worker 到期打断，
收尾后同样产出弃权回复行。）

## 第四步：弃权被后续证据打破

```
trace token   abstREV_trace_02_1789838001
root Job      job_022c7a6376284c0d
prior         11 行：falsified=7 / abstained=4 / supported=0
prior 判定    abstain=False，blocked_by=["latest settlement is abstained, not falsified
              (an abstention is already the tail: no new evidence since)"]
              → 不再弃权，走正常闭环（正常提议、真实补丁、真实 baseline/candidate、真实测量）
结算          settlement_class = supported（met=True；min_delta 被 prior 提到 8.0，实测 delta=3.0，
              因此诚实地只声明 absolute_attainment，不声明 improvement_over_baseline）
回复行        1 条
```
任务声明在同 task_id 内做了第二次尝试（保持同类）：目标改为真实失败的
`tests/test_adapter_contracts.py -k gepa`（baseline 2 passed / 3 failed），补丁是真实修复
（candidate 5 passed / 0 failed）→ 真"成功证据"进入该类，`supported > 0` 使弃权条件**结构性失效**。
两次尝试都记录在 `task.json::attempt_history` 与 `bug_report.md`。

## 第五步：反事实验证

```
RF-B1  token abstRFB1_trace_02_1789841001  job_0cb2708ddb804951  prior=off
       prior 为空 → abstain=False(prior_disabled) → 真实执行 → 结算 falsified（回复行 1 条）
AB-B1  token abstABb1_trace_02_1789851001  job_56cc31d9bbcd43b4  prior=on（同类历史）
       → 结算 abstained
POST   token abstPOST_trace_02_1789839001  job_1bb26b4baecb4ea4  prior=off（反转之后）
       → 结算 supported，abstain=False
```
即：**同一类、同样的"全 falsified 且尾部 falsified"历史**，prior=on 时弃权、prior=off 时照常下注
并结算 —— 弃权与否只来自 prior，不来自 trace token 内容；且 prior=off 时既不写 prior 也不弃权。

## 优先级（与双向 prior 规则并存）

```
1. 弃权（refuted_ratio >= 0.75 且 supported == 0 且 evidence_total >= 2 且尾部 falsified）
   → 不下注；此时 min_delta 的收紧规则**仍然照常计算并冻结**（8.0），只是没有动作去执行它
2. refuted_ratio >= 0.5 → refuted_history_raises_the_bar
3. 否则 supported > 0 → supported_history_lowers_the_bar
4. improvement_over_baseline=False 历史 → require_baseline_rerun
5. single_episode_only 历史 → replicates=2
```

## 测试

```
tests/runtime/test_abstention.py（11 项）
  规则：纯函数/确定性/阈值是命名常量/不改写入参
  全 falsified → 弃权；比例 < 0.75 → 不弃权；= 0.75 且尾部 falsified → 弃权
  有 supported → 不弃权；证据不足（<2）→ 不弃权；空 prior → 不弃权
  尾部已是弃权 → 不弃权（不能连续）；其后出现新 falsified → 可再次弃权
  内核：abstained 已注册、四条不变式（claims/outcomes/publish/claim=none）逐条拒收、habit/growth 仍被拒
  闭环（事件层真实走 prior→record→execute→reconcile）：冻结 bet 声明 abstain、快照含弃权证据、
       结算 0 outcomes/blocker=abstained、abstention.json 声明 0 模型调用 0 个臂、测量为 missing、
       回复正文含"本次选择不下注"+证据、声称进展的草稿被丢弃、requires_human/notification_kind 就位
  prior=off 在同一历史上不弃权，且审计写的是 prior_disabled（不是 class_mismatch）
```

```
PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider tests/runtime/      → 77 passed, exit 0
PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider tests/commitment/   → 141 passed, exit 0
python3 -c "from partner.events import builtin_definitions; print(len(...))"  → 139
git diff --check                                                          → exit 0, clean
残留进程 0
```

## 本轮发现并修掉的 3 个真实缺陷

1. **弃权没有回复行**（接线缺口，最要紧）：派生版 flow 给 `notify` 之后的**每个节点**加了
   `when_output="notify.notify"`；而 `notification_decide` 在"路由=continue_project 且所有 delta 为
   False"时把 kind 降为 `progress` → `notify=False` → compose/critic/deduplicate/**send** 全被跳过。
   前两次真实弃权（AB1/AB2）因此只有结算、没有回复行。修：`commitment.bet_execute` 在弃权时给出
   `requires_human=True` + `notification_kind="abstention"`（诚实映射：没有任何机器可推导的下一步，
   只有新证据能推进；**不**伪造 business_delta）。修后 AB-B1/AB3 都产出了弃权回复行。
2. **"最近一条"排序读错字段**：内核结算写的是 `created_at`，没有 `settled_at`；我上一轮的 prior 读
   `settled_at` → 全空 → 排序退化为目录名序（随机）。弃权的"尾部必须 falsified"和"不能连续弃权"
   直接依赖这个判定，会让反转阶段误判。修：`created_at` → `state.updated_at` → 结算文件 mtime 的
   兜底链（`settled_epoch`），排序用 `(settled_at, settled_epoch)`。
3. **比例分母不一致**：提高门槛的规则用 `falsified/total`（含弃权），弃权规则用"带证据"分母 →
   同一份历史两个规则读到的失败率不同。修：统一为"带证据的结算"（evidence_total），并把
   `evidence_total` 写进 evidence；无弃权时数值与旧行为完全一致。
   附带修正：`prior=off` 无 prior 的审计原因从 `prior_class_mismatch` 改为 `prior_disabled`。

（另外两次操作层面的失误：编排脚本首轮把"settlement 已冻结"当成"整条 flow 跑完"，导致 3 次尝试
都没拿到回复行；`pkill -f` 两次误杀自己的 shell。都不影响结论，已改用括号技巧与显式 gate 判据。）

## Job DB 差异

```
314 → 324 行（每次运行 +1）；每个 driver 都比对了 before/after：旧 Job 改动 = 0
本轮新增 10 个 root Job（全部在本轮窗口内，未碰任何历史 queued/running Job）：
  job_21fc1980790f4a11  CF   prior=off → falsified
  job_1a71c3a3f2c54dd1  AB1  → abstained（门控修复前的弃权 #1）
  job_2de22b37c88b4028  RF1  prior=off → falsified（新证据）
  job_7956edbf16a24def  AB2  → abstained（#2）
  job_96894537281648f8  RF2  prior=off → falsified（新证据；flow 挂在既有 message_critic）
  job_984eee7d89dd4097  AB3  → abstained（#3；worker 到期打断，收尾后补出回复行）
  job_0cb2708ddb804951  RF-B1 prior=off → falsified（反事实，回复行 1 条）
  job_56cc31d9bbcd43b4  AB-B1 → abstained（#4，本轮"真实弃权"主证据）
  job_022c7a6376284c0d  REV  → supported（反转：不再弃权）
  job_1bb26b4baecb4ea4  POST prior=off → supported（反事实）
```

## 仍然存在的限制

- 弃权规则只读"同类结算的类别分布 + 尾部"，不看结论文本、不看时间衰减；阈值（2 / 0.75）是**声明
  的**策略常量，不是拟合出来的。
- 弃权后该类**只能**靠新证据（新的 falsified 或 supported）重新获得下注资格；本轮没有实现"外部
  异质信号"入场的通道（用户提到的那条外部量仍未接）。
- 反转用的"成功"来自同 task_id 的第二次尝试（目标与补丁被重声明为真实可修）：类键不变，但**任务
  声明变了**，这一点在 `task.json::attempt_history` 与本文档中显式记录，不藏在措辞里。
- 弃权时 `min_delta` 仍按收紧规则算到 8.0（上限）——冻结在 bet 里、无人执行；这是"先算后弃"的顺序
  结果，不是新语义。
- 既有 `message_critic` 偶发仍会打断 flow（RF2 就挂在它上面，发生在结算之后）；本轮不修 legacy 语义。
- 既有索引层 UTF-8 崩溃（`stream_projection.summaries`）仍在，按本轮范围未加固。
- 弃权是否"明智"不是本轮的判定对象：本轮只证明它是**真实的、可审计的、可反转的**选择。

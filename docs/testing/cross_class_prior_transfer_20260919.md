# 跨类先验迁移：直接同类不足时，从相似类借用先验（2026-09-19）

## 结论

**通过。** 相似类判据是纯 Python（三项成分、权重是常量、最高者胜、稳定排序）；
直接同类**为空**时相似类路径真实触发，并**真实改变了决策变量**（`min_delta` 4.0 → 0.25）；同一
测量在 `prior=off` 时回到声明的 4.0（结算主张随之从 `improvement_over_baseline` 变为
`absolute_attainment`）；直接同类**充足**时不触发相似类路径（`used_direct_only=true`、
`related_class_keys=[]`，只用直接历史）。

## 第一步：相似类判据（纯函数，权重是命名常量）

```python
RELATED_WEIGHT_SAME_ACTION  = 0.6   # 同 action_id，不同 project/metric
RELATED_WEIGHT_SAME_METRIC  = 0.5   # 同 metric_signature，不同 action/project
RELATED_WEIGHT_SAME_PROJECT = 0.4   # 同 project_id，不同 action/metric
DIRECT_WEIGHT               = 1.0   # 直接同类：永不打折

def similarity_weight(own, other) -> float          # 最高者胜，绝不叠加；三项全同 = 1.0（同类）
def similarity_reasons(own, other) -> list[str]      # 命中的成分名，便于审计
def related_class_keys(class_key, all_known_class_keys, *, components=None)
        -> list[tuple[str, float]]                   # 稳定排序：权重降序，其次 class key 升序
```

- 三项全同（权重 1.0）**不是**相似类来源：`related_class_keys` 直接排除（同类走直接路径）
- 空结果是正常答案；不依赖 embedding、不依赖语义相似度、不调用 LLM
- 成分从磁盘**重建**（`bet_class_components`）：`project_id` 取自 bet、`metric_signature` 取自冻结
  的 expected_effects、`action_id` 取自快照（真实任务快照有 `task_id`；bounded 快照有
  `candidate_space` → `bounded_metric`）。**只有当重建结果复现存盘 class key 时**
  （`class_components_verified`）该行才可作为相似类证据 —— 无法证明类归属的行不会被借用
  （实测：ace 类 13/13、dgm 类 9/9 复现；32 条更早的 bounded 行没有类键 → 失败关闭、不参与匹配）

## 第二步：跨类先验聚合（公式与实现位置）

实现：`partner/application/experience_prior.py`

```python
_class_counts(rows)                     # 单类统计（含 evidence_total = total - abstained）
effective_counts(prior)                 # 规则实际采用的视图：direct 或 weighted
summarize_prior(rows, *, class_key, components=None, related=None)
                                        # related = {class_key: {"weight","rows","reasons","components"}}
adjust_declared(declared, prior)        # 规则改用 effective_counts（float，不截断）
decide_abstention(prior)                # 弃权规则同样改用加权统计 + 记录下来的 tail
```

```
直接同类证据权重 = 1.0 ；相似类证据权重 = similarity_weight
falsified_weighted  = Σ_c  w_c · falsified_c          （c ∈ {direct(1.0)} ∪ related）
supported_weighted  = Σ_c  w_c · supported_c
evidence_total_weighted = Σ_c  w_c · (total_c − abstained_c)
refuted_ratio_weighted  = falsified_weighted / evidence_total_weighted
```

- **触发条件**：`direct.evidence_total < ABSTAIN_MIN_EVIDENCE (2)` → 检索相似类；检索始终发生，
  是否采用由这一条决定（`related.triggered` 与 `related.used` 分别记录）
- **只补充不覆盖**：直接行权重恒为 1.0，逐行加入；`counts`（直接视图）与 `weighted`（加权视图）
  同时保留，规则用 `effective_counts` 取用其一
- 规则本身不变：`refuted_ratio >= 0.5` → 收紧；`supported > 0` → 放松；弃权四条件（用加权值）
- `prior=off`：不读 prior（节点清空），相似类路径不触发，决策变量回到声明值
- **审计**（`prior_adjusted_parameter`）：`applied_from` / `used_direct_only` /
  `direct_class_evidence` / `related_class_keys`（**仅列出实际使用的**）/
  `related_class_keys_considered` / `related_class_reasons` / `weighted_evidence` /
  `applied_evidence` / `adjustment_rule` / `parameters[before,after,rule,evidence]` / `tail`

## 第三步：真实跨类迁移（T 类直接同类为空）

```
T 任务        cross_class_transfer_probe（新 task_id ⇒ 新类）
              target: tests/test_adapter_contracts.py -k "gepa or dgm"；真实补丁（gepa 契约修复）
              声明：tests_passed >= 1.0（threshold）且 delta_over_baseline(min_delta=4.0)
实测两臂      control 3 passed / 4 failed → candidate 6 passed / 1 failed（delta = +3，模块 sha 不同）
trace token   ccT1_trace_02_1789860001
root Job      job_99d037a109824ad6        （类 cls_4eeadd30ed370c55）
直接同类证据  total=0 evidence_total=0 falsified=0 supported=0   ← 直接同类为空
相似类        [["cls_95bc4202aeeea132", 0.5], ["cls_fb8b1f2ced4bec6a", 0.5]]
              理由 = ["same_metric_signature", "same_project_id"]（最高权重 0.5，不叠加）
加权证据      falsified=3.5  supported=5.5  evidence_total=9.0  refuted_ratio=0.3889
              （3.5 = 7×0.5；5.5 = (2+8)×0.5 … 逐类按权重折算）
调整          applied_from=weighted  used_direct_only=False
              rule=supported_history_lowers_the_bar
              before/after: min_delta 4.0 → 0.25（地板）   replicates 1 → 2
                evidence: supported=5.5 falsified=3.5 refuted_ratio=0.3889 threshold=0.5
结算 S_result settlement_class=supported  improvement_over_baseline=True
              supported_claim=improvement_over_baseline
              outcome: baseline=3.0 observed=6.0 threshold=1.0 met=True
```
即：**直接同类为空时，相似类的先验真实改变了决策变量**（4.0 → 0.25），并因此让本次结算诚实地
主张了 `improvement_over_baseline`（实测 delta 3.0 ≥ 0.25）。改的只是 prior：类键、消息、trace
token 都不参与权重计算。

## 第四步：反事实验证

```
trace token   ccT2_trace_02_1789861001
root Job      job_19e695b37b2f4215        （同一 T 类，同一任务声明，同一次测量）
prior         prior=off → 不写 prior、不读相似类
决策变量      min_delta = 4.0（声明值，未改）  replicates = 1
              applied_from=direct  used_direct_only=True  related_class_keys=[]  rule=none
结算          settlement_class=supported  improvement_over_baseline=False
              supported_claim=absolute_attainment（基线 3.0 → 观测 6.0，threshold=1.0 met=True）
```
⇒ 同一测量、同一任务，**唯一差别是 prior 开关**：prior=on 时门槛被跨类先验压低到 0.25 并主张改善；
prior=off 时门槛停在 4.0，同样 +3 的增量不足以主张改善。跨类迁移与否只来自 prior。
（顺序说明：反事实跑在迁移之后，此时 T 的**直接**同类历史只有 1 行，仍 < 2 ⇒ 即便 prior=on 也仍走
相似类路径；prior=off 时该历史完全不参与。）

## 第五步：直接同类优先（不被相似类覆盖）

```
trace token   ccD2_trace_02_1789863001
root Job      job_8d30f87845074e2f      （ace 类 cls_95bc4202aeeea132，直接历史充足）
直接同类证据  total=14 evidence_total=10 falsified=7 supported=3 abstained=4
相似类检索    triggered=False reason="direct_evidence_sufficient"
              considered=[["cls_4eeadd30ed370c55", 0.5], ["cls_fb8b1f2ced4bec6a", 0.5]]
调整          applied_from=direct  used_direct_only=True  related_class_keys=[]
              applied_evidence = 直接统计（falsified=7 supported=3 evidence_total=10）
              rule=refuted_history_raises_the_bar  before/after: min_delta 1.0 → 8.0
              冻结的 tail: {"class_key": "cls_95bc4202aeeea132", "tail_from": "direct", "source": "direct"}
结算          supported  improvement_over_baseline=False  supported_claim=absolute_attainment
```
⇒ 直接同类充足时相似类**只被检索、不被使用**：`related_class_keys=[]`、`used_direct_only=true`，
规则读的是直接历史（7 假 3 真 ⇒ 收紧到 8.0），相似类里的 supported 行没有把门槛压下来。

## 相似类路径下的弃权

弃权规则已改用加权统计，且**可由加权统计触发**（测试固定：借来的 4 条 falsified × 0.6 = 加权
evidence_total 2.4 ≥ 2、ratio 1.0、supported_weighted 0 → ABSTAINED；借来的 2 条只有 1.2 < 2 → 不弃权）。
本轮三条真实运行里它**没有**触发，原因是可核实的：相似类集合里 dgm 类含 8 条 supported ⇒
`supported_weighted = 5.5 > 0` ⇒ 弃权条件天然不成立（这也正是"只补充不覆盖"在语义上的体现）。
没有为了让它触发而裁剪相似类集合或改动相似度规则。

## 测试（本轮新增 14 项，共 232 项）

`tests/runtime/test_cross_class_prior.py`：
```
判据：纯函数/确定性/权重是常量；同 action / 同 metric / 同 project 三种权重各就各位；
      任意两项相同取最高不叠加；三项全同（1.0）不作为相似类来源；稳定排序且幂等；
      不可验证类的成分不被借用；无成分的裸 key 被丢弃；空结果正常
触发：直接同类为空 → triggered/used 均 True、applied_from=weighted；
      直接同类充足 → 不触发、used_direct_only、related_class_keys=[] 而 considered 仍可见；
      无相似类历史 → 与直接为空等价（不调整、rule=none）
加权：逐类按权重折算（含小数）；直接视图不被改动；加权视图在采用时才生效；
      直接证据只被补充不被覆盖；审计字段（direct/related/weighted/applied/rule/before-after）齐备
弃权：加权统计可触发，且加权 evidence_total 必须自身 ≥ 2
尾部：direct 路径用直接行尾部、weighted 路径用整体尾部（决定弃权第 4 个条件）
反事实：prior=off 不触发相似类路径
事件层闭环：在临时工作区造出真实形状的相似类结算 → prior 节点借用 → 冻结进 bet 快照与审计
```

```
PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider tests/runtime/      → 91 passed, exit 0
PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider tests/commitment/   → 141 passed, exit 0
python3 -c "from partner.events import builtin_definitions; print(len(...))"  → 139
git diff --check                                                          → exit 0, clean
残留进程 0
```

## 本轮发现并修掉的 3 个真实缺陷（都影响"相似类是否真的生效"）

1. **直接同类为空时相似类证据被静默丢弃**：`adjust_declared` 的早退条件是
   `prior["empty"] or total < 1`，而直接同类为空时 `empty=True` ⇒ 规则根本不运行、相似类证据
   白借。修：`empty` 的语义改为"任何地方都没有证据"（直接+相似都空），早退判据改为
   "有效证据（加权 evidence_total + abstained）≤ 0"。这正是本轮功能的核心路径。
2. **收紧规则的 `total >= 1` 守卫挡住加权分数**：一条相似类证据按 0.5 折算后 `total = 0.5 < 1`
   ⇒ 提高门槛的规则不触发（实测 T1 曾算出 min_delta 1.0 而非 1.5）。修：守卫改为
   `total > 0`，并在注释里写明"加权后可以是分数，整数守卫会丢掉正好要借的证据"。
3. **尾部归属错位**：`applied_from=direct` 时 `tail` 仍取自"含相似类"的整体排序，而弃权的第 4
   个条件读的就是尾部 ⇒ 相似类的最新结算可能替直接类回答"能不能弃权"。修：尾部随采用的视图
   （`tail_from` 记录来源），并加测试固定。D1 因该修复重跑为 D2（结果同为 supported /
   `used_direct_only=true`，仅审计字段的 tail 来源不同）。

## Job DB 差异

```
324 → 328 行；每个 driver 的 before/after 比对均为旧 Job 改动 = 0（未碰历史 queued/running）
新增 4 个 root Job：
  job_99d037a109824ad6  T prior=on  → supported（跨类迁移主证据）
  job_19e695b37b2f4215  T prior=off → supported（反事实）
  job_aa65a63def834853  ace prior=on → supported（D1，修复前；保留为证据）
  job_8d30f87845074e2f  ace prior=on → supported（D2，尾部修复后重跑）
```

## 仍然存在的限制

- 相似度只看三项成分的**相等性**，不看"差多少"；权重（0.6/0.5/0.4）是声明式常量，非拟合
- 相似类是**对称**关系：T 借了 ace/dgm 的历史，但 ace 类的决策不会因此改变（单向只读）
- 借来的证据按权重**线性**折算，未做样本量校正或时间衰减；权重上限 0.6（同 action 不同项目）
  意味着永远低于直接证据
- 加权后的 `min_delta` 仍受地板 0.25 与上限 8.0 约束；借来的成功证据足以把门槛压到地板（本轮
  4.0 → 0.25），这是设计选择，不是副作用
- 相似类证据进入弃权规则后**可能触发弃权**，本轮真实运行未触发（相似类含成功证据）；只有
  全失败相似类才会触发，尚未在真实运行中出现
- 更早的 32 条 bounded 结算没有类键（上一轮之前写入）⇒ 既不参与直接匹配也不参与相似类匹配
  （失败关闭，未回填）
- 既有 message_critic 偶发与索引层 UTF-8 崩溃按本轮范围未修

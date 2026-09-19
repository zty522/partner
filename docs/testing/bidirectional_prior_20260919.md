# 双向 prior 轮：失败史收紧门槛，成功史放松门槛（2026-09-19）

## 结论

**通过。** 反向规则（按 refuted 比例阈值提高 `min_delta`）真实落地并在真实运行里触发；
产生了一条**真实 refuted 结算**（来自真实任务的真实失败，不是构造的）；第二次同类任务的
`min_delta` 被真实提高（1.0 → 2.0）；反事实（`prior=off`）回到基线；放松规则没有回归。

## 第一步：反向规则

```python
REFUTED_RATIO_THRESHOLD = 0.5   # 平局算 refuted：更严的规则赢平局
MAX_MIN_DELTA = 8.0             # 提高方向的上界（下界仍是 MIN_DELTA_FLOOR = 0.25）

def adjust_declared(declared, prior) -> tuple[dict, dict]   # 纯函数，无 LLM
```

优先级（按顺序，先命中先执行；每条都写进 `prior_adjusted_parameter`）：

| 顺序 | 条件 | 动作 | rule_name |
|---|---|---|---|
| 1 | `refuted_ratio >= 0.5` | `min_delta = min(8.0, base * (1 + falsified))`，仅当结果 > base | `refuted_history_raises_the_bar` |
| 2 | 否则 `supported > 0` 且 `base > 0` | `min_delta = max(0.25, base / 2**supported)` | `supported_history_lowers_the_bar` |
| 3 | 有 `improvement_over_baseline=False` | `require_baseline_rerun = True` | `improvement_false_forces_a_real_baseline` |
| 4 | 有 `single_episode_only` 阻塞 | `replicates = 2`（候选臂真跑两次） | `single_episode_blocker_adds_one_executed_repetition` |

`evidence` 逐条记录：`falsified / supported / total / refuted_ratio / refuted_ratio_threshold /
max_min_delta / floor_note`（放松分支同样记录 refuted 比例，读的人能看到为什么走的是放松分支）。

**地板与上界的语义**（明确写死）：
- 地板 `0.25` 只约束**放松**方向；坐在地板上的类**仍可被收紧**（0.25 → 0.5，有测试）
- 上界 `8.0` 约束**提高**方向；`base` 已高于上界时不会被"拉下来"（20.0 保持 20.0，有测试）

### 顺带修掉的一个真实缺陷：类别词汇

内核的权威集合是 `partner/commitment/models.py::SETTLEMENT_CLASSES =
("supported","falsified","inconclusive","invalid","blocked")` —— 内核把"被证伪"叫作
**`falsified`**，**从不发 `refuted` 这个词**。而上一轮我的 `SETTLEMENT_CLASSES` 里写的是
`"refuted"`，也就是说**上一轮的计数会把真实 falsified 结算全部漏掉**（规则永远不触发而不报错）。
本轮改为直接抄内核的元组，把旧词 `refuted` 作为别名并入 `falsified` 桶，并加了一个
**对着内核导入**的守卫测试（`test_the_class_vocabulary_is_the_kernel_vocabulary`），防止再次漂移。

## 第二步：一条真实的 refuted 结算

任务 `refuted_green_baseline_ace`（`benchmarks/real_tasks/refuted_green_baseline_ace/`）：

```
target_module   partner/research/adapters/ace.py
test_target     tests/test_adapter_contracts.py -k test_ace_append_to_memory_writes_real_file
                → 该选择器只命中 1 条测试，且 baseline 中它已经通过
声明的预期      tests_passed >= 2.0（threshold=2.0），delta_over_baseline(min_delta=1.0)
候选补丁        给模块 docstring 加一段说明（行为中立）
```

**为什么候选无法改进**：可测的最大增量是 0 —— 目标里只有 1 条测试，而它已经通过；冻结的门槛要求
2 条通过（"预期 delta 大于任务物理上能给的"，用户明确允许的一种诚实的失败）。补丁是真实的：
模块 sha 从 `e0dab03574ee` 变为 `1cf4932119b6`（证明补丁真的应用了），两个臂都由项目自己的
测试运行器真实执行。

| 臂 | 结果 |
|---|---|
| control（未修改） | 1 passed / 0 failed |
| candidate（声明补丁） | 1 passed / 0 failed（delta = 0） |

**真实 refuted 结算 S_refuted**（02 走完整闭环产生，非构造）：

```
trace token   refR1_trace_02_1789832001
root Job      job_fb0ad10891654002        （同类 cls_95bc4202aeeea132 的第一次）
min_delta     1.0（prior 空，未调整）   replicates 1
settlement    settlement_class = falsified      ← 真实的 refuted
              expectations_met = False | improvement_over_baseline = False | supported_claim = none
              outcome: baseline=1.0 observed=1.0 threshold=2.0 met=False
                       note="delta=0.0 is not an improvement over the baseline"
Job 终态      failed —— 失败发生在**结算之后**的既有 message_critic（先例 case_02），故无回复行
```

## 第三步：收紧路径真实触发

同类第二次（token `refR2_trace_02_1789833001`，Job `job_8884f5cae324444d`）：

```
prior 读到     1 条同类结算 = R1 的 bet_job_fb0ad10891654002
              counts: supported 0 / falsified 1 / total 1 / refuted_ratio 1.0（阈值 0.5）
prior_adjusted_parameter:
  name=min_delta   before=1.0  after=2.0  rule_name=refuted_history_raises_the_bar
  evidence={falsified:1, supported:0, total:1, refuted_ratio:1.0,
            refuted_ratio_threshold:0.5, max_min_delta:8.0, floor_note:...}
  name=replicates  before=1    after=2    rule_name=single_episode_blocker_adds_one_executed_repetition
S2            settlement_class = falsified（与 R1 同类结果，符合预期：任务本身不可达）
```
**R2 的 min_delta（2.0）> R1 的实际值（1.0）**，提高只来自 prior（类键只由三个声明字符串构成，
token 换成任意字符串结果不变，有纯函数测试）。

## 反事实（第三步的对照）

同类第三次，消息声明 `prior=off`（token `refR3_trace_02_1789834001`，Job `job_bcf4409f05684dfd`）：

```
min_delta = 1.0   replicates = 1   prior_adjusted = False（reason=prior_empty）
```
此时同类历史里**确实已有 1 条 falsified**，但节点被禁用 → 决策变量回到 R1 的值；Job 走完整条 flow
并产出 1 条回复行。

## 第四步：放松路径没有回归

用旧的 supported 主导类（`cls_fb8b1f2ced4bec6a`，上一轮 8 条全 supported）跑一次新代码
（token `supR4_trace_02_1789835001`，Job `job_d1b554b0708e45e9`，completed，回复行 1 条）：

```
prior         8 条同类，falsified 0，refuted_ratio 0.0 → 放松分支
adjusted      min_delta 1.0 → 0.25  rule_name=supported_history_lowers_the_bar
              evidence={supported:8, floor:0.25, falsified:0, total:8, refuted_ratio:0.0, threshold:0.5}
              replicates 1 → 2（single_episode_only）
settlement    supported，note="improvement_over_baseline delta=1.0>=0.25"
```
即：新增反向规则后，supported 主导的类仍然降低门槛（0.25 是地板，低于声明的 1.0）。

## 测试

```
tests/runtime/test_experience_prior.py（22 项，其中本轮新增 5 项）
  test_the_class_vocabulary_is_the_kernel_vocabulary     内核词汇守卫（导入内核元组比对）
  test_all_supported_lowers_and_all_refuted_raises       双向：3 supported → 0.25；3 falsified → 4.0
  test_mixed_history_follows_the_declared_priority       1f/2s → 0.25；1f/1s（平局）→ 2.0；2f/1s → 3.0
  test_raising_works_from_the_floor                      地板 0.25 → 0.5（地板只约束放松方向）
  test_raising_is_bounded_and_never_lowers_an_already_high_base   8 falsified → 封顶 8.0；base 20 不被拉低
  （原有）空 prior 不调整 / prior=off 反事实 / 只读同类 / 不改写历史 / 错类不泄漏
```

```
PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider tests/runtime/      → 66 passed, exit 0
PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider tests/commitment/   → 141 passed, exit 0
python3 -c "from partner.events import builtin_definitions; print(len(...))"  → 139
git diff --check                                                          → exit 0, clean
残留进程 0
```

## Job DB 差异

```
309 → 314 行；旧 Job (status, updated_at) 逐条不变：changed = 0
新增 5 个 root Job（全部是本轮的 run，未碰任何历史 queued/running Job）：
  job_fb0ad10891654002  R1  真实 refuted（S_refuted）
  job_8884f5cae324444d  R2  同类第二次，min_delta 1.0 → 2.0
  job_bcf4409f05684dfd  R3  反事实 prior=off → 1.0
  job_2f8a2273b2584dd9  R4  attempt1（settled supported，flow 挂 critic → 重试）
  job_d1b554b0708e45e9  R4  attempt2（completed，放松分支 0.25）
```

## 仍然存在的限制（如实）

- 反向规则只按**比例**触发，不做加权：1 条 falsified 和 10 条 falsified 若比例都 ≥ 0.5，幅度按
  `base * (1 + falsified)` 计数走，仍受 8.0 上界约束（避免无限提高，代价是上限内不区分严重程度）。
- 提高后 `min_delta` 是**冻结进 bet** 的；历史里那条 falsified 结算不会因为后面的成功而被"冲淡"，
  只有比例变化才会改变分支（例如同类变成 2 supported + 1 falsified 即回到放松分支）。
- R1/R2/R4-attempt1 的 **Job 终态是 failed**，失败发生在结算之后的既有 `message_critic`
  （先例 case_02，LLM 硬规则偶发拒绝）；结算与冻结值都完整。本轮不修 legacy 语义。
- 目标测试只有 1 条是**人为选定的**"物理不可达"设计：它诚实地产生 falsified，但也意味着这条结算
  不能说明"补丁质量差"，只能说明"这条冻结预期在该任务上不可达"。
- 本轮未做 malformed JSON 加固、未解决草稿幻觉、未提交 gepa 补丁进 main（按要求）。

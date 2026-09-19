# 真实任务轮：commitment 闭环测量一个真实仓库任务（2026-09-19）

## 结论

**有界动作已从"文本统计"换成真实仓库任务，闭环在 02 的 Event 流里跑通，并且这次的
`improvement_over_baseline=True` 是由真实证据驱动的**（项目自己的测试运行器）。

## 任务是什么

真实 bug 报告 = 仓库里**真实失败**的契约测试：

```
cd /mnt/e/work/partner && PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider \
    tests/test_adapter_contracts.py -k gepa
→ 3 failed, 2 passed
  ImportError: cannot import name 'propose' from 'partner.research.adapters.gepa'  (×3)
```

三处契约违反：模块只导出 `GepaOptimizer` 类（契约需要模块级 `propose`/`record_fitness`/
`pareto_front`）；`record_fitness` 的关键字与错误类型不符（契约要 `fitness_signal` 且必须是
`ValueError`）；`pareto_front` 不存在。

候选补丁 = 在**真实仓库之外**声明的 `patch.diff`（7.4 KB），补上模块级 API、让
`GepaError` 继承 `ValueError`、其余行为不变。任务定义在
`benchmarks/real_tasks/fix_gepa_contract/`（`task.json` + `patch.diff` + `bug_report.md`）。

## 三个条件如何满足

| 条件 | 实现 |
|---|---|
| 外部、不可自生成的指标 | 指标由 **pytest 自己**给出（passed/failed 计数与 exit code）。内核读的是运行器的汇总行，动作**不能自报成绩** |
| 真实 baseline | control 臂 = **项目历史状态**（未修改的模块），用**同一执行器、同一评测器**真实执行并测量，落为 `BaselineEvidence(provenance=fresh_execution, immutable=True)` |
| 结果不被强迫为 supported | 结算仍走内核机器规则；预期 `delta_over_baseline(tests_passed, min_delta=1.0)`，证伪条件声明为 `no_new_pass` / `missing_measurement`。本次恰为 supported，若补丁打不上则是 failed receipt（已有测试覆盖） |

## 测量结果（真实、可复核）

| 臂 | 模块 | 结果 |
|---|---|---|
| control | 未修改（项目历史状态） | **3 failed, 2 passed**（exit 1） |
| candidate | 声明补丁 | **5 passed, 0 failed**（exit 0） |

内核结算：

```
lifecycle            CLOSED  settled=True  settled_class=supported  experience_emitted=True
settlement_class     supported
expectations_met     True
improvement_over_baseline  True          ← delta=3.0 >= min_delta 1.0
supported_claim      improvement_over_baseline   ← 最强主张，这一次是真证据
baseline_already_satisfied True
machine_rules        treatment_diff_hash=127254ef2561 expected=127254ef2561
                     controls_identical=True treatment_as_frozen=True
publish_eligible     False（honest: environment_not_publishable:isolated_sample,
                      isolated_sample_only, single_episode_only）
expectation_outcomes [{baseline: 2.0, observed: 5.0, delta 3.0, met: true}]
baseline evidence    base_bet_job_387ece10625c4758 | fresh_execution | immutable
measurements         baseline (2 passed / 3 failed) vs candidate (5 passed / 0 failed)
experience           exp_bet_job_387ece10625c4758_r1 (level=experience, authoritative=False)
bet revision         1（freeze_hash 未变，链完整）
```

## 关键机制与一个必须记下的坑

- **影子仓库（symlink farm）**：把整仓用符号链接暴露到沙箱里，只把被声明的**一个文件**换成真文件。
  两个臂都走同一机制，差别仅在文件内容；**真实仓库一个字节都没被写**（有回归测试断言 sha 不变）。
- **坑（差点测出假结果）**：最初用 `PYTHONPATH=<overlay>:<repo>` 覆盖模块——**静默失效**。
  pytest 会把 repo root 插到 `sys.path` 前面，`import partner.…` 仍解析到未修改的模块，于是
  candidate 臂测的其实是 control，两臂结果完全相同（3 failed / 2 passed ×2）。改用影子仓库后
  candidate 立刻变成 5 passed / 0 failed。**凡是"替换一个模块再测量"，必须确认模块真的被替换**
  （本轮的判据：两臂的 `module_sha256` 不同，且落进产物）。

## 本轮暴露的集成缺口（如实记录，未修）

实例自己写进日志渠道的那条回复说："本轮契约修复任务未取得进展：执行阶段后台进程没有留下终态回执，
属执行失败…无法判断补丁是否应用、测试是否裁定。"

这与内核的裁决**矛盾**：内核有完整回执（control 3 failed → candidate 5 passed，settlement supported）。
原因是 `project_iteration` 流的其它节点（`inspect/plan/execute/verify`）**不消费 bet 的产物**，
它们看的是自己那条链的执行回执；本次运行的前半段被沙箱超时打断过，于是那半边的叙述是失败。
结论：**接点本身成立（内核用真实证据测量并结算），但"内核裁决 → 实例叙述/报告"这一层还没接**。
这是下一轮的候选（单独一轮，不在这轮顺手做）。

## 测试

```
tests/runtime/test_real_task_adapter.py  6 passed（新增）
tests/                                  178 passed
git diff --check                        clean
```
新增测试覆盖：control 臂测出真实失败、补丁两臂差额 = +1；影子仓库**不写真实项目**（sha 前后一致）且
目标文件是唯一真文件；`load_task` 拒绝不完整定义；补丁 FIND 不匹配/歧义/格式错都拒绝（不猜）；
补丁打不上时执行器返回 failed receipt 而不是静默退化成 control。

## 边界

- 补丁**只在沙箱里被测过**，没有写进 `/mnt/e/work/partner`（真实仓库未改）。若要真修，那是一个
  独立的、需要你点头的提交。
- `dgm.lineage_edges` 的契约失败（同一测试文件的另一个真实失败）本轮不在任务范围内，仍未修。
- 环境 `isolated_sample`，结果结构性不可发布。

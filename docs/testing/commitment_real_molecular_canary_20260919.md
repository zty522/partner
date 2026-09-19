# 真实 02 分子生成项目 commitment canary 验收（2026-09-19）

## 1. 本轮两件事

1. **legacy 发布安全修复**（commit `3c785ce`）：commitment/1 记录不再由自身 `publish_eligible`
   反推环境，统一进入 `legacy_unknown`，`schema_legacy` 成为不可绕过的发布/晋升阻塞。
2. **真实 02 分子生成项目 canary**：用项目真实冻结池与真实方法账本，跑完一个有界、可证伪、
   可审计的 bet，直到机器结算与报告产出。

## 2. legacy 修复：修改前 / 修改后

| | 修改前 | 修改后 |
|---|---|---|
| v1 缺 environment | 按 `publish_eligible` 反推为 `production` | 统一 `legacy_unknown`（属不可发布集合） |
| v1 的 publish 声明 | 直接成为当前 `publish_eligible` | 只保留为 `legacy_publish_claim` 供审计 |
| v1 ComparisonProof | 可 matched=True | `legacy_untrusted=True`，`matched` 恒 False |
| v1 结算的经验 | 可铸造 | `build_experience` 拒绝 |
| 晋升路径 | 无统一闸门 | `ExperienceRecord.assert_promotable()` 唯一闸门 |
| 历史文件 | — | 字节与哈希链读取前后不变 |

## 3. 精确命令与结果

| # | 命令 | 退出码 | 结果 |
|---|---|---|---|
| 1 | `PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider tests/commitment/` | 0 | **125 passed** |
| 2 | `python3 -c "from partner.events import builtin_definitions; builtin_definitions()"` | 0 | 135 条定义、无重名 |
| 3 | `git diff --check` | 0 | clean |
| 4 | canary 运行器 `--dry-run` | 0 | 取锁→只读投影→契约校验→冻结（freeze_hash `eb6dbbacdbc2897a`，replicates=2）→停 |
| 5 | canary 运行器 真实执行 | 0 | bet 终态 **CLOSED**，结算 **falsified** |
| 6 | 第一次真实执行（保存的失败证据） | 1 | **BLOCKED**：`PoolError: unknown selection method ''`（接线错误，见 §8） |

## 4. 投递与 ID

- 投递命令：`cd /mnt/e/work/partner && python3 scripts/send_manual_task.py --workspace /mnt/e/work/partner_workspace --instance 02 --task-file <task>`
- inbox row id：**`manual_02_1789800227_9f8b4c48317b`**（1881 chars，创建于 2026-09-19T06:43:47Z）
- 任务文件：`benchmark_runs/commitment_canary/instance02_task.txt`
- commitment 侧 ID：run_id `molecular_canary_02`、bet_id `bet_molecular_canary_02`
- **02 侧未执行**（见 §10 外部阻塞）

## 5. 状态转换与时间

| 时间 | 状态 |
|---|---|
| 14:43:25 | 只读投影 Job DB：queued 139 / running 20 / completed 71 / failed 33 @ mtime 2026-09-18 23:46:09 |
| 14:43:25 | 取得所有权锁（pid 2195907，owner `canary-molecular_generation_02_canary_v1-2195907`） |
| 14:43:25–14:43:31 | 建 bet → 冻结 → COMMITTED → 执行 control → 执行 candidate → MEASURED → SETTLED → CLOSED |
| 14:43:31 | 结算 falsified；释放锁；Job DB 复查未变 |

## 6. baseline 与 candidate 的真实来源

- **池**：`datasets/bootstrap/molecular_synth_comparison.csv`，170 条真实 SMILES（含 QED/SA），
  `pool_hash b253aa925236a7c1`；非伪造、非 fixture。
- **baseline（control）**：项目冻结方法 **`rule`**，由 `MolecularSelectionExecutor` 真实重跑并
  由同一确定性评测器测量 → `BaselineEvidence`（`provenance=fresh_execution`、`immutable=True`、
  receipt `rcpt_bet_molecular_canary_02__baseline_r1`）。
- **candidate（treatment）**：项目 ledger 中已有方法 **`scaffold_cap`**（声明先验 gain 0.08 /
  risk 0.35，由 LLM proposer 从声明空间中选出 id，再由守卫选择器确定；选择依据是**声明先验**，
  不是预测）。
- 重复：两个预先声明的 seed `(20261239, 20261307)`，`replicates=2`。

## 7. 原始指标、汇总与 guardrail

| seed | 选中数 | 去重 SMILES | scaffold 数 | 平均 QED | 平均 SA |
|---|---|---|---|---|---|
| 20261239 | 15 | 10 | 5 | 0.6185 | 2.6936 |
| 20261307 | 15 | 10 | 5 | 0.6185 | 2.6936 |

| 指标 | kind | 阈值 | baseline | candidate | met | 相对 baseline |
|---|---|---|---|---|---|---|
| qed_mean | delta_over_baseline | δ ≥ 0.02 | 0.534122 | **0.618549** (+0.084427) | ✅ | 改善 |
| sa_mean | guardrail | ≤ 1.60 | 1.327811 | 2.693580 | ❌ | — |
| validity | guardrail | = 1.0 | 1.0 | 1.0 | ✅ | — |
| uniqueness | guardrail | = 1.0 | 0.5 | 0.333333 | ❌ | — |
| selected_count | guardrail | ≥ 20 | 20 | 15 | ❌ | — |

- 额外观测（非预期）：`nn_tanimoto_mean` 从 0.537288 降到 0.172433 —— scaffold 数上升但指纹多样性下降。
- **ComparisonProof matched = True**：六项控制变量全 true（inputs/evaluator/protocol/budget/
  environment/harness），`treatment_as_frozen=True`，`code_changed=False`，
  `treatment_diff_hash = expected_treatment_diff_hash = d77120023b5c`。

## 8. 结算与机器规则

```
settlement_class      = falsified
supported_claim       = none
expectations_met      = False
improvement_over_baseline = True
baseline_already_satisfied = False
environment           = production_canary
publish_eligible      = False
publish_blockers      = ['falsification_violation:sa_ceiling_broken',
                         'new_regression:sa_mean', 'new_regression:selected_count',
                         'expectations_not_met', 'settlement_class_not_supported:falsified']
```

关键机器规则：
- `improvement_over_baseline=True while falsified: the relative delta moved in the declared
  direction, but ['sa_mean','uniqueness','selected_count'] failed, so the direction is still refuted`
- `uniqueness` 被判为 **`pre_existing_failure`**（两个臂都失败）而非新增回归 —— 这正是本轮要求的
  "既有失败 vs 新增回归"区分，在真实数据上得到验证（项目 `rule` 组本身就有重复 SMILES）。
- `sa_mean` / `selected_count` 是 **new_regression**（baseline 达标、candidate 未达标）。

## 9. 是否真正推进了项目

**结论：本次不推进项目主线，但产生了一个有效的负结果。**
`scaffold_cap` 能把平均 QED 提高 0.084（远超冻结 δ=0.02），代价是平均 SA 从 1.33 升到 2.69
（超冻结上限 1.60）、选择数从 20 降到 15、去重率从 0.5 降到 0.33。因此它不能作为主线依据。
本报告全部数字为**计算代理指标**，不是活性、药效、湿实验可合成性或临床价值。

## 10. 外部阻塞（如实报告）

02 侧未消费该任务，原因是一个**由本轮指令本身造成的冲突**：

- 02 实例当前**没有进程在运行**（`instance.pid` 81637 是陈旧 pid）；
- 唯一消费 `desktop_inbox.jsonl` 的入口是旧主链 `scripts/run_instance_native_runtime.py`
  （及其 worker / bridge / readmission）；
- 启动它会按 `_worker_count` 领取排队的 Job，即消费那 **139 个 queued / 20 个 running**；
- 而本轮指令明确要求"不得顺带领取 139 个 queued Job"、"不得启动无界 campaign"。

因此：**投递已完成（有真实 row id 与任务文件），02 侧执行被该冲突阻塞**，
未进入终态。解除阻塞需要其中一项授权（择一）：
(a) 允许只领取"该 inbox 任务"的单 Job 消费入口（需要新写一个只消费指定 row 的 worker）；
(b) 允许先约束旧主链队列到有界，再启 instance 02；
(c) 把 canary 执行权完全交给 commitment runner（本轮已在内核侧如此执行），
    02 只作为任务来源与审阅者。

本轮选择 (c) 的等价做法：canary 已由 commitment runner 在真实项目数据上跑完并结算，
02 收到的任务文本即为可复现的执行说明与产物引用。

## 11. 关键产物绝对路径

- bet：`/mnt/e/work/partner_commitment/benchmark_runs/_canary_ws_run2/state/commitments/molecular_canary_02/bet_molecular_canary_02/bet.json`
- state：`/mnt/e/work/partner_commitment/benchmark_runs/_canary_ws_run2/state/commitments/molecular_canary_02/bet_molecular_canary_02/state.json`
- events（哈希链）：`/mnt/e/work/partner_commitment/benchmark_runs/_canary_ws_run2/state/commitments/molecular_canary_02/bet_molecular_canary_02/events.jsonl`
- manifest：`/mnt/e/work/partner_commitment/benchmark_runs/_canary_ws_run2/state/commitments/molecular_canary_02/bet_molecular_canary_02/manifest.json`
- issues：`/mnt/e/work/partner_commitment/benchmark_runs/_canary_ws_run2/state/commitments/molecular_canary_02/bet_molecular_canary_02/issues.jsonl`
- baseline 证据：`/mnt/e/work/partner_commitment/benchmark_runs/_canary_ws_run2/state/commitments/molecular_canary_02/bet_molecular_canary_02/baseline/base_bet_molecular_canary_02.json`
- settlement：`/mnt/e/work/partner_commitment/benchmark_runs/_canary_ws_run2/state/commitments/molecular_canary_02/bet_molecular_canary_02/settlement/stl_bet_molecular_canary_02_r1.json`
- 报告 MD：`/mnt/e/work/partner_commitment/benchmark_runs/commitment_canary/run_real2/molecular_canary_report.md`
- 报告 PDF：`/mnt/e/work/partner_commitment/benchmark_runs/commitment_canary/run_real2/molecular_canary_report.pdf`（67509 bytes，pandoc→wkhtmltopdf 真实生成）
- canary 摘要 JSON：`/mnt/e/work/partner_commitment/benchmark_runs/commitment_canary/run_real2/canary_report.json`
- 首次失败的证据：`benchmark_runs/commitment_canary/run_real/canary_report.json`

## 12. 生产 Job DB 前后对比

| | counts | mtime |
|---|---|---|
| 运行前 | queued 139 / running 20 / completed 71 / failed 33 | 2026-09-18 23:46:09 |
| 运行后 | queued 139 / running 20 / completed 71 / failed 33 | 2026-09-18 23:46:09 |

连接全程以 `file:...?mode=ro` 打开；`JobReadOnlyProjection` 中不存在任何写路径。

## 13. 本次发现并修复的问题

1. **legacy 反推 production**（本轮主修复，数据合同层）；
2. **第一次真实 canary BLOCKED**：`PoolError: unknown selection method ''` —— canary 声明的候选空间
   把动作放在 `method` 键，而 proposer 从 `params` 读取动作参数；内核正确地拒绝了空动作。
   已修接线 + 新增 `tests/commitment/test_canary_wiring.py`（4 项）并在同一有界任务内重跑；
3. **`falsified` 曾禁止 `improvement_over_baseline=True`**：真实场景（主指标改善但 guardrail 失败）
   会被这个旧不变量挡住。已放开并让机器规则显式归因，新增测试
   `test_falsified_with_improvement.py`；
4. **外部声明 spec 嵌套对象缺 `schema_version`**：适配层新增 `_declared()` 规范化，
   声明方不必知道内核内部 schema 版本。

## 14. 仍未解决 / 边界

- 02 侧任务未被消费（见 §10 冲突，需授权择一）；
- 单 bet、单项目、两个 seed：不构成跨项目或长期结论；
- 方法实现由本 canary 按项目协议重写，未与项目原始实现逐位对齐；
- 项目主线瓶颈（更大外部实验 pK 测试集、27 条可匹配样本的功效不足）依旧未解决；
- 未验证：多 bet 并发、跨实例迁移、长期无人值守、生产晋升（本轮刻意不自动晋升）。

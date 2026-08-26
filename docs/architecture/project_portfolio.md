# 五项目长期轮转

## 目标

`portfolio-continuous` 是 Campaign 上层的项目组合调度器。它让 01–05 都有明确通道，运行时最多占用两个实例；新输入、声明式主动探索和低频证据 scout 都能创建 WorkItem。它不按时间机械重跑旧报告。

## 五条通道

| 实例 | 输入门 | 固定推进事件 | 空闲语义 |
|---|---|---|---|
| 01 | `external/content/inbox.jsonl` 内容指纹变化 | `evidence_execution_slice` | 等待新内容；禁止真实发布 |
| 02 | TargetDiff `data/` 或 `datasets/` 出现官方 split 候选 | `targetdiff_provenance_audit` | 等待官方拆分；不重跑 Stage 13 |
| 03 | Campaign/RL/Executor/CLI/测试代码指纹变化 | `framework_campaign_contract_audit` | 等待代码变化 |
| 04 | 声明的 RL/SESA/ProRL/论文来源指纹变化 | `external_learning_index_slice` | 等待新来源；indexed 不等于 integrated |
| 05 | 01–04 新准入任务全部终态，且结果集合指纹变化 | `offline_rl_self_evolution` | 等待业务证据，不抢占项目 |

## 持久状态与调度

每个 Campaign 在 `state/campaigns/{campaign_id}/portfolio_state.json` 保存每条 lane 的：观察到的输入指纹、最后已派发指纹、状态、原因、WorkItem ID 和更新时间。Controller 每个 tick 重新计算有界指纹，因此后台服务在 blocked/waiting 状态仍可被新文件或代码变化唤醒。

输入必须连续两个 Controller tick 保持相同指纹才可准入，避免下载中的临时大小被误认为多个版本。新 Campaign 从最近的 Portfolio 状态继承已消费指纹、探索轮次和 scout 游标，不因预算换代重复旧证据。

通道状态包括 `queued/active/waiting_input/waiting_change/waiting_wave/outside_campaign_scope/budget_exhausted`。`status` 命令把这份组合状态与 Campaign、WorkItem、Lease 一起返回。最大并发仍由 Campaign `max_active<=2` 与实例 scheduler 双重限制。

05 的指纹由本 Campaign 中 01–04 终态 WorkItem 的状态、事件、产物和更新时间组成。只要仍有业务任务未终态，05 就保持 `waiting_wave`。RL 只产生 candidate Experiment；样本或回归门不足不得 promoted。

## 主动探索与长期 scout

输入波次完成并被 05 摄取后，各 lane 进入有限的可执行课程：01 从来源分析推进到逐条 brief、去重 backlog 和 claim evidence matrix；02 从 split provenance 推进到官方 identity benchmark、靶点组 bootstrap 和误差切片；03 从 Campaign 回放推进到质量门候选与恢复/双槽 canary；04 从真实 fetch 推进到 adapter prototype 和独立适配合同。每轮有不同事件/参数和机器结果，课程耗尽后不得从 Round 1 重新假装创新。

全部课程完成后，每 15 分钟按游标准入最多 `max_active` 个不同 lane 的 evidence scout，检查新内容、来源/校验和、框架回归或外部资料变化。默认双槽因此一次可并行两个 Scout，而不是空置第二槽。no-change 必须明确标注为监测证据，其重复 progress signature 不获得 `novel_evidence` 奖励；Scout 属于 `audit`，不进入业务 outcome fingerprint，也不触发 05 重复审计。只有新的 `project_iteration` 业务终态才形成新的 05 学习波次。

05 只把均奖励低于 `0.25` 或成功率低于 `0.67` 的动作建立为低收益 Issue。健康候选中“相对最低”的动作不能被自动写成高严重度故障；样本不足由 canary/promotion 门表达，不靠伪 Issue 表达。05 自身、报告和 Scout 均明确不是 `business_progress`。

## Receipt 驱动的执行链

业务 WorkItem 完成后，不再因为它是 deterministic/bounded event 就停止。Controller 根据
`business_progress` 生成声明式 NextAction，01–04 当前分别有来源核验→候选 brief→安全门、
官方 split 校准→误差切片、持久证据 canary→策略接入、Harness 映射→独立适配合同等有限链。
05 摄取本波结果后，`materialize_project_actions` 把 proposed action 写成新 WorkItem；真实 task ID
出现后 action 才从 proposed 进入 queued。无语义增量、scout no-change 或达到外部批准边界时明确等待。

所有任务产物先复制到 `share/evidence/{project}/{campaign}/{work}/` 并生成 EvidenceManifest；
TaskInstance 清理不再破坏下一轮 inputs。最多双槽规则保持不变。

要求 QQ 送达的 WorkItem 还有一层运行时门：每个 QQ bridge 原子维护
`instances/XX/state/qq_delivery_state.json`。实例冷启动或网关断线时，Controller 只启动槽位、
不租约也不派发；状态变为 `delivery_ready=true` 后下一个 tick 自动继续。

两个 scout 之间 Campaign 保持 `running`，`stop_reason` 显示下一计划时间，但活动槽为 `[]`；这是节能等待，不是 blocked 或进程退出。

## 课程续生与业务密度门（2026-08-24）

旧课程完成后不能直接把五个 ProjectState 泛化写成 completed 并永久退化为 Scout。当前 v3
为 01–04 增加第二层项目专属课程：内容主张风险队列/编辑 backlog、TargetDiff 模型风险登记/
下一实验门、用户可观察性 canary/soak 密度分析、外部引用差距矩阵/采用实验 backlog。

Receipt 的可执行 NextAction 必须先于 05 物化；05 只在整个已准入业务链全部终态后运行一次，
不得在同一项目的每个小步骤后抢跑。最近 12 个非报告工作中，若至少 6 个是 Scout 且业务进步
密度低于 0.25，Controller 抑制后续 Scout 60 分钟，状态写为
`degraded_waiting_new_hypothesis`。输入指纹仍每 tick 检查，因此新证据可以立即唤醒项目。

## 长期启动

```bash
cd /mnt/e/work/partner
python scripts/partner_campaign.py start \
  --profile portfolio-continuous \
  --goal "五项目按新证据轮转，业务推进后做离线 RL" \
  --duration 12h --instances 01,02,03,04,05 --max-active 2 \
  --max-work-items 80 --max-failures 8 --max-retries 1 \
  --report-interval 1h --interval 20 --detach
```

`blocked` 且原因为 `waiting for resume event or new evidence` 是安全等待，不是 Controller 退出。到 deadline 后才收尾并发送最终报告。若 work-item 预算将耗尽，必须开新 Campaign；不得偷偷扩大既有预算。

等待状态会把真实 scheduler slots 切为 `[]`，释放全部实例资源；后续新输入或定时报告会重新启动所需实例。终态旧 Controller 只有仍拥有 active Campaign 指针时才能恢复其旧槽，避免新旧 Campaign 换代竞态。

## 验收边界

- “五项目轮转”指五条通道都被管理，不代表缺输入的项目必须伪造一次执行。
- 每轮仍需真实源码/退出码/机器结果/报告/QQ 回执和最终验收；本文件不降低原 Campaign 硬门。
- 指纹只负责决定是否准入，不证明输入质量。具体事件仍需检查 provenance 和语义。
- 当前 02 官方 split 缺失时必须可见地等待；这是有意义运行的一部分。

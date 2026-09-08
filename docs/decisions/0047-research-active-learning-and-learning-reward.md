# ADR 0047：真实源码/论文主动学习、学习 Reward 与 04 续轮验证

**状态**：Accepted for bounded manual/shadow use  
**日期**：2026-08-31  
**生产边界**：`manual_stable` 不变；不启动 Campaign，不自动修改源码，不自动 promotion

## 背景

04 已能读取文献和 GitHub 文件，但过去“读了资料”通常只是通用 Planner 生成报告：没有明确知识问题、
没有选择依据、没有来源指纹、没有证据反馈，也不能区分“知识获取”与“业务已经改善”。首次实机接线还暴露
了两个运行时问题：跨线程 `asyncio.Queue`/`asyncio.to_thread` 的完成通知依赖内部 socket 唤醒；受限环境拒绝
该写操作而 asyncio 吞掉异常，导致消息或步骤看似正常却永远等不到终态。

## 决策

增加一条受限、确定性的研究主动学习链：

```text
research_active_learning_observe
  → research_active_learning_select
  → research_active_learning_investigate
  → research_active_learning_matched
```

- `observe` 只接受允许根目录中的真实文件，记录 SHA256、Git revision、大小和可提取文本；
- `select` 使用 `information gain × task value × novelty − cost − risk` 选择问题/来源；
- `investigate` 保存原始来源身份、证据摘录、命中概念、质量和贝叶斯后验；
- `matched` 比较 first-unread baseline 与 VOI Candidate，最多 `accept_for_shadow`；
- 全链 `production_mutation=false`、`production_effective=false`、`promotion=false`。

研究 JSON 是治理证据，不作为用户成品文件发送。终态单独携带 `completion_evidence_files`，使 Receipt/RL
能引用真实证据，又不会把 JSON 文件或资料阅读冒充业务交付。

## Reward 语义

项目业务进展与知识学习进展保持两条账：

- 有真实业务成品、发现、动作和内容指纹才允许 `business_progress=true`；
- 研究 Event 成功、存在受限治理证据、发现和新指纹时允许 `learning_progress=true`；
- 本轮学习轨迹 reward 为 `0.55`，其中 learning progress `0.30`、novel evidence `0.10`；
- 它保持 `business_progress=false`、`policy_eligible=false`，但
  `learning_observation_eligible=true`；因此可训练/评估研究选择策略，不能证明业务或生产策略改善。

## 运行时修复

- 入口改为线程安全队列，事件线程异步短轮询；消息只在 `put_nowait` 成功后标记已读。
- 四个纯本地研究治理 Event 内联执行，不经 `asyncio.to_thread`。
- STOP_PROJECT 的 Episode reduction、失败观察和可选 cognition mirror 同样以内联的有界文件操作执行，
  避免任务已完成却缺最后消息。
- `instance_runtime.lock` 不再因 PID namespace 视图差异被当成 stale 文件删除，避免同一实例双进程竞争 inbox。
- 显式研究请求绕过 Planner LLM，生成固定四 Event 计划；Planner LLM calls=0。

## 实机证据

### 第一轮

- Task：`fcfe6494-74c6-46b7-b754-ff4963c45743`
- Receipt：`receipt_d12b23ed98a3`
- 来源：Codex `compact.rs`、Hermes `context_compressor.py`、JitRL PDF；均有 SHA256，代码源有 Git revision。
- 选择：上下文压缩问题 → Hermes `context_compressor.py`，VOI `0.4251`。
- 真实证据：源码明确将早期对话压缩为 handoff summary，并要求依照 summary/state 继续而非重做。
- 匹配：baseline evidence coverage `0.372`，Candidate `0.889`，delta `0.517`；
  `accept_for_shadow`，不 promotion。
- 轨迹：reward `0.55`、`learning_progress=true`、`business_progress=false`、
  `policy_eligible=false`。

### 承接续轮

- Task：`058ade34-6a6c-4596-aa9f-a4a162081059`
- 上轮已读选项 novelty 被降权，改选“无梯度更新的运行时反馈” → JitRL PDF；没有重复 Hermes 源码。
- 已存在的 matched experiment 幂等复核，不覆盖实验文件。
- 初版字面提取器把正文中 `Figure 1` 引用误当图注，证据质量 `0.541`，错误产生
  `evidence_found=false` 和支持后验 `0.117`。该负记录保留。
- `semantic_alias_v2` 将 runtime/test-time/current-state、feedback/reward/trajectory、
  improve/learn/adapt 映射为可审计概念，并只惩罚以 Figure/Table 开头的真实图注。
- 同一论文纠正记录 `research_evidence_4f43170693f8ca68` 命中 6/6 概念，质量 `0.991`，
  `evidence_found=true`，支持后验 `0.917`；旧判断未改写。
- 正式 Event-first 复验 Task `6061bc5f-2752-4d92-9ee9-6f9f5edb1625`、Receipt
  `receipt_7e24675adee3`：四 Event 4/4，JitRL 仍为 6/6 概念、质量 `0.991`、后验 `0.917`；
  matched `0.105 → 0.833`，Reward `0.55`，`business_progress=false`、production/promotion 均 false。

## 当前边界与下一阶段

本 ADR 证明的是“04 能围绕项目问题持续选择、读取、记录证据并更新研究选择信念”。它尚未证明：

1. 外部证据已转化为 Partner 代码 Candidate；
2. Candidate 在真实项目任务上优于 baseline；
3. RL 已让业务持续变好；
4. 可以无界自动运行。

下一阶段应要求至少两条互补来源证据和一个 Partner 本地实现证据，生成最小 adoption Candidate；随后冻结
输入、模型、预算和版本，执行独立 baseline/candidate 项目任务，经过 truth/safety、回归和显式
PromotionDecision 后才可能进入生产。

## 实现与验证

- `partner/governance/research_learning.py`
- `partner/v2/research_learning_events.py`
- `partner/planner/batch_planner.py`
- `partner/mind/executor.py`
- `partner/mind/harness.py`
- `partner/governance/manual_runtime.py`
- `tests/test_research_active_learning.py`

定向回归：144 passed（研究、manual stable、04 failure repair 与 Harness integration）。最终全仓回归：
`636 passed in 112.64s`，0 regression；详见 `docs/testing/last_pytest.txt`。

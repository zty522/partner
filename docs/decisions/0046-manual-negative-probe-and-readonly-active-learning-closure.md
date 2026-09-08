# ADR 0046：手动负向探测、只读主动学习与交付前真值门闭合

- 日期：2026-08-31
- 状态：代码、执行层端到端与全仓回归已通过
- 实例：04；实现为手动运行时通用能力
- 前置决策：ADR 0043、0044、0045

## 触发证据

用户在 04 上连续执行三类手动验收，暴露的不是同一个问题：

1. 多源比较任务的六个执行步骤和 Markdown/PDF 生成均成功，但交付发生在最终 Claim 真值门之前；
   随后的 8/9 Claim 拒绝只能把整轮改判失败，无法收回已经发出的文件。
2. “预期文件不存在”的诚实失败测试被 Planner 当作普通业务失败，多次语义重规划，并错误提出生成替代报告。
3. 用户明确授权的只读 Event-first 主动学习任务，先被普通业务计划的控制面禁令拦截，之后 Planner 又猜测目录，
   因而没有形成 `observe → select → diagnose → repair proposal` 的可核验链。

其中第一项 Task 为 `ac197982-55aa-4741-985f-68e66e0e3d3b`：产物真实存在，但 Claim Ledger
遗漏可解析的 `source_path/evidence_quote`，最终治理拒绝。Reward 已如实为负，不能把“文件已生成/发送”
计作任务成功。第二、三项分别是 Task `be5d...` 与 `c21...` 的验收证据；缩写只用于文档说明，权威记录仍是
workspace 中完整 Task 日志。

## 决策与实现

### 1. 真值门前移到外部文件交付之前

- 手动文件任务先解析候选产物，再执行只读 `preflight_manual_artifact_truth`；Claim 或来源门失败时不得入
  delivery queue。
- 最终 outcome 仍只由原有治理入口持久化一次。预检不提前创建 Receipt、Issue 或轨迹，避免双重记账。
- 真值拒绝保留 `failure_owner=verification`、`mechanism=claim_level_truth_gate`、完整 audit 和 partial artifact；
  不再被后续“未交付”粗化为普通 delivery failure。

### 2. Grounded Claim Ledger 合同修正

- Verified-source 后处理不再删除模型 Ledger 后追加无法归属的 footer；现在为每个真实命名来源生成一条
  `AUTO-SOURCE-###` direct Claim，包含绝对 `source_path`、`source_identity`、该源逐字连续引文和 rationale。
- 解析器允许文档规定的 `source_identity` 位于 `source_path` 与 `evidence_quote` 之间，消除“协议要求字段，
  解析器却拒绝该字段”的内部矛盾。
- 旧的悬空 source/evidence 行会被移除，避免错误路径混入最终产物。

### 3. 预期不存在是一次成功观察，不是重试目标

- 明确包含“预期不存在/不得生成替代文件/不得重复尝试”的请求被折叠成一个
  `atomic_inspect_file`，并标记 `_expected_missing_probe=true`。
- 首次实现仍错误地把预期缺失写为 `ok=false`，导致红叉、Remediation、Issue、负 Reward 和失败 Task；真实
  Task `50fcf401-ec6a-4794-87d3-5420ed5a2fb9` 证明“禁止重试”不等于语义正确。
- 最终实现将其表示为 `ok=true / exists=false / observation_met=true`，责任为 `input_state`，机制为
  `observation/expected_missing_input`。这是完成预期观察，不是用户错误或系统故障；轨迹为 monitor-only、Reward=0。
- 该分支不生成报告、不推送文件、不以“发送成功”结束，也不触发同参数语义重规划。

### 4. 显式授权的只读主动学习使用确定性 Event 链

- 只有消息同时明确 Episode、主动学习、只读/不改生产以及不 promotion 时，manual preflight 才限域放行四个
  Event：`agent_active_learning_observe_episode`、`agent_active_learning_select`、
  `agent_active_learning_diagnostic_shadow`、`agent_active_learning_propose_episode_repair`。
- 新的 observe/propose handler 读取真实 Episode；proposal 复用受限失败观察能力，并把审查 JSON 写在本次
  Task 目录。结果显式包含 `production_mutation=false`、`production_effective=false`、
  `control_policy_modified=false`、`promotion=false`。
- 这是一条用户授权、单轮、有界、可审计的控制面实验路径，不开放普通业务消息调用任意自治 Event。
- 执行层测试还发现这些 handler 经通用 worker thread 时可能在 observe 后无 terminal；四个 handler 均为
  有界、无网络/GUI 的本地文件治理操作，现由 Event executor 确定性内联顺序执行。

### 5. 失败消息与终止原因统一

- 失败步骤不再推送 partial files；最终消息使用真实 step error、failure owner 和 mechanism。
- 普通手动文件成功原因统一为“最终验收与文件交付均已通过”，纯文本成功原因为“纯文本结果、最终验收与
  消息交付均已通过”。删除本次路径上 `deliverable file sent successfully` 等内部英文原因。
- 通用 batch planner 的确定性 fallback 仅保留分子基准适用域；任意 Planner prompt 失败不再静默执行无关任务。

## 验证

- `tests/test_manual_acceptance_closure.py` 现覆盖 grounded ledger、单次缺失探测、成功负向观察 Task 终态、
  确定性四 Event 计划、真实 handler 4/4 执行及 task-local 结果。
- `tests/test_manual_governance.py` 覆盖负向观察 accepted、monitor-only、Reward=0、policy ineligible。
- 相关手动治理、失败修复、active-learning、Episode/Event-first 和 canary 回归全部通过。
- 实例 04 自动负向观察 canary：Task `e8f61ac9-ef0c-45cd-b773-784987023701`，Task/governance 均成功，
  Receipt `receipt_75d400128d24`，Reward=0、monitor-only、policy-ineligible，收到/开始/绿色完成/总结/终止
  消息均获 QQ ACK。
- 实例 04 自动只读主动学习 canary：Task `b2af8f38-3bdf-4095-be6d-1f5b9c7d9fc9`，四 Event 4/4，
  Receipt `receipt_28e35c149972`；审查制品确认 `production_mutation=false`、`production_effective=false`、
  `control_policy_modified=false`、`promotion=false`，文件交付和终止消息均获 ACK。
- 首轮 canary `traj_manual_97060e88fc060f4f` 因 STOP_PROJECT 漏传 observation provenance 被错误计为
  -0.45；修复后追加 revision 2 校正为 0，原始行与 Task 日志保持不可变。
- 相关回归：`181 passed`；最终全仓：`623 passed in 224.46s`，0 regression。

## 能力边界与下一验收

- 本 ADR 已同时证明代码合同、Harness 执行层、治理、真实 04 实例和 QQ ACK；以后同类回归由自动本地消息
  canary 承担，不再要求用户逐条充当测试员。
- 重启后应按原三条消息复验：多源任务必须在真值失败时不发送文件；预期缺失必须只检查一次并解释；只读
  主动学习必须展示四阶段、真实 Episode/轨迹路径和 `production_effective=false`。
- 该受限链不是无界自治、自主改源码或“RL 已让所有业务持续变好”；任何生产策略修改仍需独立 Candidate、
  matched experiment 和 PromotionDecision。

# ADR 0069：原生循环的新颖性 Reward 与机制级主动学习真值

**状态**：Accepted / production loaded  
**日期**：2026-09-07  
**依赖**：ADR 0068 原生项目 Event 路由

## 问题

长时间生产观察证明五实例已经能轮转并生成真实产物，但暴露了三类假进步：

1. 项目交接中提到的 `continuation.md` 被通用验收器误当成当前轮输出；分子数值报告因标题含“方法/效果”被误套三引用门。
2. `continuous_project_step` 的结果存在两层 envelope，摘要没有被读取，真实 Event 被压成“已完成本地微计划执行”，导致错误负 Reward 或重复门误判。
3. native matched 实验只验证“能路由到同项目 Event”，即使 Episode 的 `failure_classes=[]` 也写 `passed`；同时有限策略循环的相同结论可重复获得正 Reward。

## 决策

- 原生项目只由 Event 声明的 typed artifact contract 决定输出；项目交接路径是输入，不再隐式推断为输出。
- 原生项目数值/机器产物不套用研究报告引用门或 Claim Ledger 门；普通研究报告和 Candidate 文本仍保持原门槛。
- Harness 同时识别 direct result 和 typed step envelope，Receipt 必须保存 Event 的具体测量摘要。
- native matched 实验必须具有非空 failure class，并把该类绑定到实际 Candidate Event/边界；空机制一律 rejected，不能以路由成功替代因果匹配。
- `rejected` / `inconclusive` 是 Candidate 实验的有效结论，不是 Event 执行故障：学习任务应持久化该负结果后正常结束并返回项目；只有实验未执行/证据未落盘才判学习失败。对 `repeated_findings`，若源动作身份缺失，`candidate_changes_action` 必须 fail-closed。
- 对旧 reducer 遗留的空 failure class，只能从不可变 `trace.jsonl` / task log 恢复 typed acceptance signal；不改写旧 Episode 和旧实验。
- 同项目、同 Event、相同语义 finding 的再次出现标记 `duplicate_outcome=true`，不算 `business_progress`，不得进入 policy eligibility，Reward 为 `-0.1`。文件重新生成或 PDF hash 改变不能制造新颖性。
- `duplicate_outcome` 同时投影为 Episode failure class `outcome.duplicate_semantic_result`，触发一次受限项目内学习中断；学习结束返回项目。它不是工具执行失败，也不能被完成状态吞掉。
- runtime 每个 watchdog sweep 只采用一个权威 slot 快照：先停止非选中进程，再补齐该快照，禁止二次重算造成三进程短时重叠。

## 生产证据

- 五实例均已有修复后真实 Receipt。代表样本：
  - 01 `receipt_01fb635db04a`，随后新版本连续来源/风险/编辑步骤均得到具体 finding 与 `reward=0.85`；
  - 02 `receipt_164f19c0a190`，真实分子合成基准通过；旧 generic finding 导致的 BLOCKED 已按外部代码与测试证据解锁；
  - 03 `receipt_5b3d7845dd0d`，5 个 velocity-Verlet timestep 实验稳定，最差相对能量漂移 `5.1700958952867536e-05`；
  - 04 `receipt_36ce0c1c85bc`，Harness 合同映射通过；
  - 05 `receipt_272fed415a5a`，读取 4 份源码合同、聚焦回归通过并形成 1 个 Candidate spec。
- 机制实验：
  - `native_episode_de15fa6637888cd6_mechanism_v3` 因机制为空被 `rejected`；
  - v4 从 archived trace 恢复 `verification.acceptance_contract/implicit_handoff_artifact` 与 `named_artifact:continuation.md`，机制绑定通过，`candidate_validated`，`production_effective=false`。
- 02 生产链已实跑 `project/repeated_findings → observe/select/diagnose/propose/matched → native_learning_observation_recorded → resume project`；实验拒绝不再把学习任务打成失败。后续对缺失 source action 的“改变动作”声明按 fail-closed 记录 rejected。
- systemd 生产运行器保持最多两个实例子进程；旧单 tick 二次重算已移除并有源码契约测试。

## 仍然不能宣称的事

- 这证明了持续项目 Event、失败观察、机制恢复、Candidate 验证和 Reward 防重复已经接通；不证明长期 RL 已统计成熟。
- 当前五项目仍是有限的确定性策略集合。没有新输入、代码变化或真实实验差异时，应降低/拒绝重复 Reward，不能靠持续生成相同 PDF 制造学习数据。
- Candidate 验证不等于自动生产晋升；仍须跨日期、足够样本、独立硬门与可回滚 canary。

## 回滚

代码回滚不删除任何 Episode、Receipt、trajectory 或 promotion decision。v3 rejected 与 v4 passed 都作为 append-only 审计证据保留。

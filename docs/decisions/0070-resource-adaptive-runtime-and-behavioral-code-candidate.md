# ADR 0070：资源自适应运行与行为型生产代码 Candidate

**状态**：Accepted / production loaded / longitudinal validation open  
**日期**：2026-09-08  
**依赖**：ADR 0060、0068、0069

## 问题

固定双槽浪费空闲资源，配置为五槽又可能在内存压力下失控；同时旧自进化 apply 路径会把一条注释追加到
Python 文件并称为代码 Candidate。这既没有改变行为，也不能证明 Reward 或主动学习带来了生产改善。
五实例验收脚本还只投递 01/04，重启恢复任务可能抢在显式验收消息之前。

## 决策

1. 调度器使用实时 `CPU count + loadavg(5m) + MemAvailable` 计算容量，配置仅作为 1–5 的上限；宿主保留
   2 CPU/2 GiB，每实例预算 2 CPU/1.5 GiB。资源下降采用自然排空，不抢杀在途任务。
2. 显式 inbox 消息优先于自动恢复与新项目 seed；`run_seed_all.py` 每次生成唯一 ID，覆盖五实例并支持子集。
3. 删除无 diff 时制造 comment-only 补丁的生产入口。没有行为改变、测试假设和 rollback 的文本提案不能进入 apply。
4. 首个代码生成器只开放一个窄白名单：读取真实 MD 重复轨迹，生成 `generated_candidate_policy.py` 的参数轮次
   策略 diff；在隔离副本完成 matched probe，应用后跑聚焦回归，失败立即恢复 preimage。
5. Candidate 的权威链是 Event-first：`candidate/proposed → experiment/started → experiment/completed →
   policy/promoted → policy/activated`。Task JSON、patch 和报告只是被引用的产物。
6. 05 的 `outcome.duplicate_semantic_result` 学习中断不再用无关的 output-reference fixture 验证；它在
   observe/select/diagnose/proposal 后调用上述机制匹配的行为型 Candidate Event。其他未知机制继续 fail closed。

## 生产证据

- Candidate：`code_candidate_md_novelty_20260907234635`。
- 行为对照：baseline 的 turn 7/8 均无变化；Candidate 输出不同 `candidate_variant` 与安全范围内的 timestep/
  temperature 设计；聚焦回归 13 passed，生产文件 SHA-256 与 preimage 均记录。
- 治理：Experiment `experiment_8f3246270609`、PromotionDecision promoted、激活 Event
  `evoevt_45a8cf0a9b13c6cfed39`，`production_effective=true`。
- 05 Task `21ccbb6b-de40-4dae-98cd-c1bf961b5a55` 产出任务本地 JSON，检查通过并获得真实 QQ 文件 ACK。
- 第二 Candidate `code_candidate_research_novelty_20260908005532` 从 04 重复轨迹选择
  `turn_source_rotation_v1`，baseline fail / Candidate pass，15 项聚焦回归通过；激活后 04 实际读取 Codex
  rollout-trace README（8345 bytes，SHA-256 `5d8c177a38dec8e8...`）并交付。此前两次错误构造均 rejected，
  证明 hard gate 会阻止错误 baseline 和畸形 diff 进入生产。DeepSeek→Codex 两轮轨迹均为 Reward=0.85、
  business_progress=true、duplicate=false；来源轮转耗尽后重复结果仍会降为 -0.1。
- 资源：00:23 快照 22 CPU、约 10.5 GiB available、load 1.90，effective=5；01–05 五进程均在线。
- 全仓：`942 passed, 2 warnings in 327.70s`。
- 历史 ledger 审计（01:29）：7192 条记录的单条 hash 验证通过；退休并发 writer 的 3248 条 unkeyed、11 个 fork、
  192 个 seq collision 作为显式 anomaly 返回。2026-09-07 后当前 writer 不享受兼容例外。

## 对 RL 与自进化的定义

RL 不是自进化的同义词。主动学习选择“最值得补哪条证据”；Reward 与后验更新比较“哪类动作更有效”；
Candidate 合成器负责提出可执行干预；matched evaluator、PromotionDecision 和 rollback 决定是否进入生产。
本 ADR 证明同一失败机制在 MD 与源码学习两个领域能形成行为型闭环；仍不证明通用 LLM 能稳定发明任意修复，
也不证明跨日期长期 RL 已成熟。

## 剩余门槛

- 01/04 当前真实验收仍是重复结果，Reward=-0.1；需由新输入/新来源驱动不同动作，不得重复写报告刷分。
- 至少再覆盖两个不同 failure mechanism，并跨真实日期重复 baseline/candidate 与 rollback 演练。
- Candidate recipe 的增加必须逐项白名单化；通用 LLM 只能提出 patch，不能绕过 frozen tests 和独立决策门。

## 回滚

降低配置上限即可减少并发；在途任务自然结束。代码 Candidate 失败时恢复保存的 `preimage.py`。回滚不删除
Episode、trajectory、Experiment、PromotionDecision 或 Event；历史 comment-only 事件保留为负证据，但该入口不再可执行。

# ADR 0053：即时串行执行与跨项目失败修复闭环

**状态**：Accepted；04/05 即时串行队列已实机验证，完整长期 RL 仍 blocked  
**日期**：2026-09-01  
**模型边界**：MiniMax-M3 only；没有 DeepSeek fallback

## 问题

首日 matched 采样一次向 inbox 写入多条 USER_MESSAGE。入口会先把消息快速转换成 BATCH_PLAN；旧逻辑随后
把尚未执行的计划合并为最后一条，留下多个永远不会执行的 TaskInstance `pending`。此外，宿主机残留的旧
04/05 进程与新进程同时消费 inbox，使旧代码合同、`Updates.pdf` 假输出要求和重复步骤消息污染新实验。

这两类问题都不能靠缩短 30 分钟 scheduler 周期解决。scheduler 是健康/未来日期采样脉冲，不是任务队列的
执行时钟；真正需要的是“同一实例 FIFO、任务终态后立即取下一条、两个实例最多并行两槽”。

## 决策

1. 带完整 `experiment_id + match_key + policy_arm`、`execution_mode=serial_queue`，或来自长期采样器/串行
   控制器的 USER_MESSAGE 是独立审计样本，禁止合并 BATCH_PLAN。
2. 串行实验任务绕过 60 秒内容完成去重；事件 ID 去重仍保留。否则合法重试会被吞掉并留下假 pending。
3. 新增 `scripts/run_serial_experiment_queue.py`：04/05 可并行，各实例内部一次只投一条；等待 TaskInstance
   经 Harness/Claim/交付/Reward 变为真实终态后立即投下一条，不等待 scheduler 周期。
4. 旧的非终态镜像不改写成成功：统一关闭为
   `event_queue/experimental_batch_plan_merge`，记录 redrive，再以新 TaskInstance 执行。
5. Candidate Claim 合同新增单路径不变量：跨来源比较只能写正文；Ledger 中每条 Claim 只能逐字绑定一个
   verified absolute source path，禁止逗号、分号、数组或连接符拼接多个路径。硬门没有降低。
6. 同时运行的每个实例必须只有一个宿主进程。运行锁仍 fail closed，但诊断必须同时核对宿主进程，不能仅凭
   当前沙箱 PID namespace 或 lock 文本断言唯一实例。

## 真实实验

### v4 队列恢复

- 对遗留队列完成诚实 reconciliation：04 重投 1 条，05 重投 2 条；每个实例内部都实现完成后立即接续。
- 04/05 baseline 均因缺少冻结双源 Claim 证据被真值门拒绝，Reward `-0.4`，成为有效负样本。
- 05 v4 Candidate 两条来源与 11 条 Claim 中 10 条通过；唯一失败 Claim 拼接两个 source_path，Reward
  `-0.4`，自动生成 mechanism-specific repair proposal。

### v5 最小修复 matched 实验

- 04 `day1_literature_repair_pair_01`：baseline `-0.4`；Candidate Task
  `b485a656-1f61-475e-ad18-7452a5cfe0e0` 完成，Claim truth score `0.85`，Reward `+0.8`。
- 05 首次执行暴露宿主双进程。精确终止旧 04 PID 48490、旧 05 PID 21535 后，各实例只保留一个新进程。
- 05 干净重试 Task `75b7417a-8e48-4c59-8304-87543f25f452` 完成，Claim truth score
  `0.8571428571`，Reward `+0.8`；没有再次出现 `Updates.pdf`。
- 结果文件：`share/mind/governance/experience_guided_policy/serial_runs/experiment_research_business_v5.json` 与
  `experiment_research_business_v5_05_retry2.json`。

这证明了一个真实的短闭环：`失败轨迹 → 机制诊断 → 收紧 Candidate 合同 → matched 重跑 → 两项目正向
Reward`。它不证明 20/arm、三日期窗或 Wilson 下界已经满足，因此 full production readiness 继续 blocked。

## 验证

- 相关回归：`152 passed in 10.95s`。
- 完整回归：`679 passed, 2 warnings in 150.29s`。
- 宿主进程最终核验：04、05 各一个运行进程。

## 后续运行语义

已有队列不等待周期；终态后立即接续。真实日期窗口仍不能伪造：采样器可频繁复审，但同一天只登记一次，
下一真实日期到来后再创建新窗口。当前 7 天 supervisor 运行参数为 `--interval 300`，因此新日期最多等待
5 分钟，不再按 6 小时 sleep；等待真实日期不是任务调度空转，也不得通过改时间戳冒充长期证据。

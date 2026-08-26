# Campaign `campaign_46a3b906ffee` 两小时实机审计

## 1. 结论

这次运行不能验收为“五实例持续迭代成功”。它在 14:26–16:27 确实轮换启动了
01–05，最大同时活动数未超过 2；但预算、收口、任务身份、轮转公平性和自进化都存在实质故障。

## 2. 客观数据

| 项目 | 声明预算 | 实际 |
|---|---:|---:|
| 运行时间 | 7200 s | 约 7200 s |
| 最大活动实例 | 2 | 未观察到超过 2 |
| WorkItem | 12 | 18 |
| 失败 | 3 | 16 |
| retry | 每项 1 | 累计 9 |
| 可转 RL 的非报告终态 | - | 10 |
| 正式 Experiment | 应产生 | 运行期间 0 |

“failure budget exhausted”最终日报在 14:30 生成、14:57 送达，但 Controller 仍继续派发
03/04/05 直到 16:27。这证明“报告已发”和“调度已停”原来没有硬状态门。

## 3. 实例结果

- **01**：Campaign 内任务只留下登录页证据后租约失效。用户登录后，另一条非 Campaign continuation
  成功进入编辑器并发送截图。业务有真实进展，但被两个 Controller 分割。
- **02**：`molecular_data_readiness_audit` 生成 4 个真实产物并获得交付回执，
  以缺失目标/活性数据的证据边界 blocked，是本轮唯一正奖励确定性业务动作。
- **03**：Interaction 与 Executor 建立了不同 TaskInstance；Campaign 找到不完整记录并最终 watchdog blocked。
- **04**：泛化文献规划超时，没有消费 `external/` 的真实资料。
- **05**：长时占槽；失败的“验证 Issue”产生新 Issue，新 Issue 又生成 evolution WorkItem，
  形成派生风暴。`experiments.jsonl` 和 promotion decision 在运行期间均为空。

## 4. 根因与修复

1. 报告豁免创建预算却计入 usage。现在总预算包含报告，并为最终日报预留槽位。
2. 停止边界没有 latch。现在失败/截止时间/模型/成本边界持久，并取消未开始业务项。
3. Executor 只读 `task_id`而 Interaction 传 `task_instance_id`。现在两者均可复用，不再重复建任务。
4. 03/04/05 的泛化 planner 无有限协议。默认改为 `framework_campaign_contract_audit`、
   `external_learning_index_slice` 和 `offline_rl_self_evolution`。
5. evolution 失败可递归派生。现在每次只物化一个根 Issue，实验失败不再生成新 evolution 源。
6. 旧运行的 10 个业务终态已写为离线 RL 轨迹，并生成首个正式 candidate Experiment。

## 5. 尚未证明

修复有合同测试，但还没有在新代码上重跑两小时实机 Campaign。
当前只能说“根因已修、旧运行已转换为学习证据”，不能说“长跑已通过”。

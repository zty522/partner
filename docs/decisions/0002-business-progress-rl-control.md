# ADR-0002：以业务增量驱动 Receipt continuation 与 RL 控制

- 状态：accepted
- 日期：2026-08-24

## 背景

旧循环把 WorkItem completed、PDF、QQ 回执和新的文件哈希当成主要正奖励。结果是 scout 与 05 审计看起来收益最高，却不改变下一事件；Receipt 常写空 `next_actions`，实例清理后产物路径又失效。

## 决策

1. 每个成功的业务 WorkItem 先归档到 `share/evidence/{project}/{campaign}/{work}/`，Receipt 只引用持久路径。
2. 以去除路径、时间和命令字段后的机器 JSON 计算 `outcome_fingerprint`；只有项目语义结果变化才标记 `business_progress=true`。
3. Receipt 必须给出一个可执行 NextAction，或明确外部输入/批准边界。下一动作由 Campaign 入队并记录 proposed→queued→running→completed/blocked。
4. RL v2 只训练 01–04 的业务 `project_iteration`；audit、report、05、自评和 no-change 全部 `policy_eligible=false`。
5. 未晋升策略交替执行 baseline/candidate。每臂至少 3 个有效样本，candidate 成功率至少 0.67 且平均 reward 增益至少 0.15，才写 promoted PromotionDecision；否则 rejected/inconclusive。

## 后果

完成数会下降，但“持续运行”开始表示项目证据在推进。首轮 canary 尚未证明长期 promotion 有效；必须用真实 Campaign 累积双臂样本，不得把单次测试通过写成 RL 已学会。

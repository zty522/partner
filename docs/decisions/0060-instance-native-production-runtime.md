# ADR 0060：用实例原生事件循环替换长期 Campaign

- 状态：Accepted / Production Active
- 日期：2026-09-02
- 前置：ADR 0059

## 决策

长期运行采用实例原生状态机，而非 Campaign 周期控制器。五项目全部进入同一合同，最多双槽；项目权威终态立即
触发下一项目动作，只有真实失败、冲突、不确定性或知识缺口才插入一次 Episode-grounded 主动学习。学习通过后
立即恢复原项目。Campaign 只保留为 bounded benchmark、matched experiment 和审计工具。

## 原因

旧 Campaign 把控制面周期摘要呈现成项目进度，固定课程与人的“工作中遇到问题才学习”不一致，并造成业务 Receipt、
学习 Reward、报告 delivery 和 continuation owner 混线。实例原生设计把项目连续性放回实例，把全局层缩小为资源仲裁。

## 生产证据

- 旧 `partner-sprint18@campaign_74833fad4af8.service` 停止且禁用，历史账本保留。
- `partner-instance-native.service` enabled/active；生产槽账本最大 2，实跑已覆盖 04/05/01/02/03，当前为 02/03。
- 04 真实学习任务 `c7621557-b46b-455a-bc15-8f2be5474f3e` 和
  `540ee115-0179-4bf5-9cf9-cc0732f974c8` 均执行四 Event、生成审查 JSON、通过交付，并由权威终态立即恢复项目。
- 重启孤儿、无 Episode、通用 citations 门、同参数超时重试和 `filename` writer 缺口均由真实失败发现并修复；
  失败轨迹未删除。
- 05 两个项目动作成功并让槽；01/02/03 均完成一次失败触发的确定性学习并恢复业务项目。
- 控制器重启不再重选固定首槽、打断在途项目；一次学习应用后若业务仍失败，该实例让槽而非无限学习重试。
- 完整回归 `746 passed, 2 warnings in 259.83s`。

## 限制

本 ADR 证明长期控制架构已接入生产，不宣称长期 RL 成熟。Promotion 仍要求 matched baseline/candidate、真实业务
Reward、跨项目/跨日期样本、false-success=0、抗遗忘和 rollback 门。外部副作用与生产修改仍须审批。

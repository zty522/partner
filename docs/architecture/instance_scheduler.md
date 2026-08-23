# 五实例双槽位调度

## 约束

- 可管理实例：01–05。
- 同时 active 不得超过 2。
- 当前默认槽位：01、02。
- pause 必须先持久化，再停止 service；resume 必须先检查槽位，再启动 service。
- 调度器不得自动启动用户没有放入槽位的 03–05。

## 实例长期职责

| 实例 | 职责 |
|------|------|
| 01 | 小红书账户推送与维护 |
| 02 | 分子生成方法创新与实践 |
| 03 | Partner 框架与前端优化 |
| 04 | 文献/GitHub 真实拉取、运行与学习 |
| 05 | Agent 自进化探索与验证 |

## 换出检查点

记录当前 project_id、最新 receipt_id、queued/running action、blocked reason、resume_event、未发送产物。
换入时优先恢复已 queued/running 的工作，不从项目背景重新规划。

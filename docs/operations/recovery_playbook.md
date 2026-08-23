# Campaign 故障恢复

1. 运行 `partner_campaign.py status`，不要先删除状态。
2. 检查 runner unit、活动槽和 Partner unit。
3. queued/running WorkItem 若有任务目录，让下一个 tick 自动 reconcile。
4. 租约未过期时不要手工重复注入。
5. 租约过期会自动重试；超过 max_attempts 后 blocked。
6. 缺数据/登录/权限时保留 blocked 和 resume_event，获得条件后创建新 WorkItem 或 resume。
7. Candidate 改动回归失败时执行 rollback/non-adoption，不得直接重启推广。

禁止删除 `state/campaigns/` 来“解决”卡住；这些文件是恢复和审计依据。

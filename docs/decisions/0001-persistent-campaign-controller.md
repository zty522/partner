# ADR-0001：使用持久 Campaign Controller 管理长期自治

**状态**：accepted  
**日期**：2026-08-23

## 决策

长期运行由独立、持久、确定性的 Campaign Controller 管理；Partner 实例只执行有边界 WorkItem。
Campaign WorkItem 禁用旧的进程内 Research Loop 续跑，由 Controller 根据 Receipt 和证据创建下一轮。

## 原因

旧方案把“进程一直 active”和“项目一直推进”混为一谈；Research Loop 状态在内存中，重启丢失；
外部 Agent 会话结束后也没有所有者继续消费下一步。单一持久控制层可以提供租约、预算、恢复和审计。

## 后果

- 好处：可跨重启恢复、双槽硬门、任务不重复、成本/失败有界、自进化后可返回项目。
- 代价：Campaign 与实例任务之间需要 marker 和完成回调；状态文件增多。
- 兼容：普通用户任务仍可使用旧 Research Loop；只有带 Campaign marker 的任务改为 Campaign 单轮所有权。

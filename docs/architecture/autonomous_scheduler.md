# Campaign 自动调度与双槽约束

## 调度规则

1. 全局最多两个活动实例。
2. 已 leased/queued/running 的 WorkItem 保持实例槽位。
3. 空余槽按 priority、创建时间、work_item_id 稳定排序选择。
4. 同一实例同一时刻只执行一个 Campaign WorkItem。
5. `human_required` 和 `forbidden` 项不自动调度。
6. 切换时先持久化 scheduler 状态，再停止移出槽位的 unit，最后启动选中 unit。
7. Campaign 创建时保存原活动槽；正常结束后恢复原槽位，避免长跑结束后改变日常运行基线。

## 五实例职责

| 实例 | 项目 ID | 长期职责 |
|---|---|---|
| 01 | xiaohongshu_operations | 小红书维护、可见浏览器、截图/读图/交付 |
| 02 | molecular_generation | 分子方法、实验和证据边界 |
| 03 | partner_framework_frontend | Partner 框架与前端 |
| 04 | literature_github_learning | 论文/GitHub 拉取、复现、学习 |
| 05 | agent_self_evolution | Issue/Experiment/Promotion 验证 |

服务 active 只表示进程存在；WorkItem 有 task ID、任务目录和执行证据才表示正在工作。

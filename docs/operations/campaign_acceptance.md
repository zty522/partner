# Campaign 验收标准

## 模拟验收

- 两槽上限在所有调度路径成立。
- 同一 tick/restart 不重复 dispatch。
- queued → running → completed 可从持久 task log 重建。
- 租约超时按预算重试，耗尽后 blocked。
- 三轮相同证据触发熔断。
- 人工门动作不 dispatch。
- deadline/预算到达后只运行最终日报。
- work-item 创建预算耗尽时，先排空已准入的主工作项，最终日报不得抢跑。
- 明确文件名不能被宽泛 glob 替代，PDF 字节不能伪装成 Markdown。
- `completion_status=done` 后若最终 LLM check 为 false，Controller 必须保持 running。
- retry 的 task/message ID 必须随 attempt 改变；cancel 后不得残留 active Lease/running WorkItem。

## 实机验收

依次进行 30 分钟、2 小时和整夜 soak。每次检查：

- 实例不超过两个。
- 每轮有真实 task ID 和 Receipt。
- 下一轮承接上一轮产物。
- Issue 只来自可复核信号。
- 自进化有 Experiment 与 PromotionDecision，并回到原项目。
- 阶段/最终 QQ 摘要有 delivered 回执。
- 没有重复刷屏、无限重试或无新证据的机械轮次。

只有前一阶段无关键缺陷才进入更长 soak。

## 最近一次短程 canary（2026-08-23）

- 已证实：01 打开已登录的小红书发布页；关键截图真实生成；视觉模型返回页面描述；多次文本/文件回调获得确认；缺少指定报告或报告格式错误时会进入下一迭代而不是结束。
- 已拒绝：仅有 delivery 回执、错误文件名匹配、PDF 二进制伪装成 `.md`、最终 LLM 验收未通过。
- 未通过整轮：QQ 文件 API 后段出现间歇性连接失败，最终验收未形成，Campaign 已取消并恢复 01/02。
- 恢复条件：确认 QQ 文件上传连续稳定，重新运行 30 分钟 canary；成功后才允许 2 小时和整夜 soak。

## 30 分钟 canary：campaign_a06e75ccfa0f

- 12:40 启动，deadline=13:10，实例限制 01/02，最多并发 2。
- 01：3/3 截图、3/3 视觉描述、对应文件/文本回执及最终摘要均真实送达；安全边界后 blocked。
- 02：受控扫描完成，详细 PDF/Markdown/JSON/数据契约生成，PDF 与摘要真实送达；缺目标活性数据后 blocked。
- 12:50 首个阶段报告暴露旧进程和路由条件缺陷；两次旧执行明确记失败，没有伪装成功。
- 12:53 确定性替代 WorkItem 真实发送阶段报告，未再调用 LLM 或发送内部进度噪声。
- 最终判定待 13:10 最终报告和 01/02 槽位恢复证据；在此之前状态为“进行中”。

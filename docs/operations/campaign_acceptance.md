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
- 每个非报告 WorkItem 都有真实送达的 instruction_received/started/executed/verified/finished；消息必须用既有项目化格式复述清理后的业务指令，说明承接、精简的实际命令或事件、机器验收和文件送达，再给出下一步。内部控制标签和冗长绝对路径不得面向用户展示。
- 抽查 PDF 正文是领域化信息结构，不是所有实例共享固定章节或大段原始 JSON。
- 没有重复刷屏、无限重试或无新证据的机械轮次。
- execution profile 每轮都有实际源码、进程退出码和机器结果；只写计划/PDF 不算推进。
- 05 在 01–04 全部终态后运行，并对 Experiment 写出显式决策；candidate 不等于 promoted。

只有前一阶段无关键缺陷才进入更长 soak。

## 用户可观察性 canary（2026-08-24）

- `campaign_104d093f2287`：3/3 completed，验证三阶段 QQ 消息和新版实例 03 报告；暴露“文件实际送达但收尾写未确认”的返回合同差异。
- `campaign_4faa4352f48b`：修复后 2/2 completed、0 failure、0 retry；QQ history 出现 `报告送达：已确认`。
- 新版 PDF 真实渲染为 2 页，中文、标题层级、证据块、页眉页码正常。该短测只通过用户体验合同，不替代五实例长时 soak。

## 最近一次短程 canary（2026-08-23，campaign_653873ef41c2）

- 五实例声明式主阶段 5/5 收口，检查点和最终日报均真实送达；最终 7/7、0 failure、0 retry。
- 01 的 3 个关键步骤均有截图、qwen3-vl-flash 描述、图片和文字回执；02–05 均有原始证据和 PDF 回执。
- final sync 补齐 01、05 后，本轮五条业务轨迹全部进入 RL；candidate Experiment 没有自动晋升。
- 本轮通过的是审计型短 canary。下一门槛是 Sprint 11 execution profile 的 2 小时实跑，
  必须观察真实代码/拉取/运行/分析，而不是重复审计。

## 30 分钟 canary：campaign_a06e75ccfa0f

- 12:40 启动，deadline=13:10，实例限制 01/02，最多并发 2。
- 01：3/3 截图、3/3 视觉描述、对应文件/文本回执及最终摘要均真实送达；安全边界后 blocked。
- 02：受控扫描完成，详细 PDF/Markdown/JSON/数据契约生成，PDF 与摘要真实送达；缺目标活性数据后 blocked。
- 12:50 首个阶段报告暴露旧进程和路由条件缺陷；两次旧执行明确记失败，没有伪装成功。
- 12:53 确定性替代 WorkItem 真实发送阶段报告，未再调用 LLM 或发送内部进度噪声。
- 13:00 第二份阶段报告按时真实送达，期间没有业务重复、cron 插队或新增模型调用。
- 13:10 最终报告真实送达，内容区分“业务轮次 2/2 受控收口”和历史报告链问题，并说明 deadline。
- 最终验收曾错误把固定 `campaign_report_delivery` 同签名当成业务重复实验；修复为 report 豁免后，
  最终 WorkItem 校正为 completed，Campaign=completed，01/02=active。
- 账本保留真实异常：2 次旧报告路由失败、1 次重试、11 次模型调用（3 次视觉 + 8 次被中止旧路由调用）。
  因而本轮判定为“30 分钟通过但运行中发现关键缺陷并完成修复”，不是零缺陷 soak。

# 五实例修复 canary：`campaign_653873ef41c2`

## 1. 验收结论

本轮证明五实例能够在双槽限制下依次执行声明式主阶段，并以真实产物和 QQ callback 收口。
它是修复后的短程 canary，不等同于 2 小时或整夜稳定性证明。

## 2. 主阶段事实

| 实例 | 终态 | 可验证结果 |
|---|---|---|
| 01 | blocked（受控） | 已登录小红书发布页；3 个关键步骤各有 PNG、qwen3-vl-flash 描述、图片和文字发送确认；未上传/发布 |
| 02 | blocked（受控） | 有界扫描 3001 个文件，未发现分子+靶点+活性联合数据；Markdown、2 页 PDF、校验 JSON、数据契约已发送 |
| 03 | completed | 实际运行 30 个 Campaign/RL 合同测试，全通过；Markdown、JSON、2 页 PDF 已发送 |
| 04 | completed | Polar、RLVR-World、SESA、JIT-RL 4/4 来源存在并记录哈希；只标记 indexed；Markdown、JSON、PDF 已发送 |
| 05 | completed | 写入离线轨迹、candidate policy、Issue 和 candidate Experiment；2 页 PDF/JSON/Markdown 已发送；无自动晋升 |

主任务严格为 5 个，没有额外 evolution 派生，也没有泛化续写。5 分钟时另创建并真实发送 1 个
检查点报告；截止时 final sync 补入 01、05 两条轨迹，再由 01 真实发送最终日报。
最终账本为 7/7 终态、0 failure、0 retry、3 model calls、3 cost units；Campaign 于
17:50:26 以 `campaign deadline reached` completed，并恢复 01/02 常驻槽。

## 3. RL 是否起效果

是，但边界必须说清楚：05 确实读取持久 WorkItem 结果，写入带 reward decomposition 的 JSONL，
更新保守 contextual-bandit candidate policy，并建立正式 candidate Experiment。它从历史证据中选择
了重复 9 次、均奖励约 -0.83 的 `05:evolution_experiment:generic_or_unobserved` 作为主要风险，
而不是凭反思文本宣称进化。样本门槛未达到，所以没有 PromotionDecision。

本轮还发现 05 原先可能早于慢任务完成，只摄取“半轮”结果。修复后，05 必须等待同 Campaign
的 01–04 非报告任务全部终态；Campaign 在最终报告前再幂等同步一次，补齐晚完成任务与 05 自身。
本轮 final sync 的事件明确记录 `new_trajectories=2`，最终该 Campaign 恰有 5 条非报告轨迹。
目前只有 02 的数据就绪审计达到离线策略的 canary 样本门槛；这不等于生产晋升，仍需
Experiment/PromotionDecision。

## 4. 本轮监督中修复的问题

1. 跨 tick 反复物化 evolution WorkItem：改为 Campaign 单例。
2. 03/04/05 有界事件完成后回退泛化 NextAction：禁止隐式续写。
3. systemd 环境没有 `python`：合同测试使用 `sys.executable`。
4. Controller 重启后 queued 消息已被 deduper 消费但没有 TaskInstance：60 秒后使用 recovery ID 重投，且不收费业务 retry。
5. 风险 Experiment 被单个 -1 样本劫持：按 `mean_reward * sqrt(samples)` 优先重复危害。
6. 05 只学习半轮：增加 01–04 终态依赖与停止前 final sync。
7. 预发送最终报告显示 `finalizing`、统计不含自身，容易被误读为没有收口：文案明确说明
   “本报告送达回执成功后自动 completed；当前统计不含本报告自身”。

## 5. 验证与下一门槛

- 完整 pytest：148 passed。
- 真实交付：所有五个主阶段、检查点和最终日报均确认。
- 资源约束：未观察到超过两个活动实例。
- 下一门槛：在当前代码上重跑 2 小时，核验多个检查点、最终报告、RL final sync、槽位恢复、
  无任务风暴、无泛化回退和成本预算；通过后再进入整夜 soak。

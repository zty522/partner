# Sprint 15：四 Harness 统一、Episode 学习与受控 Canary

**日期**：2026-08-26  
**状态**：五阶段已实现并完成首轮真实 canary；候选未晋升生产

## 五阶段实际完成情况

1. **真实 Episode 批量化**：04 历史任务可从 TaskInstance、JSONL、step result、Receipt、消息和
   trajectory 离线重建 Episode Trace v3；本轮共识别 19 个项目 Episode。
2. **自动归约**：手动任务终止治理后 best-effort 自动生成 Episode；归约失败不改变任务结果，失败任务
   也进入负样本，原始事件不被覆盖。
3. **Candidate Skill Registry**：候选有版本、适用/不适用边界、来源 Episode、反例、成功标准、回滚和
   append-only revision；只有带显式 PromotionDecision 的 `promoted` 才能 production effective。
4. **策略空间与 Shadow**：六个 decision key 已登记。`candidate_preflight_aware_planning_v1` 对 10 个
   历史基线做匹配反事实回放：基线 preflight failure 6，候选投影 1；基线 semantic repair model call 9，
   候选投影 2。以上是投影证据，不能单独晋升。
5. **双槽受控 Canary**：04 执行业务，05/离线脚本审计；调度仍为最多双槽。追加泛化验证后共有
   17 个真实 candidate Episode：4 次 completed/policy-eligible、13 次失败均保留。除 iteration 28 的
   首次合格样本外，iteration 29–31 又形成三轮逐源 3/3 的承接链，Receipt 为
   `receipt_c8d056aa01c6`、`receipt_f67dabb4d5a2`、`receipt_ee1489e68318`。

## 真实调试中解决的问题

- `.py` 来源路径被误判为要求生成 Python；旧 outgoing Receipt 不可读/不可承接。
- 同步 Hermes Adapter 被当成 async；`$step.result.output.content` 别名无法解析。
- Markdown 形式的 `source_path/evidence_quote` 未被真值审计识别。
- 手动产物只在 task 临时目录，Receipt 不能长期承接；现归档到 `share/evidence/.../manual/<task>`。
- 超长来源被要求重新编码为 JSON，造成截断；现由确定性命名源抽取代替不必要模型调用。
- semantic repair 错用 60 秒全局超时并产生重叠调用；现为 180 秒、无内层重试，外层仍有界。
- 读取步骤和 LLM 总结会复述旧文档中的“无 shell/file-write”错误；现读取消息只报文件与大小，
  `manual_stable` 总结只依据 runtime 状态，最终 Receipt 才宣告交付通过。
- 逐字证据会被中间摘要改写；现命名源 extract 强制每源一个对象，并把 `verified_sources` 直接保持到
  最终生成依赖。Markdown `output_spec` 不再走状态封装器，生成 fallback 会检查最小正文长度、引用对数
  和 `File-mutation verifier`，历史复盘中的旧错误也不会被误判为本轮能力声明。

## RL 解释边界

当前候选状态为 `canary`、`production_effective=false`、`promotion=false`。17 次执行包含调试和连续承接，
不是 17 组独立随机对照；13 次失败不能被 4 次成功覆盖。Shadow 文件显式写入
`intervention_isolated=false`：当前 arm marker 只做归因，尚未真正切换 planner 执行路径。下一门必须先
实现 baseline/candidate feature isolation，再收集独立、难度和来源匹配的执行并由用户显式决定晋升。

## 验收

- 全量回归：`327 passed in 14.45s`。
- Canary 执行时 04/05 受最多双槽约束；收口检查时两个 systemd 服务均为 inactive，scheduler 仍记录
  意图槽 `04/05`。没有打开 Campaign、cron 或自动下一轮，也不把槽记录误报为进程正在运行。
- 本地消息链包含收到、5 个步骤开始/完成、总结、文件附件和最终 Receipt。网络 QQ 与本地记录是不同
  transport 证据；本轮用 `local_canary` 验证框架语义，不冒充远端 QQ 到达。

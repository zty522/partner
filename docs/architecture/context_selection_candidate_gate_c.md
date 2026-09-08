# Gate C：认知上下文选择 Candidate 实验合同

**日期**：2026-08-28  
**状态**：机械 shadow 完成；Candidate v3=shadow，尚未执行业务匹配任务

## 1. 要回答的唯一问题

在同一手动任务、同一模型、同一工具权限、同一 planner/executor 和同一验收门下，用 Cognition Ledger
提供的结构化索引来选择上下文，是否比现有上下文加载策略更准确、更省预算？Gate C 不检验自动写代码、
自动续轮或策略晋升；这些变量不得混入本实验。

## 2. 两条真正隔离的执行路径

| 项目 | baseline | candidate |
|---|---|---|
| 上下文选择 | 当前文档分级/项目状态加载 | 相同来源池，经只读 cognition selector 排序和裁剪 |
| 模型、temperature、工具、提示主体 | 固定且相同 | 固定且相同 |
| 输入预算 | 固定 | 固定，不得靠更多 token 获胜 |
| Episode/Receipt/真值门 | 相同 | 相同 |
| 记忆和策略写入 | 禁止 | 禁止 |
| 生产生效 | 否 | 否 |

每次运行必须在 Episode intervention marker 中记录 `experiment_id`、`match_key`、`policy_arm`、
selector 版本、入选 context id/digest、字符/token 预算。没有这些字段的样本不进入比较。

## 3. Candidate 允许做什么

输入仅限 catalog 可定位的 L1/L2 文档、当前项目 L3 state/receipt、与任务相关的已验证 CognitionEvent。
排序特征可包括任务词匹配、project/episode 身份、证据新鲜度、父链完整度和历史相关性。当前 v1 只使用
已验证 project/instance/percept 信号产生有界 soft boost，并为最新 Receipt 预留 1200 字符。输出只是：

```json
{
  "selected_context_refs": ["..."],
  "excluded_context_refs": ["..."],
  "budget_used": 0,
  "selection_reasons": [{"ref": "...", "evidence": "..."}],
  "selector_version": "cognition-context-shadow-v1"
}
```

它不得生成事实、改写原文、把模型自述当证据、读取其他实验臂输出、注册 Candidate、修改配置或调用工具。

## 4. 匹配任务与防泄漏

- 先以实例 04 的文献/GitHub 证据综合任务做探索，因为其来源引用和产物可机械核验。
- 每个 `match_key` 使用难度、来源数、输出格式和验收规则相同但内容不同的任务；不能把同一任务第二次运行
  当作独立样本。
- baseline/candidate 顺序交替或随机，运行前冻结任务与来源；任一臂的产物、聊天和反思不得进入另一臂上下文。
- 探索门至少 7 个匹配对，只用于发现实现问题；即使全胜也不自动晋升。是否扩大到独立 holdout，必须由
  人工审阅数据质量后显式决定。

## 5. 评价与停止条件

truth 和 safety 任一失败，该样本候选判负且不可由其他分数补偿。其余比较 business progress、handoff、
observability、efficiency，以及上下文 token/字符数、延迟、引用覆盖率和错误检索率。发生身份串线、预算不等、
来源泄漏、执行路径未隔离或 Candidate 产生生产写入时，立即把实验标为 invalid，而不是修饰结果。

Gate C 成功只意味着“可以提交一个 shadow Candidate 草案供人工审阅”。从 shadow 到 canary、从 canary 到
production 仍分别需要新授权、独立样本、回滚方案和显式 PromotionDecision。

## 6. 2026-08-28 机械实验结果

- 首版 `experiment_780b5cc8351d`：hard requested IDs 挤掉 Receipt 3/7、query 相关文档 4/7；已明确
  rejected，Candidate revision 2=`rejected`。
- 修正版 `experiment_acfec34e9d7b`：soft boost + L3 reserve，7/7 身份、预算、mandatory、确定性、
  continuity、query relevance 门通过；3/7 真正采用 cognition-ranked 文档。
- Candidate revision 3=`shadow`，`production_effective=false`；结果文件为
  `share/mind/governance/experience_guided_policy/shadow_evaluations/experiment_acfec34e9d7b_context_gate_c_preflight.json`。
- 这些是同一任务规格上的 selector 机械双跑，不是 14 个独立业务执行，不能计算质量增益或 RL 奖励。

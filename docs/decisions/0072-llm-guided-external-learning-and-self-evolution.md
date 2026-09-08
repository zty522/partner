# ADR 0072：LLM 引导的外部主动学习、自进化判断与 PDF 交付

**状态**：Accepted / Production event enabled / Longitudinal effect not yet proven  
**日期**：2026-09-08

## 决定

Partner 固定采用以下术语边界：

- **主动学习**面向 Partner 外部的未知信息：决定现在最值得读哪个仓库、论文、数据或实验，目的是减少对现实/领域的认知不确定性。
- **自进化**面向 Partner 自身：根据自己的 Episode、失败机制和 Reward，提出并验证运行时策略、Event、协议或代码改动。
- **项目迭代**消费知识与能力完成业务目标；它既不等于主动学习，也不等于自进化。
- **RL**不是第四套执行器，也不是三者的总称。当前 RL 是保守的离线/contextual policy 层：从可验证 Reward 更新动作价值，帮助下一轮选 action。它不能搜索事实、生成真值或自行批准代码。
- **Candidate**是尚待证明的干预。外部知识只有需要改变 Partner 时才编译为 Candidate；自进化 Candidate 必须经隔离对照、回归、回滚和显式 Event 生命周期。

统一但不混同的链路为：

```text
外部主动学习：knowledge gap
  → LLM 形成可证伪问题
  → 代码真实检索/下载
  → LLM 按信息增益与反证价值选源
  → 真实读取源码/PDF
  → LLM 综合、提出想法和最小实验
  → Observation/Reward/下一外部动作
  →（需要改变 Partner 时）Candidate

内部自进化：Episode/Issue/Reward
  → LLM 诊断因果机制与风险
  → 白名单 Candidate
  → frozen baseline/candidate + pytest + rollback
  → 独立 LLM critic 寻找反例和 reward hacking
  → 机器硬门 PromotionDecision
  → policy/activated 或 rejected/inconclusive
```

LLM 负责语义分叉，确定性代码负责事实与安全。URL 是否返回、仓库是否克隆、PDF 是否下载、源码/PDF 实际读取范围、测试是否通过、Reward 如何计算和生产是否生效，全部由代码与账本裁决。模型文本不能覆盖这些真值。

## 生产实现

新增 `external_knowledge_scout` Event，作为 04 项目原生轮转动作之一，也可由明确的 GitHub/论文学习请求直接路由。每轮关键阶段使用三次 MiniMax-M3：

1. 根据已读索引形成知识缺口、GitHub 查询、论文查询与选择标准；
2. 在真实候选集中选择互补 repo/paper，说明预期信息增益和可能反证；
3. 只依据实际读取的 README/源码片段与论文证据生成札记、原创想法和可证伪实验。

外部产物长期存放在：

- `external/code/discovered/`：浅克隆仓库；
- `external/literature/discovered/`：论文元数据与可获得 PDF；
- `external/insights/discovery_index.jsonl`：append-only 成功索引；
- `external/insights/discovery_failures.jsonl`：检索失败、相关性拒绝和证据不完整；
- `external/insights/*_external_learning.{md,json}`：人类札记和机器审计。

Event 会读取 README 加最多三个机制相关源码文件；论文有 PDF 时抽取前八页文本，否则明确标成 `abstract_only`。记录 `repository_files_read`、`paper_pdf_pages_read`、证据路径和摘要哈希。`downloaded`、`abstract_only`、`pdf_excerpt`、`idea_proposed` 不得互相冒充。

自进化构造器现在在白名单选择前调用一次 LLM 诊断；出现真实新 Candidate 并通过机器回归后，再调用一次独立 critic。2026-09-08 实跑中，MiniMax 正确判断三个已有 recipe 全部饱和，返回 `no_new_candidate`；没有为了增加次数伪造代码，现有策略仍单独记 `existing_production_effective=true`。

API 日志新增 provider 返回的 `prompt_tokens/completion_tokens/total_tokens`。短选题和选源使用受限 `classify` token 预算，研究综合使用独立 `research_synthesis` 预算，不再误用 `focus_extract` 被强制抬到 16000 tokens。

## QQ 与文档合同

- QQ 用户侧附件默认只交付 PDF。JSON/JSONL/Markdown/CSV 等机器 sidecar 保留在 Task、Receipt、治理或 external 目录；没有 PDF 时用文本诚实汇报，不主动发送难读的机器文件。
- 用户明确要求 CSV、XLSX、JSON 或源文件时，显式格式要求优先。
- 每次改变上述语义、Event、Reward 或交付合同，必须同步更新 `current_status.md`、本 ADR、对应架构文档、Sprint 和测试基线。

## 本轮真实证据与纠错

- 第一次生产搜索取得 `affaan-m/ECC` 和 arXiv `2402.11651v2`（Learning From Failure），仓库真实克隆、论文 PDF 真实下载，三次 MiniMax 完成问题/选择/综合，QQ 仅发送 `04_external_knowledge_scout.pdf`。
- 后续轮取得 `bytedance/deer-flow` 与 MuMath-Code；后者虽命中 tool-use LLM 词面，但与自进化主轴较弱，保留为选择质量负例，不能以“下载成功”充当高价值学习。
- 更早一次把叶片分割论文选入 Agent 研究，已追加 `semantic_relevance_rejected`，未删除历史；由此新增领域相关性和可读证据门。
- 新 Event 首次被旧 real-action gate 误判为 `missing_external_action`，造成一次多余内部学习。验收信号现已加入 `external_knowledge_scout/github.clone/paper.download/source_acquisition`，并有回归测试。
- 本轮实现证明了“真实外部获取 + 多阶段 LLM 判断 + PDF 交付”可运行；尚未证明这些知识已经改善 Partner 生产策略，也未证明跨日期长期 RL 成熟。
- 最终热重载后生产验收 Task `7bab1ca7-84c8-48d2-bac6-97c80a5dca26`：`alibaba/open-code-review` +
  *Large Language Model Agent: A Survey*，读取仓库 4 个文件、论文 PDF 8 页；研究综合调用为
  `prompt=11,342 / completion=4,736 / total=16,078 tokens`。Task 与治理均 done，Receipt
  `receipt_f89b3862b4fd`，QQ 只交付一个 19,718 字节 PDF，之后由完成信号立即进入下一项目动作。
- 完整回归：`960 passed, 2 warnings in 129.32s`；定向外部学习、自进化 Candidate、real-action 与交付回归 `150 passed`。

## 不回退约束

1. Event-first；LLM 输出不能直接执行或晋升。
2. 主动学习的 Reward 是信息价值/新颖性/后续采用证据，自进化的 Reward 是 Partner 行为改善；两者不得串账。
3. 不以 LLM 调用次数、token 数、下载数或报告长度替代业务效果。
4. 同一来源由 discovery index 降权/去重；没有新证据时允许诚实停止本轮，不刷报告。
5. 任何新的自进化代码面仍须白名单、baseline-fail/candidate-pass、聚焦回归、原子回滚四门。
6. `production_effective=false` 的外部学习或 critic 结论不得写成生产晋升。

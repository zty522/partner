# Event Flow 生成、迭代与学习策略

## 决策

Partner 当前由 LLM 生成有限候选 Event Flow，由确定性 Flow Compiler 检查结构与契约，Jev 做快速类型化判断，潜空间世界模型预测候选后果，Commitment 冻结最终选择，Event Runtime 真实执行，Settlement 根据证据裁决。现阶段不训练端到端神经网络直接生成完整 Flow。

这一安排适用于项目推进、主动学习、自进化和 benchmark。Flow 是版本化的 Event 组合；LLM 只能引用 Event Catalog 中已注册的 Event，不能用自由文本步骤绕过 Runtime。

## 标准生成链

```text
目标、索引上下文、最新 Settlement
  -> LLM 生成 2–4 个结构化候选 Flow
  -> Flow Compiler 校验 Event、类型、依赖、预算、隔离和可达性
  -> Jev 对候选做有限类型判断和风险筛选
  -> 潜空间世界模型预测终态、成本和可能失败节点
  -> Commitment 冻结 Flow、预期、评价器、失败条件和预算
  -> Event Runtime 按图真实执行并捕获 Checkpoint
  -> 独立评价器与 Settlement 裁决
  -> complete / continue / local_patch / active_learning / self_evolution / waiting
```

LLM 输出必须是可解析的候选图，至少包含 `flow_id`、节点、Event 类型、依赖、输入绑定、预期产物、成功标准、反证条件、预算和回滚。候选生成、批评和选择分别记录，不能将一次模型回答同时当成提出、批准和裁决。

## Flow Compiler 的硬门

候选执行前必须检查：

- Event 类型和版本真实存在；
- 节点输入输出契约兼容，前置条件可满足；
- 图无非法环、不可达节点或悬空依赖；
- 真实动作后存在验证，终点存在 Settlement；
- 时间、模型调用、token、GPU、外部访问和 child Flow 不超预算；
- benchmark 的 subject 看不到预期效果、隐藏标签和父级评价；
- 关键边界具有协议要求的 Checkpoint；
- 失败、超时、缺失产物和拒绝预测均有明确终态路由。

Compiler 只证明图可执行，不证明科学结论正确或项目已经推进。

## Jev、世界模型与 Commitment

Jev 只回答有限选择：候选是否准备好、主要风险类型、是否为知识缺口或 Partner 机制缺陷、建议选择哪个候选。它不生成完整 Flow，也不覆盖硬门和 Settlement。

潜空间世界模型预测每个候选的终态分布、产物概率、预期成本和可能失败节点；样本不足或 OOD 时必须拒绝预测。其输出在完成校准前保持 shadow。

Commitment 冻结最终 Flow 的内容哈希、Event 版本、输入、允许差异、预期效果、评价器、失败条件、预算和停止条件。执行期间不得由 LLM 偷换指标、数据、fold、候选特征或验收阈值。

## 迭代规则

Settlement 先分类失败，再决定迭代。普通迭代只替换失败相关的最小子图，保留已经通过的节点、证据和协议：

| 证据终态 | 允许路由 |
|---|---|
| 科学假设被否证 | 修改假设或 candidate |
| 产物缺失、格式错误 | 修复执行或收集子图 |
| Event 契约不匹配 | 修改依赖或适配 Event |
| 预算耗尽 | 缩小规模或选择低成本候选 |
| 评价不完整 | 增加独立 evaluator Event |
| 可复现的 Partner 机制缺陷 | 启动受控 self-evolution child Flow |
| 可由外部证据解决的知识缺口 | 启动 active-learning child Flow |
| 达到 Commitment | 进入项目下一阶段或 complete |

局部重规划仍须重新经过 Compiler、Jev、世界模型和 Commitment。失败不得触发无界重写或无限重试。

## 自进化与主动学习

自进化要求稳定 reproducer、baseline、候选补丁、相同条件 candidate、回归测试和 PromotionDecision。科学阴性、数据不足和外部服务故障不能伪装成机制缺陷。

主动学习要求先冻结知识问题，再执行索引检索、外部来源读取、来源质量检查、claim crosscheck、可迁移模式提取和下游 matched experiment。阅读内容本身不直接写入生产习惯或代码。

两类 child Flow 完成后必须返回父项目的原失败位置或报告边界，并保留唯一 continuation owner。

## 神经网络接管路径

运行时从现在开始记录：冻结前状态、候选图、Compiler 结果、Jev 判断、世界模型预测、最终选择、Event 序列、Checkpoint、产物、成本、指标变化、Settlement 和人工评价。

只有当稳定任务族积累了足够多、可复算且带真实 Settlement 的轨迹后，才依次训练：

1. `FlowRanker`：排序 LLM 产生的有限候选；
2. `NextEventPolicy`：预测稳定局部子图的下一个 Event；
3. `OutcomeModel`：预测成功率、成本和失败位置；
4. 完整 Flow 生成器：最后评估，必须始终受 Compiler、Commitment 和 Settlement 约束。

模型训练标签区分真实产物、业务推进、guardrail、预期效果、返工、无效循环和人工接受，不能把“Flow 到达 completed”直接当作成功。新模型先以 shadow 方式与 LLM 方案做同输入对照；达到跨任务、跨 seed 和回归门后，才能逐步接管排序或局部选点。

## 当前实施边界

Core v1 已具备候选提出/批评/选择、潜空间预测、Jev shadow、Commitment、Event Runtime 和 Settlement。当前需要补充的是通用候选 Flow schema、Compiler 的完整静态检查，以及从真实轨迹构建训练集的投影；在这些证据形成以前，LLM 是 Flow 候选生成器，确定性组件拥有执行与裁决权。

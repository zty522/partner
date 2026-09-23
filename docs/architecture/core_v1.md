# Partner Core v1：会读、会赌、会执行、会裁决的单一主干

## 当前结论

Core v1 不是第二套 Runtime。它是现有 Event Runtime 中的统一决策脊柱：领域 Event 负责提出候选与真实执行，Core v1 负责将候选变成可追溯的状态、预测、类型化判断、冻结承诺和结算记录。生产代码只保留 `main`；历史 Flow 依靠版本号回放，不依靠长期 feature 分支或第二 worktree。

最新三条 Flow 为：

- `project_iteration@3.1.0`
- `active_learning@2.0.0`
- `self_evolution@2.0.0`

`project_iteration@2.5.0/2.6.0` 等已运行图继续可解析，不能就地改写。

## 唯一运行顺序

```text
真实上下文与记忆
  ↓
领域 LLM：提出有限候选、反驳候选、选择一项
  ↓
core.state_build：冻结前统一状态与数值特征
  ↓
core.latent_forecast：预测各候选后果；样本不足或 OOD 时拒绝预测
  ├──────────────┐
  ↓              ↓
core.jev_evaluate：有限类型判断（shadow）
  └──────┬───────┘
         ↓
core.commitment_freeze：冻结选择、替代项、预期、评价器、失败条件和预算
         ↓
领域 Event：真实执行
         ↓
领域独立评价 Event：项目证据核验 / 学习 matched verify / 自进化 matched compare
         ↓
core.settlement：supported / falsified / inconclusive / invalid / blocked
         ↓
core.route_next：确定性触发 project iteration / active learning / self-evolution / waiting / complete
```

Event 不调用 Event。Flow 图声明依赖，Event Worker 执行并持久化节点终态。主动学习或自进化作为 child Flow 时，父 Flow 在 `route` 后暂停，child 结束后恢复原父 Flow；child 不建立另一条永久控制链。

## 六类责任

### LLM

LLM 读取经索引选择的上下文，提出 2–3 个有限候选，写出预期观察、反证条件、成功标准、风险和回滚，并先攻击自己的候选。LLM 可以解释复杂问题，但不声明实验成功，也不单独触发自进化。

### 潜空间动力学

`partner/core_v1/latent.py` 是第一版可审计基线。它对结构化状态做 PCA 编码，以当前潜状态和确定性 action embedding 为输入，用 ridge 回归预测下一潜状态和收益。它具备三个硬边界：

1. 少于 8 个可信 transition 时 `abstained`；
2. 当前状态超过训练潜空间的 OOD 界时 `abstained`；
3. 预测始终 `authoritative=false`，不直接执行、不裁决实验。

Settlement 将新 transition 先原子写入 Linux-native SQLite 热索引，再写入该 decision 自己的 `transition.json` 审计投影；训练只查询最近最多 500 条索引记录，不扫描全部历史。

### Jev

Jev 使用 TypeSafe 官方 `POST /v1/systemone` 协议和 `jev-latest`。一次请求并行回答 Choice、Noul 和 Score 问题。Core v1 默认 `shadow`；密钥只从 `TYPESAFE_API_KEY` 读取，响应、日志和冻结记录不保存密钥。缺密钥、超时、HTTP 错误或类型错误都生成 `unavailable` 记录，真实执行继续。

Jev 只判断限定问题：建议路由、是否为知识缺口、是否为 Partner 机制缺陷、候选是否准备好。它不能证明科学结论、授予权限、应用代码或覆盖 Settlement。接口依据 [TypeSafe OpenAPI](https://api.typesafe.ai/openapi.json)；模型定位依据 [TypeSafe Jev 说明](https://typesafe.ai/blog/introducing-system-one-models-and-jev)。

### Commitment

`core.commitment_freeze` 在执行前写入 `state/core_v1/decisions/<decision_id>/commitment.json`。冻结字段包括：

- 选中的候选和所有被放弃候选；
- 预期观察和成功标准；
- 独立评价 Event；
- 失败条件；
- 单动作、模型调用和 child Flow 预算；
- 世界模型预测与 Jev typed answers；
- 选择规则和内容哈希。

同一 `decision_id` 重写不同内容会失败。对于需要严格 baseline/candidate 的科学实验，原有 `partner.commitment` BetRecord 内核仍负责更细的实验冻结与统计裁决；Core v1 负责跨领域统一编排，二者不是互相替代。

### Event Runtime 与 Settlement

Commitment 后只允许相应领域 Event 执行。项目以 `project.outcome_verify` 为独立评价器；主动学习以 `active_learning.matched_verify` 判断知识采用是否有同输入同预算证据；自进化以 `self_evolution.matched_compare` 和 promotion decision 判断隔离候选。Settlement 只消费这些真实终态，模型叙述不能升级结果。

## 三类自动触发

### 项目迭代

只有以下条件同时成立才产生 `continue_project`：当前动作已裁决；产生新证据；存在具体下一动作；预算未耗尽；原请求授权仍覆盖；不是重复已否证路线。手动稳定模式每轮仍只执行一个主动作；多轮由 Event Worker 在原请求预算内排队。

### 主动学习

只有已识别知识或外部来源缺口，且外部证据能够解决该缺口时触发。基础设施错误、Partner 机制错误、科学假设阴性均不触发主动学习。child Flow 必须完成来源登记、全文读取、claim crosscheck、采用候选与 matched verify，之后回到父 Flow。

### 自进化

只有 Partner 自身机制缺陷同时具备稳定复现和独立评价器时触发。科学结果未达预期、数据稀缺、外部服务不可用都不能伪装成自进化。自进化必须完成 Issue → diagnosis → candidate → critic → Core Commitment → isolation → baseline/candidate → matched compare → promotion decision → Settlement；结束后回到父 Flow 的原失败位置或报告边界。

## 配置与状态

工作区配置键为 `core_v1`。默认 Jev `shadow`，潜模型自然从无数据开始并拒绝预测。核心状态位于 `partner_workspace/state/core_v1/`：

- `decisions/`：不可变 Commitment 与 Settlement；
- `decisions/<decision_id>/transition.json`：按决策隔离的真实 transition 审计；
- Linux-native runtime `core_v1.db`：并发安全的 transition 权威索引，训练查询最多 500 条；
- `latent_model.json`：可重建的线性潜空间模型。

任何生产 Event 读取这些历史时先查 SQLite 热索引或明确的 decision 路径，不遍历全部历史 JSON。单决策 `transition.json` 只是审计投影，不是训练查询入口。

## 当前边界

Core v1 已实现代码、Flow 接线、历史版本兼容、Jev typed adapter、潜模型、不可变存储、触发政策和确定性 benchmark。Jev 尚未用真实 key 做校准，潜模型尚未积累真实项目 transition，因此两者保持 shadow。它们变成 advisory 或 gated 之前，必须分别完成校准误差、OOD 拒绝、路由收益和失败降级 benchmark。

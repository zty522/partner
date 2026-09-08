# BDK 世界模型研究主线

**状态**：Canonical；核心已迁入 Partner，压力与真实 Episode shadow 已完成，未进入 production  
**更新日期**：2026-08-30

## 1. 原意校正

BDK 的北极星不是“四个核中选一个”，也不是为了在 TargetDiff 上替换 sklearn。用户提出的是一个更一般的
认知问题：有限观察点只约束现实的一部分；学习者的知识决定能提出哪些函数/机制假设；计算机负责大规模
拟合、组合与反驳；新增观察既淘汰旧解释，也可能触发新结构；重复而可靠的经验应巩固记忆，被反证的经验
应降低检索权重但保留历史。

```text
现实/观察点
  → 表征与显著性
  → 联想检索（相似、时序、因果、反例、来源）
  → 多个函数/机制假设
  → 参数拟合与模拟
  → 后验/证据权重更新
  → 主动选择最能区分假设的新观察
  → 同化 / 顺应 / 巩固 / 可恢复遗忘
```

“函数”是认知结构的透明类比：真正的世界模型还要处理离散状态、对象关系、因果机制、语言、图像和动作。
因此当前 FunctionPool 只是一件旧的数值实验器，不代表 BDK 主线已经完成。

## 2. Transformer、概率、函数搜索与 RL 的分工

| 部件 | 应负责 | 不应独占 |
|---|---|---|
| Transformer | 从多模态上下文形成表示，联想检索，提出新的机制/程序/函数候选 | 事实账本、最终真值、生产权限 |
| 函数/程序假设库 | 提供可解释基础形式、组合算子、定义域和复杂度 | 假装覆盖现实中一切机制 |
| 概率统计 | 对噪声、不确定性、模型证据、校准和变点做显式更新 | 把单次 softmax 当严格后验 |
| 主动学习/BO | 选择最能区分当前假设的新观察或实验 | 通用 Agent 的全部策略学习 |
| RL/bandit | 在可重复状态、可比较动作、可信延迟奖励存在时优化行动策略 | 生成事实、修改安全硬门、替代自进化治理 |
| Event runtime | 保存发生了什么、证据来自哪里、谁批准了什么 | 直接决定哪种模型更真 |

拟议的改版 Transformer 应先作为 `HypothesisProvider`：输入当前观察、检索到的记忆和领域约束，输出带
来源与可证伪预测的候选结构。候选仍由独立拟合器、统计评分器和真实实验评价。这样可利用神经网络的联想
与组合能力，同时避免让模型自述同时充当提议、裁判和历史。

## 3. 当前最小实现与实验事实

正式代码已由孵化区迁入 `partner/cognition/world_model/`：

- `hypotheses.py`：constant/polynomial/Fourier/decay/hinge/reciprocal/modulated 等可检查假设；新增点数或
  基础残差惊奇可触发工作集外候选。
- `engine.py`：归一化、联想检索、Huber IRLS + robust BIC/MDL v2 证据、后验加权、模型分歧主动选点；
  少于 8 点明确返回 `insufficient_evidence`。下一观察现使用 Gaussian information gain × coverage，
  不再把 MaxVar 当成通用主动学习定义。
- `memory.py`：相似检索、时间衰减、重复确认、反证、持久化；降低优先级而不删除旧痕迹。
- `events.py`：观察、检索、假设、信念、主动询问、巩固六事件的 append-only SHA-256 链。

可复现实验：

```bash
cd /mnt/e/work/partner
python scripts/run_world_model_shadow_benchmark.py --output-dir artifacts/world_model_shadow_20260830
pytest -q tests/test_world_model_migration.py
```

2026-08-30 实际结果：5 点时 top=`cubic`、有效假设数约 `1.649`；17 点时 top=`fourier_f1`、后验约
`0.869`；重复相同证据后记忆继续巩固。该结果只证明“追加证据能够修正函数信念、重复经验能够巩固
检索先验”的最小机制，不证明通用世界模型、因果学习或 Transformer 已实现。

## 4. 融入 Partner 的顺序

1. **Shadow observation**：把 Partner Episode/Receipt 投影成观察 Event，只读运行世界模型，不改变 prompt。
2. **Hypothesis Candidate**：允许它提出下一观察或上下文候选，但必须带 `evaluation_contract(kind=event)`。
3. **匹配实验**：同一输入、模型、工具、预算下比较 baseline 与 candidate 的 truth、task utility、cost、
   calibration；不得只看文案或训练损失。
4. **有限 canary**：重复、多项目有效后才让策略影响一个可回退决策点。
5. **Transformer provider**：积累了真实 Event/Episode 数据后训练检索/假设 provider；原始事实、统计评价、
   safety 和 promotion 继续留在模型外。

第 1–3 步已经完成两级实验：ADR 0035 的干净三领域演示，以及 ADR 0036 的 120-case 压力门和 110 条
真实 Episode 只读映射。第二级实验中 Candidate 保持 proposal recall=1.0，将工作集 28→20.95；但真实
Episode 只有 04 的奖励有足够变化，03/05 全零，因此 Policy 仍为 `inconclusive`，没有注入生产实例。

真实 Episode 奖励轨迹只用于描述“历史 reward 随轮次怎样变化”。它不把 Agent 任务简化成函数，不从
曲线推出任务真值或因果关系。`hard_gate_passed` 只覆盖 truth+safety；RL 必须使用 `policy_eligible`/
`outcome_gate_passed`，并核验任务状态与业务进展。

## 5. 自进化的准确含义

自进化不是 RL 的同义词。它是：真实问题定位 → 可证伪假设 → 最小 Candidate → 独立执行 → 匹配评价 →
接受/拒绝/不确定 → 记忆与策略版本更新 → 回到项目验证。RL 只可能优化其中“重复行动选择”一环。

长期自动循环也不能为了不停而制造反思：项目 NextAction 优先；只有积累足够新业务证据才运行一次学习；
只有同 Campaign、可执行且带评价 Event 合同的 Candidate 才进入一次 bounded experiment；没有新证据时进入
可恢复 WAIT，并继续监听真实输入。

对象层与 Agent 元层的统一 acquisition、真实 Episode taxonomy 顺应和当前 repair/RL 边界见
`docs/architecture/two_level_active_learning.md` 与 ADR 0037。

# ADR 0049：多源证据 Candidate 的下游 Agent 匹配实验

**状态**：Accepted as shadow evidence；Policy 仍为 `inconclusive`  
**日期**：2026-08-31  
**生产边界**：`manual_stable`、`control_policy.json` 与生产流量不变；不启动 Campaign，不 promotion

## 要回答的问题

ADR 0048 只证明 Candidate 能把 Receipt、同项目轨迹和研究证据完整放入有限上下文，不能证明下游 Agent
会正确使用这些证据。本轮把评价推进一层：冻结三个不同机制的真实任务，在相同消费者、设置和预算下比较
baseline 与 Candidate 的结构化答案，并用独立评价器检查真值、来源、安全与预算。

三个任务分别要求恢复最新 Receipt/下一步、解释 JitRL 的运行时反馈机制，以及恢复 Hermes handoff 的机制与
状态。评价不以文字相似度替代事实：关键事实错误、来源伪造或越权建议会将该任务 reward 硬置零。

## 实现

- `partner/governance/research_downstream.py`：冻结任务、JSON 解析、独立真值/来源/安全评价、外发脱敏和
  本地 `typed-evidence-consumer-v1`。
- `scripts/run_research_adoption_downstream_experiment.py`：创建新 Issue/Experiment，配对并交错执行两臂，
  核对项目状态与 Receipt SHA 不变，并拒绝把 benchmark reward 写成业务进步。
- Candidate 仍只能经 Event-first 白名单执行；实验输出均为 shadow，`production_effective=false`。
- 外部模型模式可选，但任何真实或派生项目上下文外发都必须得到用户明确授权。脱敏函数会移除本地路径、
  Receipt/trajectory/experiment/task 标识、哈希、URL、长数字和凭据赋值；脱敏本身不构成外发授权。

## 失败、定位与修复

1. `experiment_d5ddf561c154`：MiniMax 在受限网络中 12 次调用均 DNS 失败，两臂均为 0；保留为失败证据。
   请求提升网络权限时，安全门因上下文包含真实项目数据而拒绝；随后即使启用脱敏，也因没有用户明确授权
   而继续拒绝。没有通过其他通道绕过。
2. `experiment_b2c19879b603`：本地消费者 Candidate 仅 1/3。根因是研究证据放在末尾后被最终盲截断，
   但引用清单仍声称已选中。修复为完整对象预算、Receipt 保护区、先放研究证据，并只登记真实写入的引用。
3. `experiment_13fc5eca87f1`：Candidate 2/3。JitRL 中文查询与英文论文证据字面不匹配，Top-2 还可能来自
   同一来源。修复为可审计的中英机制别名加分与来源去重，而不是扩大 oracle 或修改评价标准。
4. `experiment_a420574f08ad`：最终本地配对实验 3/3 通过。Baseline reward `[0,0,0]`，Candidate
   `[1,1,1]`，均值 `0 → 1`；三对均无回退，truth/safety、同设置、状态不可变和生产隔离硬门全部通过。

最终结果：
`instances/05/state/tasks/research_downstream_20260831_234943/research_adoption_downstream_experiment.json`。

## 结论边界

本轮证明的是：由多源证据编译的上下文可以被一个与评价器分离的、确定性本地 Agent 消费，并在三种真实
机制任务中恢复正确事实；同时实验确实找出了两个上下文编译缺陷并完成回归。这比“证据召回率变高”更进一步，
但仍不能声称：

- 外部或通用 LLM 的任务质量已经改善；
- 已产生用户业务成品或纵向业务 Reward；
- RL 已让五项目持续变好；
- Candidate 可以进入 production 或 Partner 可以无界长跑。

因此 Policy 保持 `inconclusive`。下一硬门是：获得明确数据外发授权后做同样的盲化 LLM 配对实验，或安装
本地模型完成该实验；随后还必须在真实项目产物和多轮 Reward 上验证，才能讨论限域 promotion。

## 验证

- 新增/扩展：`tests/test_research_downstream.py`、`tests/test_research_adoption_candidate.py`
- 完整回归：`649 passed in 118.33s`
- 所有历史失败实验 append-only 保留；没有改写原 Episode、Receipt 或结果文件。

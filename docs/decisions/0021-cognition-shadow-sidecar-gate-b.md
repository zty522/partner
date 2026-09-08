# ADR 0021：Episode 后置认知 Shadow Sidecar

**日期**：2026-08-28  
**状态**：Accepted；能力默认关闭

## 背景

BDK 沙箱已经证明 CognitionEvent 哈希账本可回放，但直接把实验包导入 Partner 热路径会耦合两套根基，
也可能让“观察运行”意外变成“修改运行”。Gate B 需要获得真实运行样本，同时保持手动稳定路径不变。

## 决策

1. 只在 Partner Episode reducer 成功后调用 Partner-native `cognition_mirror.py`，不导入 `partner_test`。
2. 只映射权威 Episode `state.json`，生成 observation 与 percept；不生成假设、行动、Candidate 或因果结论。
3. `runtime.cognition_shadow_mirror` 默认 `false`，必须显式开启；关闭时不创建任何镜像目录。
4. wrapper 捕获全部镜像异常并返回诊断。镜像结果不得改变 task status、Receipt、delivery 或消息协议。
5. bundle 继续通过统一 adapter 核验 source project/task/instance、digest 与三个严格 false 权限位。
6. 同一 source digest 幂等；同路径不同内容视为冲突，禁止覆盖。不同 Episode state 修订使用不同 digest 目录。

## 后果

- Partner 可以逐步积累真实认知旁路数据，又不把研究运行时植入核心循环。
- Gate B 的成功只证明采集与回放链成立，不证明策略更好，也不授权 Gate C 或自动自进化。
- 下一步实验受 `context_selection_candidate_gate_c.md` 约束，必须只改变上下文选择一个变量。

## 验证

- 5 个 mirror 专门测试覆盖默认关闭、两事件链、幂等、fail-open 和权威路径。
- 实例 04 completed Episode `episode_29582c86e705d2df` 显式镜像成功；哈希链复核通过。
- 完整回归：361 passed；生产开关保持 false，Candidate 未注册。

# 外部认知账本的 Partner Shadow 集成边界

**日期**：2026-08-28  
**状态**：L2 current；Gate B 已实现，生产默认关闭

## 1. 定位

`partner_test/bdk_transformer` 正在研究细粒度的感知、联想、假设、行动和信念更新事件。Partner 不导入该实验包，也不把它接入 `manual_stable` 热路径。双方只通过 `docs/contracts/cognition_shadow_bundle.schema.json` 交换一个 shadow-only JSON bundle。

## 2. Partner 持有的权威

- Partner Episode Trace v3 与原始 task/channel/tool 日志仍是业务事实来源。
- BDK CognitionEvent 是派生研究视图，不能替代 Receipt、trajectory 或 truth audit。
- Partner 适配器 `partner/governance/cognition_adapter.py` 验证 bundle digest、来源 Episode 文件真实存在、
  至少一个 Episode 与 project/task/instance 精确匹配、身份一致性和三个否定权限位。
- 错误 import 不删除或覆盖；`correct_cognition_shadow_import()` 只向 `corrections.jsonl` 追加
  `invalidate/reinstate` 决定。
- `import_cognition_shadow()` 仅归档到 `share/mind/governance/cognition_shadow/`，幂等且不注册候选。
- `candidate_skill_draft_from_cognition()` 只返回兼容 `register_candidate_skill()` 的 `status=shadow` 草案；调用注册函数仍是另一个显式治理动作。
- `partner/governance/cognition_mirror.py` 是 Partner 自有旁路实现，不依赖或导入 `partner_test`。它在
  已完成的 Episode reducer 之后，把权威 `state.json` 确定性映射成 observation/percept 两个事件，写入
  哈希链后再走同一个 shadow import 校验器。
- 热路径仅在 `runtime.cognition_shadow_mirror=true` 时调用；默认值为 `false`。失败返回
  `mirror_best_effort_failed` 并写任务诊断，不改变任务 done/failed、Receipt 或用户交付。

## 3. 不变量

```text
candidate_registration_allowed = false
production_mutation_allowed     = false
manual_stable_override          = false
source_episode_ids              = non-empty Partner episode_* ids
```

任一权限位不是严格的 JSON `false`、bundle 被修改、event count 不一致或 digest 格式错误，都拒绝导入。

## 4. 融合阶段

1. **历史 shadow（完成）**：只读映射 Episode，不影响运行。
2. **旁路镜像 Gate B（完成、默认关闭）**：Episode reducer 后 best-effort 生成 bundle；失败 fail-open 且有诊断。
3. **上下文 Candidate Gate C（合同完成、尚未执行）**：baseline/candidate feature-isolated，先做匹配任务；见
   `context_selection_candidate_gate_c.md`。
4. **Canary**：用户显式授权、truth/safety 硬门、完整回归。
5. **Capability seam**：只有 canary 重复有效才定义稳定 provider；不把 BDK 代码写进 Partner 核心循环。

当前完成第 1、2 阶段及 Gate C 实验合同，不得据此启动自动自进化。

## 5. 验证

- BDK：8 个 ledger/replay/篡改/恢复/bridge 测试通过。
- Partner：adapter 与 Gate B mirror 共 10 个专门测试；完整回归 361 passed。
- 实数回放：20 个现有 Partner Episode → 40 个 CognitionEvent，20/20 deterministic replay。
- 初始 import `cognition_shadow_49cae789b89154ac` 因 project_id 规范化错误已追加 invalidate；
  权威 import 为 `cognition_shadow_809c3bcdd0b0412b`，来源 `episode_0168bb64ac8cccad`；
  已核验 Candidate 草案未写入 registry。
- `manual_stable`、Campaign 开关、QQ/浏览器路径均未改动。
- Gate B 真实显式验证：实例 04 已完成 Episode `episode_29582c86e705d2df` 被镜像为
  `cog_episode_29582c86e705d2df_f60650145f5c6ec1`，import 为
  `cognition_shadow_4c8be7fad06c69e6`。独立复核 bundle、ledger SHA、head hash 和链连接均通过；
  运行时开关仍为 false，Candidate 文件仍不存在。
- 独立调用 BDK bridge 对同一 source 重建后，ledger bytes 与 Partner-native 输出相同，replay state 和
  replay digest 相同（`9b30e97c784c0a68fb579eda4a83045ade36ea59cb2dbacf6ecb0379e45a36a0`）。

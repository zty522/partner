# ADR 0045：Claim-level 验真、Reward 校正与失败主动学习闭环

- 日期：2026-08-30
- 状态：candidate validated；运行时安全修复生效，不等于通用语义模型或生产策略晋升
- 实例：04 历史失败回放；实现可被其他手动实例复用
- 前置决策：ADR 0043、0044

## 事实核对

Task `d2cb657c-f79d-4146-93d2-cdec40dc084e` 的四个读文件 Event 全部成功，文本合成和
`harness_comparison.md` 也已生成。真实失败点是第 8 步 PDF Event：Planner 只传了裸相对路径
`harness_comparison.md`，未将它绑定为第 7 步的 typed output，最终报
`no content (provide content or source_path)`。这是 `output_reference` 内部合同错误，不是
用户输入路径错误。

反复出现的“后台执行超过单步时间限制”长文未进入当时任务日志、QQ 历史、delivery queue
或 Event pipeline，因此不伪造函数级唯一定论。已排除持久任务、Campaign 和 Hermes cron；最高可信
归因是修改前 04 长驻进程中的旧超时兜底播报。ADR 0044 已停旧进程并将消息统一经持久审计/
去重入口；本 ADR 进一步从执行层阻止同参数内部错误重试。

## 决策与实现

1. **Claim-level 语义验真**
   - 04 已晋升的文件证据报告必须输出显式 Claim Ledger：`claim_id/claim_text/claim_axes/
     source_path/source_identity/evidence_quote/support_type/rationale`。
   - 硬门重新打开命名来源，检查路径归属、逐字成员关系、标题/banner/frontmatter、五个比较轴和
     保守的 claim–quote 语义相关性。`inference` 只计 0.5；未标注、跨源或不相关 direct 结论硬失败。
2. **Output reference 与重试语义**
   - PDF 生成优先解析 `$step.result.path` / `$step.files[0]`，然后只在 Task working directory 内解析
     相对路径和已持久化的 step result。
   - `output_reference/planner_contract` 确定性失败首次即 `retryable=false`；其他错误若“同参数+
     同错误签名”也短路，不再将固定错误播报三次。
3. **Episode / Reward 修正**
   - failed outcome 不得获得 `accepted_completed` 和完整 `artifact_contract`；已写但未完成交付的文件单独记
     `partial_artifact=0.05`。failed Episode 的 truth 强制为 0，不能进入晋升样本。
   - 历史轨迹不改写；对 `traj_manual_4d3f78f10ae387af` 追加 revision 2，Reward=`-0.4`，action 改为
     `planning.output_reference_contract/typed_reference_unresolved`。Episode 只更新可重建投影，原 trace 不变。
4. **失败自动学习闭环**
   - 手动任务失败后自动执行 `observe → select → diagnose → repair proposal`，输出机制级 Candidate；
     热路不修改源码、不修改 control policy、不自动晋升。
   - 历史 04 失败已产生 `repair_proposal_d45f590ce525fd6c`，干预为
     `typed_output_reference_resolution_v1`，selection 和 diagnosis 均已落盘。

## 匹配实验

Event-first 正式 Event 实验 `experiment_manual_learning_event_20260831_v1`（前置函数级预验
`experiment_manual_learning_20260830_v1`）使用冻结输入同时验证两类故障：

- Baseline 如实暴露“逐字存在即接受”和“不解析 typed output”两个缺口；
- Candidate 接受真实支持的 claim，拒绝语义混淆 claim，并解析真实 typed Markdown 路径；
- 4/4 硬门通过，决策为 `candidate_validated`，反馈写入 ActiveLearningMemory；
  `production_effective=false`，不用一次小实验冒充通用晋升。
- 重放同一 experiment ID 现返回 `idempotent_experiment_replay`，不会重复更新 ActiveLearningMemory。
- 机制级诊断器已正确消费含 `/` 的 failure class；历史 04 Episode 诊断为 1 个可读 Episode、
  evidence coverage=1.0、top signature 为 typed-reference unresolved。Evolution ledger 267 events 验链通过。

## 验证和边界

- 针对套件：88 passed；PDF/candidate/reward 扩展套件：42 passed；全仓：
  `615 passed in 235.99s`。
- 这里的语义门是保守、可解释、确定性的第一层，不是通用 NLI/世界模型。复杂中文改写、否定、
  数字范围和多跳因果仍需独立的语义 evaluator Candidate。
- 匹配实验证明本次两个机制修复，不证明“RL 已让所有业务持续变好”、模型权重已训练或可无界自治运行。
- production 仍是 `manual_stable`；本轮未启动 Campaign。

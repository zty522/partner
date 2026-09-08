# Sprint 20：经验驱动策略学习与项目假设引擎

**状态**：Core Implemented / Namespace Migration Complete / Longitudinal Observation Running  
**日期**：2026-09-08

## 目标

让五个项目不是机械轮换固定 Event，而是在真实历史停滞时由 LLM 提出一个能力边界内、可执行、可证伪的新 Event Candidate；隔离检查后进入项目动作池，用真实终态决定保留或拒绝。同时停止把当前 UCB 策略选择笼统称为 RL。

## 已完成

- [x] 术语迁移为 EGPL；代码、测试、脚本和工作区状态只读写 `experience_guided_policy/`，旧目录与旧字段已一次性迁移并移除。
- [x] 每个生产项目动作都调用一次 MiniMax 六段式审议；停滞 Candidate 再调用 proposal + 独立 critic 两次，调用结果和 token usage 可审计。
- [x] 五项目 Candidate grammar：每项仅能选注册 Event、既有策略、有界 variant 和明确业务测量。
- [x] `project_hypothesis_propose` 注册为 Event；自动选择与手动调用共用一个原子处理器。
- [x] 连续三个非正 Reward 才允许 LLM 提案；无停滞不制造候选，拒绝后至少推进三步才可再提。
- [x] 权威可证伪命题由 Event 测量契约生成；LLM 创意保留为 advisory，不能扩大工具能力。
- [x] Candidate 作为独立 arm 进入 UCB；真实 trajectory 负责 canary 评价，非正 Reward 自动拒绝，三次正样本也只能进入 matched 实验而不能自我晋升。
- [x] 01 evidence variant、02 experiment seed、03 MD variant、04 query/source variant、05 code-surface variant 均已真正传入对应 Event，而非只改 JSON 标签。
- [x] 六段哲学推理契约接入提案与动作 critic，并把业务推进、外部主动学习、Partner 自进化分账。
- [x] 真实发现并拒绝两条“文本聪明、Event 测不到”的越界候选；这类失败成为契约测试样本。
- [x] 完整回归更新为 976 passed，0 failed；旧目录、旧字段和当前模块旧命名扫描为零。

## 后续硬门

- [ ] 每个项目至少完成一条语义合法 Candidate 的 baseline/candidate 同输入匹配实验。
- [ ] Candidate 晋升必须比较声明指标的方向与效应量，不能只看合成总 Reward。
- [ ] 04 将外部证据形成 adoption Candidate，并在后续真实项目 Event 中验证使用，而非止于札记。
- [ ] 05 对至少三种真实 failure mechanism 形成不同的可回滚代码 Candidate；生成者与验收者分离。
- [ ] 连续三个真实日期证明 EGPL 的项目 Reward、重复率或失败恢复率优于冻结轮转基线。
- [ ] 将上下文目录选择、项目经历巩固/遗忘和反例召回接入同一认知契约，但不得一次性加载全部文档。

## 完成定义

短期完成：候选可由 Event 产生、参数确实影响执行、测量不越界、失败自动拒绝、五项目各有真实轨迹。长期完成：跨日期 matched 证据显示业务持续改善；外部学习确实改变后续行动；Partner 自进化至少覆盖三种机制且全部可回滚。未满足长期门前不得称“长期 RL 成熟”或“通用自进化完成”。

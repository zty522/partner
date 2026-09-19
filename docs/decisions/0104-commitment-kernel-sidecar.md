# ADR 0104：保留现有 Partner，在同仓库新增干净的 commitment 内核

- 状态：已接受（本轮实现完成，生产接入未授权）
- 日期：2026-09-19
- 关联：`docs/architecture/commitment_loop.md`、`docs/testing/commitment_kernel_acceptance_20260919.md`

## 背景

现有 Partner 的自主主链（`instance_native` + terminal bridge + readmission + 共享 worker）
是长期演化的产物，承载了 Application Job、Event Flow、索引、记忆、交付等大量真实能力，
同时也有已知的累积缺陷（例如 2026-09-18 观测到的 native 队列失控：生产速率约为消费速率的 10 倍）。

用户要求：**保留现有 Partner 作为真实环境和对照基线**，另做一个干净、独立、可测试的
commitment 内核来表达"会赌的自闭环"，而不是继续在旧主链上打补丁，也不是重写整个 Partner。

## 决策

1. **保留现有 Partner 不动。** 不启动、不迁移、不领取或改写历史积压 Job。它是本轮的真实环境。
2. **在同仓库新增 `partner/commitment/` 独立包。** 不建永久分叉的产品（无 `partner_v2`），
   不复制 Application / Event / QQ / Web / 索引 / 领域执行器。
3. **用 git worktree 隔离实现。** `/mnt/e/work/partner_commitment`，分支 `feature/commitment-kernel`，
   基线 `ba786c7`。这样"干净内核"与"真实环境"在同一个代码库、但差异可审查。
4. **薄适配而非重写。** `partner/application/commitment_adapter.py` 只做点读快照、
   allow-list 分子执行、确定性测量；业务逻辑不复制。
5. **只读预留端口。** `ExternalLearningRequester` / `ConsequenceForecaster` 本轮只有
   Null/Shadow 实现，世界模型与主动学习大循环不在范围内。
6. **暂不接入生产。** `partner/events/commitment.py` 只声明与映射，不接管任何生产续跑所有权。

## 理由

- 直接改旧主链会在无法区分"内核改进"与"主链历史缺陷修复"的情况下失去对照；
- 重写整个 Partner 会丢掉索引、记忆、交付、领域执行器等已被验证的能力；
- 同仓库 + worktree + 薄适配，使得新增差异可审查、可回退，同时不制造第二套产品。

## 后果

- 内核的行为测试全部离线可跑（79 项），不依赖生产进程；
- 三臂 benchmark 目前只是**执行骨架**（合成 fixture），不能据此宣称优劣；
- 一个真实分子纵向样本已完成并结算，但它只是**单样本**，不支持研究假设；
- 生产接入需要另行评审（见下）。

## 何时允许接入生产（三个前置条件）

1. 生产 Job 存储以**只读投影**方式接入，内核不成为第二个作业权威；
2. 一次 canary bet 在真实项目快照上通过，留下完整可审计产物（receipt / measurement / settlement / experience）；
3. 与 `instance_native` 的续跑所有权做显式交接：同一实例同一时刻只有一个续跑主人，
   并且旧 main-chain 的队列**必须先约束到有界**（当前积压未清理，不允许在失控队列旁再挂一个环）。

三项齐备后另开一轮评审；本 ADR 不构成接入授权。

# ADR 0058：真实日期窗口与 append-only 成熟度投影

**日期**：2026-09-02  
**状态**：Accepted / Longitudinal validation running  
**生产影响**：无；`manual_stable` 与 `control_policy.json` 不变

## 背景

Sprint 18 的专用控制器已经可以常驻，但 2026-09-01 的有限课程结束后只会等待已有证据，真实日期变化尚未
自动形成下一轮 matched WorkItem。同时，trajectory ledger 采用 append-only correction，长期成熟度评估器却
把同一 `trajectory_id` 的旧行和修订行都当作独立样本，导致样本数和历史 false-success 被重复计算。

## 决策

1. 专用控制器每次 reconcile 前调用 `ensure_real_date_window`。它只接受本机真实日期，每日幂等，最多三个日期，
   只创建普通 baseline/candidate WorkItem，不直接写 Reward、Receipt、策略或 PromotionDecision。
2. 每个项目内部严格 baseline→Candidate，同机最多双槽；当前仍仅 04/05，MiniMax-M3 only。
3. matched 实验不得写 ProjectState Receipt 或触发业务 continuation。历史污染 Receipt 采用 append-only
   invalidation；误创建且未执行的 continuation 明确取消，不改写成成功。
4. matched trajectory 可进入学习观察，但不因此获得 production-policy eligibility。
5. production readiness 对 append-only trajectory 先按 `trajectory_id` 投影最新行，再计算业务、样本量、
   Reward、false-success 和 Wilson 门；账本顺序为权威，不把 correction 当新独立样本。

## 2026-09-02 真实结果

- systemd 服务在前夜 `/mnt/e` 短暂 I/O 故障和 WSL 重启后自动恢复，当前继续常驻；空闲不占实例槽、不调用 LLM。
- 新日期生成 04/05 两项目、每项目两个 topic，共 4 个严格 matched pair、8 个 WorkItem。
- baseline `0/4`、平均 Reward `-0.4`；Candidate `4/4`、平均 Reward `+0.8`。Candidate 的 Claim Ledger 均为
  两个真实来源、`2 passed / 0 failed`。
- Campaign 当前 42 项：30 completed、11 blocked、1 cancelled、0 active；模型调用累计 17。取消项是修复前
  outer Campaign 错误提出、但尚未执行的 continuation。
- 最新 Observation 投影为 689 total、547 learning eligible、94 promotion eligible、439 positive、
  238 negative、12 neutral。
- 去重修订后的连续实验累计 baseline 26、Candidate 29；均值 `-0.4 / 0.5931`，Candidate Wilson 95% 下界
  `0.6545`，仍有 5 个历史 false-success，只有 2 个真实日期。因此 readiness 继续 blocked。
- 独立 sustained-business 门也仍 blocked：只有 3 个带真实交付的合格轮次、1 个项目、1 个日期；本次本地
  matched 学习产物没有渠道 ACK，故没有被错误抬成业务交付成功。
- 全仓回归：`726 passed, 2 warnings in 108.22s`。

## 边界

今天的 4/4 证明修复后的 Candidate 在第二个日期窗口真实生效，不等于长期 RL 已成熟。第三真实日期、
Wilson ≥0.67、Candidate false-success=0、anchor 抗遗忘和 rollback/readiness 仍是硬门。控制器可以一直在线，
但信息耗尽时等待真实新日期或新证据，不重复旧题制造样本。

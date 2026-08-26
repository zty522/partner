# Sprint 14：手动受控 Canary 与最终成品真值门晋升

> **2026-08-26 追加纠正**：本 Sprint 的 production smoke Receipt `receipt_680db01279ab` 后被
> Episode v3 审计作废：来源引文虽逐字匹配，成品却继承“无 shell/file-write”陈述并与当前
> `create_file` 事实矛盾。本 Sprint 仍证明 canary/PromotionDecision 接线，但不再证明该样本成功。

**日期**：2026-08-26  
**状态**：完成  
**生产模式**：`manual_stable`

## 目标

把“真实手动轨迹可以建立候选”推进为一次可归因、可回滚、可审计的 baseline/candidate 实验。
本 Sprint 不恢复 Campaign、自动续轮或无人监督自进化。

## 实验

- Experiment：`experiment_5af99917bea9`
- Decision key：`literature_github_learning:manual_final_artifact_truth`
- Baseline：`manual_stable_grounded_v1`
- Candidate：`manual_stable_truth_audit_v2`
- 唯一干预：candidate 在最终 Markdown/TXT 阶段重新打开每个实际输入，验证连续
  `source_path/evidence_quote` 对；路径必须属于输入，quote 至少 20 字且逐字存在于对应源。

## 六个真实样本

| Arm | Task | Receipt/状态 | Reward | 真值结果 |
|---|---|---|---:|---|
| candidate | `8457fd0a-aab7-4ed7-86e4-7dacd62277c3` | `receipt_b6005d8ae618` | 1.0 | 3/3 源通过 |
| baseline | `d5b5e4dc-494b-4c3c-a996-b572381411b6` | `receipt_e6b8c38b8b22` | 1.0 | 基线验收通过 |
| candidate | `9a7e1bcd-5bce-4945-ac62-f3a0c68dde68` | `receipt_d38a62c90436` | 1.0 | 3/3 源通过 |
| baseline | `c00e462d-d92b-4774-b31f-d8b7f51d7666` | failed，无 Receipt | -0.45 | 路径写坏，`citations<3` |
| candidate | `663993be-b86b-4d09-81d7-2b778946941e` | `receipt_53fd2d7a6e29` | 1.0 | 3/3 源通过 |
| baseline | `3ab6ae6b-0b4f-4e2f-bf98-400c49782e67` | `receipt_a71495411b73` | 1.0 | 基线验收通过 |

Baseline：samples=3、success_rate=0.6667、mean_reward=0.5167、false_success=1。  
Candidate：samples=3、success_rate=1.0、mean_reward=1.0、false_success=0。  
Reward gain=0.4833。

## 决策与交付

实验决策时的全量回归证明：`pytest -q`，`302 passed in 9.24s`；生产验证后的当前基线为
`307 passed in 9.23s`。05 通过用户显式事件形成
PromotionDecision=`promoted`。最终成功任务 `9bc52720-423b-4254-9691-3e811d78a9e6`，Receipt
`receipt_03db4def9a27`；交付 `manual_canary_decision.md` 与 `manual_canary_decision.json`，没有下一动作。

## 晋升后生产验证

普通 04 任务（无 candidate/baseline marker）最终由任务
`45cbe78a-bc36-46a3-9961-02b645baf7d3` 验证。生产策略自动在三个真实来源读取与报告生成之间插入
确定性命名源抽取，避免模型改写 Markdown/HTML 原文；最终报告再由治理层回读全部来源。

- Artifact：`production_truth_policy_verified.md`
- Receipt：`receipt_680db01279ab`
- Truth audit：3/3 verified，0 missing，0 invalid
- Trajectory：policy_arm=production，reward=1.0，handoff_consumed=true，false_success=false
- Stop：`next_actions=[]`
- 回归：`307 passed in 9.23s`

此前失败试跑及负奖励轨迹均保留，分别推动步骤引用、结构化 data、抽取→合成、同任务写后回读和
输入谱系修复；没有删除失败记录来制造生产成功率。

此前两次表面完成分别因“JSON 未进入 Receipt”和“泛化 next action”被追加作废；最初一次 Planner
附加伪动态路径和重复 writer，任务直接失败。历史不覆盖，均保留作回归证据。

## 生产边界

v2 只对 04 中“已有真实输入文件并生成 Markdown/TXT 成品”的任务生效。聊天、代码产物、其他实例、
自动续轮和 Campaign 均不受本次晋升授权。任何新策略必须重新建立 Experiment、独立样本、回归证明与
PromotionDecision。

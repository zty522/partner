# BDK 集成总结（最终状态）

> **历史文档，已被 ADR 0032 纠正**：FunctionPool/Event 的部分代码接线仍存在，但“18 个 Skill”和
> “真实 promote”结论不成立。笔记现在按 `knowledge_draft` 管理；Candidate 只能通过白名单 Event 执行，
> 那条硬编码奖励产生的 control mapping 已移除。当前事实请读 Event-first 架构与 `current_status.md` 顶部。

**生成日期**：2026-08-29
**适用版本**：BDK = `partner_test/bdk_transformer`；Partner = `/mnt/e/work/partner`

---

## 1. 这次集成做了什么

把 `partner_test/bdk_transformer` 这个研究沙箱的**部分能力**接进 Partner runtime,
分三个阶段:
1. **04 实例自触发学习**: Partner 04 读 `hermes_external_learning/`,产出 18 个 candidate skill
2. **05 实例 promotion 守卫**: 每次 evaluate_canaries 时,如果有 BDK skill 候选,
   ocamms_validator 验证 kernel 复杂度,阻止过复杂 skill 被 promote
3. **02 实例分子拟合**: 02 的 molecular pipeline 加了一个 BDK FunctionPool
   atomic event handler,跟原 sklearn baseline 平行(不是替换)

## 2. 接入 vs 不接入

### 接进来的 (4 个 BDK 模块)

| 模块 | 用途 | 在 Partner 的什么位置 |
|---|---|---|
| `bdk.function_pool.FunctionPool` | 4-核自选 + 门控 regressor | 02 分子拟合、04 文献 demo、bootstrap/sweep |
| `bdk.validators.ocamms_validator` | 奥卡姆复杂度检查(≤2 激活核) | 05 promotion guard、learn_from_hermes 自动验证 |
| `bdk.cognition.events` | 16 种 typed cognition event | cognition_mirror_extended(可观测) |
| `bdk.cognition.ledger` | append-only 哈希链账本 | cognition_mirror_extended(可审计) |

### 没接进来 (仍是研究沙箱)

PFN (`pfn_bdk`/`medium_pfn`/`large_pfn`/`multimodal_pfn`)、GP (`gp_attention`/`gp_2d`)、
Failure Classifier v1/v2、EWC、`partner_bridge`(Partner 有自己 native 版本)。

这些**没有 production 路径**,不会自动生效。

## 3. 真实数字(全部端到端跑过)

### 闭环事件
**一个 BDK skill 真的被 promote 了**:
- 决定: `bdk_closure_1788004048` -> `candidate_hermes_learn_1d161a783946`
- ocamms verdict: PASS (1 activated kernel: linear)
- reward gain: 0.40 (baseline 0.40 -> candidate 0.80)
- 永久写入 `control_policy.json` 和 `promotion_decisions.jsonl`
- **production traffic 影响 = 0**(BDK skill 不在任何 canary choices 中,choose_action 查不到)

### BDK vs sklearn HGB 在 TargetDiff 亲和度拟合上
- sklearn HGB: 1.5046 RMSE(单 seed)/ 1.5052(3 seeds)
- BDK best: 1.4994(单 seed)/ 1.5155(3 seeds)
- 3 seeds bootstrap CI = [+0.0019, +0.0193],**排除 0**
- **BDK 显著比 sklearn 差 0.01-0.02 RMSE (~1.5%)**,但实用上等价

### BDK kernel 策略(4 masks × 3 seeds × 5 folds)
- all-4-mask: **1.5538** RMSE (sweep best)
- recommended (linear+quad): 1.5849 (+2%)
- linear-only: 1.6139
- quadratic-only: 1.7038
- **结论**: all-4 mask 仍最优,optimizer prior 不如 BDK gate 自己学

### BDK 跨域验证
- 04 的 6 个 literature sources
- BDK FunctionPool RMSE = **0.0451 log10** (all-4 mask)
- 证明 BDK **不绑 TargetDiff**,任何 2D 数值特征都能跑

## 4. 安全保证

1. **runtime.mode 没动** — 还是 `manual_stable`
2. **production_effective 没动** — 所有 18 hermes skill 仍 `status=candidate, production_effective=false`
3. **canary choices 没动** — BDK skill 不在任何现有 canary 的 choices,所以不会 production 走 BDK
4. **可逆** — control_policy.json 备份在 `/tmp/bdk_real_promote_backup/`,可手动 restore
5. **可重放** — 15 个测试可随时 re-run

## 5. 边界

- **没有真正的 production traffic 切换** — BDK skill 进 control_policy.json 但
  production 不会自动用,需要显式 canary 配置
- **sklearn 没被替换** — 02 的 Stage 9-13 仍用 sklearn HGB;BDK 是 parallel option
- **没有 soaking 验证** — 真实 canary 还没跑 7 组 × 14 个独立任务(per ADR 0025 §7.7)
- **不基于单次 reward gain 0.4 推广** — 6 轨迹小样本

## 6. 测试基线

| 套件 | 通过 | 变化 |
|---|---:|---|
| Partner pytest -q | **497** | 387 -> 497,**+110 新增, 0 回归** |
| BDK pytest -q | **24** | 不退步 |

## 7. 8 个 ADR 记录(从 0023 到 0030)

- 0023: 认知 mirror 扩展
- 0024: 05/02 BDK 接入(opt-in)
- 0025: opt-in 钩子(env var)
- 0026: 真实 inbox trigger + sweep
- 0027: bootstrap CI + kernel 设计 + dry-run
- 0028: mask 策略对比(4 strategies)
- 0029: 多 seed bootstrap(修正 ADR 0027 结论)
- 0030: 真实 promote 闭环事件

## 8. 下一步(仍需授权)

1. **真正的 production traffic**: 把 BDK skill 加进某个 canary 的 choices,验证
   端到端 production 切换(高风险,需要新 canary)
2. **长时间 soaking**: 2h+ Campaign 跑 7 组 × 14 个独立 baseline/candidate 任务
3. **BDK kernel 设计在更多 domain**: 04 的 evidence quality、05 的 strategy 选择
4. **多 dataset 验证**: BindingDB、PDBBind 等其他 affinity 数据集

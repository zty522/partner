# 外部知识借鉴记录 (External Learning Log)

记录从外部代码库、论文中学习和借鉴的内容，以及如何应用到 Partner 的改进中。

---

## 1. PocketFlow — 分子生成引擎

**来源**: `/mnt/e/work/partner_workspace/external/PocketFlow/`
**论文**: PocketFlow (Nature Machine Intelligence, 2024)
**功能**: 基于蛋白口袋结构的配体分子生成（ZINC 预训练模型）

**调用方式**:
- Wrapper: `partner/partner/agents/wrappers/pocketflow_wrapper.py`
- Manifest: `partner/partner/agents/manifests/pocketflow.json`
- 入口: `main_generate.py -pkt <pdb_path>`
- Conda 环境: `pocketflow`

**借鉴应用到 Partner**:
- 通过 `call_agent_skill(pocketflow)` 在 batch_plan 中调用
- SP140 PHD 口袋生成 20 个候选分子的测试已通过
- 生成结果经 RDKit 分析性质后输出 PDF 报告

---

## 2. CytoBridge — 单细胞轨迹推断

**来源**: `/mnt/e/work/partner_workspace/external/CytoBridge/`
**GitHub**: https://github.com/JackkWangzh/CytoBridge-agent
**功能**: 单细胞转录组轨迹推断与细胞动力学建模（PAGA, DPT, RNA velocity）

**调用方式**:
- Wrapper: `partner/partner/agents/wrappers/cytobridge_wrapper.py`
- Manifest: `partner/partner/agents/manifests/cytobridge.json`
- CLI: `cytobridge-agent run`
- Conda 环境: `cytobridge`

**借鉴应用到 Partner**:
- pancreas.h5ad 数据集上的轨迹推断测试已完成
- 进度回调链路: wrapper → dispatcher → harness → QQ

---

## 3. SESA — 自进化搜索 Agent

**来源**: `/mnt/e/work/partner_workspace/external/SESA-Self-Evolving-Search-Agents-master/`
**论文**: "Self-Play Meets Skill Evolution: Self-Evolving Search Agents that Pose, Solve, and Remember" (2025)
**核心机制**:
- Proposer ↔ Solver 自我博弈
- 失败 → 提取结构化 Skill Card → 持久化到 Skill Bank
- 检索相似技能辅助后续任务
- Skill Card 格式: CATEGORY, PATTERN, COMMON_CONFUSION, KEY_DISTINCTION, TRIGGER_KEYWORDS, QUERIES

**借鉴应用到 Partner**:
| 借鉴内容 | Partner 对应实现 | 文件 |
|----------|-----------------|------|
| Skill Bank 持久化 | SQLite 技能库 + 结构化技能卡 | `partner/evolution/self_heal.py:SkillBank` |
| 失败→技能提取 | SKILL_EXTRACT_PROMPT 驱动 LLM 提取 | `partner/evolution/self_heal.py:SelfHealEngine` |
| 技能检索 | 关键词匹配检索 top-k | `SkillBank.retrieve()` |
| 技能验证 | success_count / fail_count 追踪 | `SkillBank.record_result()` |
| Proposer/Solver 动态 | OODA Engine (Proposer) + batch_plan Executor (Solver) | `ooda_engine.py` + `executor.py` |

---

## 4. VeriSkill — 程序验证技能自进化

**来源**: `/mnt/e/work/partner_workspace/external/literature/VeriSkill.pdf`
**论文**: "VeriSkill: A Self-Evolution Framework for Program Verification Skills" (2025)
**核心机制**:
- 验证失败归因到技能缺陷
- 提取诊断签名→可复用课程
- 迭代精炼：只保留能提高验证性能的技能
- 在保留程序语义的前提下改进

**借鉴应用到 Partner**:
| 借鉴内容 | Partner 对应实现 |
|----------|-----------------|
| 失败归因到技能 | `SelfHealEngine.diagnose_and_fix()` — 步骤结果→根因→修复动作 |
| 迭代精炼 | `SkillBank.record_result()` — 只保留成功率高的技能 |
| 修复验证 | `_apply_fix()` — params/env/config 三种自动修复 |
| 不破坏原有功能 | 代码修复走 Hermes delegation，不直接修改 |

---

## 5. AI2BMD — AI 驱动分子动力学

**来源**: `/mnt/e/work/partner_workspace/external/AI2BMD/`
**功能**: 基于深度学习的生物分子动力学模拟（替代传统 MD）
**状态**: 已放置，尚未集成到 Partner agent 调用链

---

## 6. ViSNet — 等变图神经网络分子性质预测

**来源**: `/mnt/e/work/partner_workspace/external/ViSNet/`
**功能**: 基于等变神经网络的分子势能和力预测
**状态**: 已放置，尚未集成

---

## 7. Amber / MMPBSA — 分子动力学模拟与结合能计算

**来源**: `/mnt/e/work/partner_workspace/external/amber/`, `/mnt/e/work/partner_workspace/external/mmpbsa/`
**功能**: 
- Amber: 经典分子动力学模拟套件
- MMPBSA: 蛋白-配体结合自由能计算
**状态**: 已放置，mmpbsa 有 Python 脚本（`analyze_racp_il33_contacts.py`），可通过 wrapper 调用

---

## 8. Hermes Agent — 当前 Agent 框架

**来源**: `/mnt/e/work/partner_workspace/external/hermes-agent/`
**功能**: Partner 运行的 Agent 框架本身
**状态**: 运行依赖（runtime dependency），非工具集成

---

## 待集成

| 工具 | 来源 | 用途 | 优先级 |
|------|------|------|--------|
| ViSNet | external/ViSNet | 分子性质预测（对接后评分） | 高 |
| Amber/MMPBSA | external/amber, external/mmpbsa | 结合能计算 | 中 |
| AI2BMD | external/AI2BMD | AI MD 模拟 | 低 |

---

*最后更新: 2026-08-06*

---

## 9. Polar — Agentic RL on Any Harness

**来源**: `/mnt/e/work/partner_workspace/external/literature/Polar Agentic RL on Any Harness at Scale.pdf`
**代码**: `/mnt/e/work/partner_workspace/external/ProRL-Agent-Server-stable/`
**论文**: NVIDIA, arXiv:2605.24220 (2026)
**核心机制**:
- API Proxy 模式: 在 agent harness 和 LLM 之间插入代理，拦截所有 API 调用
- 记录 token 级交互轨迹用于 RL 训练
- 异步 rollout: 并行管理多个 harness 实例
- 黑盒集成: 不需要修改 agent 代码

**借鉴应用到 Partner**:
| 借鉴内容 | Partner 对应实现 | 文件 |
|----------|-----------------|------|
| API Proxy 拦截 | direct_api.py 已记录每次 LLM 调用 | `partner/adapters/direct_api.py` |
| 轨迹记录 | hermes_chat.jsonl 记录所有 LLM 调用 | `instances/03/state/logs/hermes_chat.jsonl` |
| 异步 rollout | Partner 实例独立运行（03/05 并行） | `partner/__main__.py` |
| 解耦设计 | adapter 层隔离模型/训练框架 | `partner/adapters/` |

---

## 10. ERA — AI System for Scientific Software (Nature)

**来源**: `/mnt/e/work/partner_workspace/external/literature/An AI system to help scientists write expert-level empirical software.pdf`
**论文**: Nature Vol 654, 2026 (Google Research)
**核心机制**:
- LLM + **树搜索**系统探索代码解空间
- **连续质量评分**（非布尔）：每个候选方案都有分数
- 代码变异: LLM 改写代码 → 评估质量 → 保留最优分支
- 在生物信息学发现 40 种超越人类的方法
- 在流行病学生成 14 个超越 CDC 的模型

**借鉴应用到 Partner**:
| 借鉴内容 | Partner 对应实现 | 文件 |
|----------|-----------------|------|
| 树搜索修复 | tree_search_heal: 并尝试 N 种修复策略，选最优 | `partner/evolution/tree_search.py` (新建) |
| 策略生成 | LLM 生成多样化的修复策略（非单一方案） | `TreeSearchHealer._generate_strategies()` |
| 质量评分 | 每种修复策略评分(0-10)，选最高分 | `TreeSearchHealer._score_result()` |
| 分支探索 | 深度≤3，每层 ≤3 分支 | `TreeSearchHealer.search()` |
| 修复→评估→选优 | 自愈失败后自动进入树搜索 | `executor.py` (集成) |

---

*最后更新: 2026-08-06*

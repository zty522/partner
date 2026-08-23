# Sprint 6: 自进化与分子生成探索

**时间**: 2026-08-01 ~ 2026-08-06
**目标**: 完善自愈机制 + SP140 分子生成技术探索

---

## 1. OODA v4 引擎重写

**文件**: `partner/partner/core/ooda_engine.py`

### 改进内容
- **CircuitBreaker**: 连续失败 5 次自动熔断，防止死循环
- **LLM 驱动计划生成**: `strategy=llm_driven`，每轮由 LLM 生成研究计划而非硬编码
- **固定目录命名**: `round_NNN_YYYYMMDD_HHMMSS` 格式，便于追踪
- **任务前缀路由**: 注入消息加【任务指令】前缀，避免被路由分类器误判为 direct_reply
- **迭代上下文**: `_build_context()` 传递前 3 轮成果、产物文件名、执行记录
- **基于上一轮继续**: prompt 要求每轮基于上一轮成果推进

### 验证
- SP140 分子生成: 20 个候选分子 + SMILES + QED/LogP/MW 完整性质表
- web_search 搜索成功: DuckDuckGo 搜索 DiffDock/TargetDiff 等前沿方法
- PDF 报告生成: 106KB + 130KB

---

## 2. 自愈引擎 v2 (Skill Bank)

**文件**: `partner/partner/evolution/self_heal.py` (365 行)

### 借鉴 SESA + VeriSkill 论文

| 组件 | 功能 |
|------|------|
| `SkillBank` | SQLite 持久化技能库，存储修复技能 |
| `SelfHealEngine` | 失败诊断 → 提取技能 → 尝试修复 |
| `auto_heal()` | 一站式调用入口 |
| `retrieve_skills()` | 任务前检索相关修复技能 |

### 技能卡格式
```
CATEGORY: agent_call|param_fix|env_setup|file_path|code_bug|config|dependency
PATTERN: 失败模式描述
ROOT_CAUSE: 根因分析
FIX_ACTION: 具体修复步骤
FIX_TYPE: params|env|config|code|cannot_fix
TRIGGER_KEYWORDS: 触发关键词
RETRY_PARAMS: 调整后的参数 JSON
```

### 集成点
- `executor.py`: `core_step_failed` 时先尝试自愈再 break
- 诊断在 REFLECT_PATCH 之后、CURIOSITY_PATCH 应用之前
- 修复结果记录到 `state/self_heal_log.jsonl`

---

## 3. Prompt 精简

**文件**: `partner/partner/planner/prompt_builder.py`

- growth/evolution 上下文块缩减
- 新增规划规则: 禁止在临时任务目录用 read_file，有文件路径直接传 agent
- batch_plan prompt 从 ~60KB 降至 ~25KB

---

## 4. 模型适配

**文件**: `partner/partner/adapters/direct_api.py`

- 支持 `DEEPSEEK_MODEL` 环境变量切换模型
- 默认 `deepseek-chat`（5s batch_plan）
- v4-flash 回退到 v4-pro 的自动 fallback 逻辑
- 超时动态调整: `external_calls.yaml` batch_planner 120s, classify 45s

### 模型对比
| 模型 | batch_plan 耗时 | 输出 | 结论 |
|------|----------------|------|------|
| deepseek-chat | 5s | 1993 chars ✅ | 主力 |
| deepseek-v4-flash | 38s | 0 chars ❌ | 无法处理大 prompt |
| deepseek-v4-pro | 97-165s | 3819 chars | 可做 fallback |

---

## 5. 文件清理与结构整理

### 清理
- partner 根目录: 删除 6 个 .noqa 僵尸文件、构建产物
- workspace: 从 22 目录精简到 8 目录
- shared_projects: 从 248 目录精简到 7 项目（含 _archive）
- instances/tasks: 清除 104 个旧任务目录
- archive 324 个 stub agent manifests（无 endpoint）
- 删除 instance 06（僵尸实例）
- 统一 5 个实例的 QQ 配置

### 最终结构
```
partner/
├── partner/                    ← 核心代码
│   ├── mind/                   ← 事件循环 (executor, harness)
│   ├── core/                   ← OODA 引擎, 断路器
│   ├── planner/                ← 计划生成 (prompt_builder)
│   ├── adapters/               ← LLM 适配 (direct_api)
│   ├── evolution/              ← 自进化, 自愈 (self_heal)
│   ├── agents/                 ← Agent 框架 (dispatcher, wrappers, manifests)
│   └── v2/                     ← v2 模块 (perception, control, outer_loop)
└── docs/                       ← 文档

partner_workspace/
├── config/                     ← 统一配置 (QQ, agents, external_calls)
├── instances/                  ← 01-05 实例
├── external/                   ← 外部工具 (PocketFlow, CytoBridge, SESA, etc.)
└── shared_projects/            ← 研究项目 (SP140, molgen_exploration, etc.)
```

---

## 6. 当前能力

| 能力 | 状态 | 备注 |
|------|------|------|
| web_search (DuckDuckGo) | ✅ | 搜索前沿方法 |
| PocketFlow 分子生成 | ✅ | SP140 口袋生成 20 个分子 |
| RDKit 性质分析 | ⚠️ | bioinformatics agent 偶尔返回空值 |
| PDF 报告生成 | ✅ | weasyprint 转换 |
| QQ Bot 推送 | ✅ | 5 个实例配置完成 |
| OODA 自主循环 | ✅ | v4: LLM 计划 + 断路器 + 上下文 |
| 自愈引擎 | ✅ | v2: Skill Bank + SESA 风格提取 |
| 迭代上下文 | ✅ | 每轮基于前序成果继续 |

---

## 7. 下一步

- SP140 分子对接可行性评估（Amber/MMPBSA 集成）
- ViSNet 分子性质预测集成
- 自愈引擎: 技能检索精度提升（embedding 替代关键词匹配）
- 多实例并行: 01-05 五个实例协同分工

---

*最后更新: 2026-08-06*

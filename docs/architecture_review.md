# Partner 架构审视

> 闭环首次关闭后的架构差距分析。基于 2026-08-07 实际运行数据。

> **⚠️ 状态更新 (2026-08-13)**：本文件是 08-07 的历史分析，部分差距项已解决或过时。现状：
> - G2 实例间知识隔离 → ✅ 已通过 `share/knowledge/` 累积知识库部分解决（research_loop.archive_outputs）
> - G3 Executor 脆弱 → ✅ self_heal 已模块化（self_heal_hook.py）
> - G4 消息系统脱节 → ✅ 已修（QQ 推真实文件 + 研究摘要）
> - G6 OODA 断裂 → ⛔ 过时（OODA 已删除，被 research_loop.py 替代）
> - G1 修复无验证、G5 配置简单、G7 无仪表板 → 仍未解决
>
> **⚠️ 状态更新 (2026-08-21)**：Sprint 9 开启（自我认知与自主学习）。
> - 新增能力清单 `capabilities.md`（会/不会/需学）→ 部分缓解 G5（实例缺领域上下文）
> - 新增强制写总设计 `design.md`（先设计后执行）→ 朝 G1（修复无验证）方向，但"执行后验证"仍未闭环
> - 浏览器自动化（browser_open 等）→ ✅ 已修复（chromium 在 Partner 进程 fork 链里 SIGTRAP，改用 systemd-run 启动独立 worker 解决；详见 change_log 2026-08-21）
> - 研究循环知识承接 → ✅ 已修复（OUTPUT_REQUIRED_TYPES 补 "01"，01 实例恢复归档）
> - G7 无仪表板 → 仍未解决
>
> **⚠️ 状态更新 (2026-08-23)**：自进化真实性与可观测性收敛。
> - G1 修复无验证 → 🟡 部分解决：关键协议已要求 DOM/文件/发送回执并完成 01/02 实机验收；通用 post-fix 故障注入仍不完整。
> - G4 消息系统脱节 → ✅ 已解决：文字/文件只在真实渠道回调确认后算成功；01 还会发送每个关键步骤的截图和 qwen 读图说明。
> - G6 迭代链路断裂 → ✅ 对已定义协议已解决：02 可从第二轮自动续跑第三、第四轮，并在无新证据时明确停止。
> - G7 无仪表板 → ❌ 仍未解决；`current_status.md` 只是人工更新的状态基线，不是实时仪表板。

---

## 一、当前架构

```
QQ Bridge → Event Loop → Routing → batch_plan → Harness → Steps
                                                        ↓ fail
                                              self_heal → skill_bank
                                                       
proactive_evolver (bg thread)
  → scan (10min): read self_awareness → LLM → findings → auto_fixer
  → heal_log: self_heal 运行时修复
```

> 上图为 2026-08-07 历史快照，其中 `proactive_evolver`/OODA 不是当前主路径。
> 当前主路径是 `QQ Bridge → executor → Harness/v2 事件 → 证据验收 → research_loop 续跑/停止`。

---

## 二、架构差距

### G1: 修复无验证
- 现状：auto_fixer 应用 patch → 语法检查通过 → 记录 success
- 差距：没有验证修复是否真的解决了问题
- 需要：修复后重启实例 → 触发对应功能 → 检查是否通过

### G2: 实例间知识隔离
- 现状：01 修了 config.yaml，02-05 不知道
- 差距：5 个实例独立运行，经验不共享
- 需要：共享 skill_bank 数据库，或 central heal_log

### G3: Executor 脆弱
- 现状：每次 git checkout 抹掉自愈集成，需手动重加
- 差距：核心文件修改太频繁且无保护
- 需要：executor 的自愈钩子应独立成模块，不修改原文件

### G4: 消息系统脱节
- 现状：QQ 消息 = 模板 "已停止"；实际产出在深层目录
- 差距：研究进展对用户不可见
- 需要：消息应该展示：做了什么 + 发现了什么 + 文件在哪

**2026-08-23 结论**：已解决。`push_events.py` 使用运行时 callback 验收真实发送，
`browser.py:_visual_step()` 进一步把操作截图和视觉描述发给用户。

### G5: 实例配置过于简单
- 现状：config.yaml 只有 name + goal + tools
- 差距：缺少领域知识、预期产出、验收标准
- 需要：增加 knowledge_files, expected_artifacts, success_criteria

### G6: OODA 与 batch_plan 断裂
- 现状：OODA 生成研究计划，但 batch_plan 完成后不通知 OODA
- 差距：OODA 无法推进 phase，cycle() 返回 None
- 需要：batch_plan DONE 时回调 OODA 标记 phase_completed

**2026-08-23 结论**：OODA 问题已过时；它已被删除。对当前已定义的 02 分子研究协议，
`research_loop.py` 已能在完成回调后自动 enqueue 下一轮。通用、非协议化研究仍受轮数和多样性门控约束。

### G7: 无自我监控仪表板
- 现状：你需要我来检查 5 个实例的状态
- 差距：没有集中视图展示所有实例健康状态
- 需要：状态汇总文件或简单的 health dashboard

---

## 三、优先级排序

| 优先级 | 差距 | 理由 |
|--------|------|------|
| P0 | G1 修复无验证 | 闭环不完整，修复可能无效 |
| P0 | G3 Executor 脆弱 | 每次修改都冒破坏风险 |
| P1 | G4 消息系统脱节 | 用户看不到进展 |
| P1 | G6 OODA 断裂 | 实例完成后无法自动续任务 |
| P2 | G2 实例间隔离 | 学习效率低 5 倍 |
| P2 | G5 配置太简单 | 实例不知道领域上下文 |
| P3 | G7 无仪表板 | 运维不便 |

### 2026-08-23 重排后的未完成项

| 优先级 | 差距 | 当前判断 |
|--------|------|----------|
| P0 | 通用 post-fix 验证 | 特定协议已有闭环，需扩展到通用修复 |
| P0 | 通用浏览器视觉步骤策略 | 目前只对小红书事务强制 |
| P1 | 新研究证据接入 | 02 缺少目标活性/对接数据 |
| P1 | 实时健康与交付仪表板 | G7 仍未解决 |

---

## 四、建议的架构演进方向

### 短期（本周）
1. G3: 将 self_heal hook 抽成独立模块 → executor 不再需要手动 patch
2. G1: auto_fixer 增加 post-fix 验证步骤
3. G4: 修改 DONE 消息格式（已在 prompt_builder 中做）

### 中期
4. G6: OODA-batch_plan 回调链路
5. G2: 共享 skill_bank（SQLite 文件放在 workspace 根目录）

### 长期
6. G5: 实例配置模板（知识文件路径、验收标准、迭代策略）
7. G7: 状态仪表板

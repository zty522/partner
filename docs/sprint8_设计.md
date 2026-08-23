# Partner 项目全景回顾与 Sprint 8 设计

## Sprint 1-6 基础评估

| Sprint | 主题 | 交付 | 状态 |
|--------|------|------|:--:|
| S1 | 基础架构 | 工作区统一布局、config/instances 分层 | ✅ |
| S2 | 核心架构 | 包重组 51→25、harness | ✅ |
| S3 | v2 扩展 | 56 Harness Event、v2 模块 | ✅ |
| S4 | 集成稳定 | AETHER GPU、CytoBridge | ✅ |
| S5 | harness 增强 | execute_code、batch_plan | ⚠️ |
| S6 | 自进化 | OODA v4、self_heal、5实例QQ | ⚠️ |

基础评分: 70%

---

## Sprint 8 设计: 从"能跑"到"有用"

### P0: 深度研究闭环 ✅
- 03: 读论文→提取方法→获取代码→运行→对比→改进
- **实际**: 03 已产出含实际代码运行结果的分析报告

### P0: 桌面操作稳定化 ✅
- 01: 截图 + push_files 稳定运行

### P1: Research Loop（替代 OODA） ✅ 新增
- 替代已删除的 OODA 引擎
- 新建 `partner/mind/research_loop.py`
- 5 实例自主循环：task 完成 → 分析产出 → 生成下一步 → 直接 enqueue
- 质量门控：最大 5 轮 + 多样性检查 + 产出验证
- 不经过 desktop_inbox，不与消息流冲突

### P1: 累积知识库 ✅ 已接入
- 每轮产出归档到 `shared_knowledge/{id}/latest/` + `history.jsonl`
- 下一轮注入上一轮摘要，实现 v1→v2→v3 演进
- `research_loop.py` 的 `archive_outputs` / `load_latest_knowledge`

### P1: 用户可见性 ✅
- QQ 消息含研究发现摘要

---

## Sprint 8 实际实现

### harness 架构增强 (harness.py)
1. **ProjectProber** — 自动探测项目结构（依赖类型、源码目录、已安装包）
2. **PlanExecutor 返回 step_failures** — 供重规划使用
3. **产出验证** — 执行后检查 expected_artifacts

### planner 可靠性增强 (batch_planner.py, prompt_builder.py)
4. **_ensure_write_artifact** — LLM 忘记 write 步骤时自动追加
5. **JSON 兜底计划** — LLM 非法 JSON 时用硬编码计划
6. **probe_results 注入** — 规划 prompt 含项目结构
7. **包可用性检测** — 引导 planner 优先直接运行

### executor 增强 (executor.py)
8. **路径智能提取** — 从用户消息提取项目路径
9. **_event_completion_receipt_local 加 sanitize**
10. **Research Loop 集成** — _handle_stop_project 完成时回调 + _handle_user_message 时重置

### Research Loop (partner/mind/research_loop.py) ★ 新增
11. **on_task_done()** — task 完成时判断是否继续
12. **质量门控** — 最大 5 轮、多样性检查（同类型连 3 次停）、产出验证
13. **实例差异化** — 每个实例有自己的研究方向和循环策略
14. **直接 enqueue** — 不经过 desktop_inbox，避免消息流冲突

### QQ Bot 修复
15. **app_id 字符串修复** — QQ API 要求字符串格式
16. **INVALID_SESSION 重连**

### 已删除
17. **OODA 引擎** — 被 Research Loop 替代
18. **polling loop** — 与 push_callback 竞态

---

## 当前验证结果 (2026-08-13)

| 实例 | 产出 | QQ推送 | 自主循环 |
|------|------|:--:|:--:|
| 01 | winscr_*.png | ✅ | — (截图不适配循环) |
| 02 | catalog.md | ✅ | ✅ |
| 03 | analysis.md | ✅ | ✅ |
| 04 | report.md | ✅ | ✅ |
| 05 | report.md | ✅ | — (工具任务不适配循环) |

02/03/04 自主循环已验证：连续多轮自动迭代 + shared_knowledge 归档 + 上一轮摘要注入。

---

## 待做

| 任务 | 优先级 | 状态 |
|------|--------|:--:|
| 03 git clone + 实际运行外部代码 | P1 | ✅ 已验证（numpy 兼容性修复 + execute_code content 修复 + 真实解析运行） |
| 周报 cron job | P2 | ✅ 已创建（每周一 9:00，job_id 34d33d1c98d8） |
| TargetDiff 完整 benchmark 复现 | P2 | ⛔ 受限于无 GPU/预训练权重/torch_scatter，不可行 |
| architecture_review.md 差距分析更新 | P2 | ✅ 已更新（2026-08-21，见 architecture_review.md 状态更新） |

---

*创建: 2026-08-07*
*更新: 2026-08-12 — Research Loop 新增*
*更新: 2026-08-21 — Sprint 8 收尾，核心功能全部完成并验证；TargetDiff 因环境限制不可行已如实记录。开启 Sprint 9。*

# Sprint 7 基础设施修复清单

> Sprint 7 规划完成度 ~20%。70% 时间花在修基础设施 bug。
> 不先修基础，Sprint 7 新功能无法稳定运行。

---

## P0 — 阻塞性

### 1. 文件交付链断裂
- bug: handler 返回 `{"path": "..."}` → harness 读 `result["files"]` → 产出: []
- 嵌套: handler 返回被包在 `{"content": {...}}` 里
- 影响: 01 截图、02 报告、所有 execute_code 产出都显示为空
- 修复: harness.py 从 `result["content"]["files"]` 和 `result["content"]["path"]` 读取
- 状态: 🔧 已部分修复（加了 code 但没生效，需要调试 content 嵌套层级）

### 2. expected_artifacts → "需补齐" 死循环
- bug: LLM 设 expected_artifacts="*.pdf" → 产出文件名不匹配 → CHECK 失败 → 补计划 → 循环
- 影响: 所有实例产生数百条 "需补齐" 消息
- 修复: batch_planner prompt 告诉 LLM 探索步骤不设 expected_artifacts
- 状态: ✅ 本轮已验证（0 条 "需补齐"）

### 3. Wrapper 注入旧路径 FileNotFoundError
- bug: wrapper 保存 "上一轮摘要" 包含绝对 UUID 路径 → 任务目录被清理 → 新轮 FileNotFoundError
- 修复: 去掉 "上一轮摘要"，任务自包含
- 状态: ✅ 已修复（干净重启）

---

## P1 — 严重影响

### 4. 消息无内容
- bug: QQ 消息全是 "deliverable file sent successfully"
- 用户看不到: 02 产出了 Self-Refine 对比报告，03 clone 了 TargetDiff
- 修复: DONE handler 不发送模板文本，改为发送产出文件摘要
- 文件: executor.py (_enqueue_visible_report 逻辑)

### 5. 实例完成后停止
- bug: STOP_PROJECT → 进程退出 → 无 wrapper 就停了
- 修复: wrapper 自动续任务（已有）或 event loop 空闲兜底
- 状态: ✅ wrapper 方案可用，event loop 方案未实现

### 6. Executor 脆弱
- bug: git checkout 抹掉自愈集成
- 修复: self_heal_hook.py 模块化
- 状态: ✅ 已创建模块，待集成到 executor

---

## P2 — 改善性

### 7. OODA 配置硬编码
- bug: 所有实例读 shared_projects/molgen_exploration/config.yaml
- 状态: ✅ 已修复（读 instance config）

### 8. proactive_evolver adapter=None
- bug: 不能调 LLM → 0 auto-applied
- 状态: ✅ 已修复（SimpleAdapter 从 .env 读 key）

---

## 修复顺序

第一轮: P0-1（文件交付链）— 让产出可见
第二轮: P1-4（消息内容）— 让用户看到做了什么
第三轮: P1-5（自动续任务）— 不用手动 restart

# Sprint 10 测试报告

**开始**: 2026-08-21
**方案**: docs/sprint10_严格测试.md（L1-L6 六层）

---

## Phase 1：单元测试第一批（✅ 完成）

**时间**: 2026-08-21
**命令**: `python3 -m pytest tests/ -v`

**结果**: 34 passed, 0 failed (0.65s)

| 文件 | 用例数 | 覆盖 |
|------|:--:|------|
| test_artifact_validator.py | 5 | pattern 匹配 / png↔jpg 兼容 / 内部产物排除 / required 语义 |
| test_correct_extension.py | 8 | md 冒充 png→md / 真 PNG-JPEG 保持 / 扩展名纠正 |
| test_evaluator.py | 9 | 评分 0/60/80/100 / 失败反思读写/过滤/limit / 技能卡片 |
| test_gap_filler.py | 8 | detect 已装/未装 / fill 三态 / 日志 |
| test_run_log.py | 4 | 写入字段 / 失败记录 / limit / 异常降级 |

**发现并修复的缺陷**:
1. evaluator.py record/load 忽略 workspace 参数（已修，统一 workspace 优先）
2. C4 技能卡片共享语义（固定根级 share/mind）
3. correct_extension 不可测（重构为模块级）

**遗留**: 无

---

## Phase 2：单元测试第二批（✅ 完成）

**命令**: `python3 -m pytest tests/ -v`
**结果**: 70 passed, 0 failed (2.28s) — 累计 70 用例（8 个测试文件）

| 新增文件 | 用例 | 覆盖 |
|------|:--:|------|
| test_self_review.py | 15 | 能力匹配回归（BLAST/AlphaFold/Scanpy 不误报）、_derive_weaknesses 回归 |
| test_direct_api.py | 11 | 模型分流（chat→v4-flash / 长生成→deepseek-chat / max_tokens） |
| test_vision_events.py | 10 | 配置解析、workspace 上溯、最近图回退 |

**重构**: direct_api 提取 select_model_and_tokens（可测试）

**遗留**: 无

---

## Phase 3：L2 事件级集成测试（✅ 完成）

**命令**: `python3 tests/integration/l2_events.py`（真实环境）
**结果**: 31/31 PASS (24.7s)

| 节 | 项 | 真实验证 |
|----|:--:|------|
| execute_code + run_log | 8 | 真实运行 + code_runs.jsonl 字段 |
| run_command | 7 | 成功/失败 exit_code + 日志 |
| ensure_tool | 4 | already_present / manual_required / unsupported / missing_param |
| read_image | 5 | qwen3-vl-flash 真实识别 "L2 VISION TEST 456" + 回退 + 非图报错 |
| web_capture | 4 | Edge 真实截图 8.7s，有效 JPEG 29190B |
| capability_inventory | 3 | agents=18、gaps=1、文件生成 |

**发现的问题**: 无实现缺陷（1 处测试脚本修正：async 事件需 asyncio.run；mind↔core 循环导入破环）

**遗留**: 无

---

## Phase 4：L3 生信 Agent 实测（✅ 完成）

**命令**: `python3 tests/integration/l3_agents.py`
**结果**: 29/29 PASS (8.6s) — 真实数据

| Agent | 关键结果 |
|-------|------|
| enrichment | 146 通路命中 / 124 显著（真实 Enrichr API） |
| plink | 20 变异 / 4 显著 / 关联 SNP 正确检出 |
| iqtree | tree.treefile Newick 含 8 物种 |
| bcftools | sites=10 / samples=2 / 过滤 VCF |
| diffexp | significant=102 / deg_results.csv |

**修正**: 5 处测试断言与 wrapper 实际契约不符（treefile/sites/significant 字段名）——非实现缺陷

**遗留**: 无

---

## Phase 5：L4 端到端（✅ 完成，暴露并修复 2 个缺陷）

**方式**: 注入真实任务到 03/04/05 实例
**结果**: 04 完整成功；03 首轮失败→修复→复测全链路成功；05 报告产出（执行步骤 LLM 代码问题）

**发现并修复**:
1. run_command `python` 不存在（systemd PATH 无 miniconda）→ python→python3 兼容替换
2. progress_done 模板固定 ✅ 不区分失败 → 模板加 icon（✅/❌ + 错误摘要）

**复测证据**: sum100.py=真代码(26B)、run_command exit=0 stdout=5050、code_runs 修复前后对比

**遗留**: write 步骤 content 偶发引用 design（LLM 行为）；execute_code 生成代码质量（有重试兜底）

---

## Phase 6：L5 回归 + L6 稳定性（✅ 完成）

**命令**: `python3 tests/integration/l6_stability.py`
**结果**: 17/17 PASS (28.6s)

- batch_plan 真实调用 ×3：全部成功（1.3-1.8s/次），JSON 完整
- write_design：真实 LLM 生成 19.6s / 10510B（不卡死）
- planner 过滤回归：cline/skyvern 不在列表，新 agent 在列表
- run_command 兼容 + 5 实例隔离正常

---

## Sprint 10 总结

**总用例**: L1 70 + L2 31 + L3 29 + L5/L6 17 = **147 项全部通过**（L4 端到端 3 任务另计）
**发现并修复缺陷**: 7 个（workspace 参数契约、C4 共享语义、run_command python 兼容、
progress_done 图标、可测试性重构 ×2、correct_extension 模块级）
**遗留**: write 步骤引用 design（LLM 偶发）、execute_code 代码质量（重试兜底）

---

## 2026-08-22 后续回归：真实交付、浏览器视觉回执与连续研究

**命令**: `python -m pytest -q`  
**结果**: **88 passed, 0 failed (3.68s)**

本次 88 项是当前代码的 pytest 回归集；上文 147 项是 Sprint 10 分层测试历史基线，
包含独立 L2/L3/L6 脚本，不应将两个数直接相加或说成本轮重跑了 147 项。

**新增回归范围**：

- 文字/文件只有在运行时 callback 获得回执后才返回成功。
- 小红书事务的三个关键视觉步骤：截图、qwen 读图、图片发送、文字发送。
- 登录状态协议、可见浏览器与上传控件验收。
- 分子第三轮 SA/随机基线和第四轮 QED/SA 多目标选择。
- Research Loop 从第三轮自动进入第四轮，并在证据边界到达时停止。

**实机补充验收**：01 真实发送 3 张截图和 3 条 qwen 描述；
02 真实执行第 3/4 轮并发送 2 份 PDF 和 1 份 CSV。

## 2026-08-23 后续回归：治理基础

**命令**: `python -m pytest -q`  
**结果**: **98 passed, 0 failed**

新增覆盖：分级上下文的实例选择、字符预算和 provenance；Receipt 的停止/续跑互斥；
上一轮产物承接；真实 queue ack；Issue 去重；进化成功标准与回归 promotion gate；
双实例槽位硬门；高置信运行信号；声明式协议的先记录后入队，以及协议新周期累计轮次。

同时执行 Python 编译检查、七份 Schema JSON 解析、catalog YAML 解析、两份协议加载和
`git diff --check`。这次没有重跑 Sprint 10 的独立外部 L2/L3/L6 脚本，因此仍不与 147 相加。

## 2026-08-23 后续回归：持续 Campaign Controller

**命令**: `python -m pytest -q`  
**当前结果**: **132 passed, 0 failed (9.34s)**

新增覆盖 Campaign 契约、五实例默认 WorkItem、两槽幂等 dispatch、人工安全门、持久 inbox message ID、
产物/真实 delivery 验收、Receipt/NextAction 承接、真实 enqueue ID、租约超时重试、三轮相同证据熔断、
deadline 最终日报、dashboard Campaign 摘要以及确定性 soak。

另运行 `python scripts/simulate_campaign_soak.py --cycles 120`：120 个 fake-clock tick 内 dispatch
241 个有唯一 task ID 的 WorkItem（122 ticks），最大并发槽 2，0 失败，最终 completed，生成 25 份阶段/最终报告。
模拟使用隔离临时工作区和伪任务日志，用于验证状态机、恢复、预算和收敛；不视为真实 QQ、真实模型或外部网络验收。

## 2026-08-23 后续回归：30 分钟 Campaign 实跑修复

**命令**: `python -m pytest -q`  
**当前结果**: **137 passed, 0 failed (5.07s)**

新增覆盖：blocked Campaign 仍按间隔创建阶段报告；终态 blocked 回调幂等；失败任务无需等待 Lease 即重试；
小红书上传要求审计保留 3 个视觉步骤并报告 3 次模型调用；planner 与逐步骤模型调用去重累计；
无界分子数据扫描改为有深度、排除目录和文件数量上限。实机阶段报告替代 WorkItem 已获真实 QQ 回执。

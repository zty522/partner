# 03 实例最小链渐进任务模板

**日期**：2026-08-27
**状态**：任务设计完成，等待 03 实机执行
**配套**：Sprint 15 + Codex 5 阶段建议（任务 1/3）
**前置**：当前 partner 工作区有 18 个 Codex 未提交改动（10 对 canary 配套），
必须保持不动；本任务不对 partner 框架做任何修改

## 0. 03 真实可用能力（来自 harness.py 实测）

**03 真实可用的 endpoint**：
- atomic_read_state、原子_list_project_files、原子_inspect_file
- atomic_write_artifact、create_file
- smart_llm_structured_action、run_shell
- send_user_text、push_files

**03 不可用的 endpoint**：
- app_focus / app_send_keys / app_screenshot_window（01 XHS 专属）
- analyze / check_quality（03 实例不持有）
- atomic_compose_structured_result

**路径安全白名单**：
- 写：当前 TaskInstance 的 working_dir
- 读：partner 仓库内 partner/、partner/tests/、docs/
- 读：workspace 内 external/code/、external/literature/
- 读：受治理的 share/evidence/、share/mind/governance/、share/projects/
- 其它绝对路径直接拒绝

**不允许的 planner 行为**：
- planner 不得自行 record_iteration
- 不得启动 Campaign / Research Loop / next_iteration / strict_reflect / self-heal
- 不得发送末尾总结或下一轮预告
- 不允许写 Receipt（Receipt 只在治理层生成）

## 1. 任务 1/3：只读诊断（不改代码）

**目标**：验证 03 能精准读出 partner 代码里的一个真实可观察的现象，并写成诊断 markdown。
**不改任何文件**，只生成 1 个产物文件。

**Bug 候选**（已知真实存在）：
partner/mind/harness.py 中 _is_placeholder_content 函数（行号 3147 附近）。
- 当前实现里有一段对 .md / .py 文件用 len(content) < 200 判定占位的逻辑
- 已知行为：短但非占位的内容（如 < 200 字的真实摘要）会被误判
- 03 的任务：只读这段代码，写一份诊断 markdown，说明当前实现是什么、阈值在哪、什么场景会误判、改进方向（但不实现）

**任务原文（直接发 QQ 给 03）**：

```
[manual_stable 任务 1/3：03 只读诊断]
请只读 partner/mind/harness.py 中 _is_placeholder_content 函数（行号 3147 附近），
不要修改任何代码。

写一份诊断报告到当前任务 working_dir 下的 placeholder_content_diagnosis.md：
1. 真实复制该函数源码（不省略、不改写）
2. 列出当前对 .md / .py / 其他扩展名的判定条件
3. 列出至少 2 个真实场景会被误判为占位
4. 给出 1-2 条改进方向（仅描述，不实现）
5. 不准修改 harness.py 或任何文件

完成后停止，等用户消息。必须真实发送：收到、计划、每步完成、最终结果四条 QQ 消息。
```

**endpoint 白名单**：atomic_inspect_file、atomic_read_state、create_file、send_user_text
**禁止 endpoint**：app_*、analyze、check_quality、atomic_write_artifact、atomic_compose_structured_result
**绝对路径**：partner 仓库根（只读）、当前任务 working_dir（写）
**产出文件**：placeholder_content_diagnosis.md，预期字节 800-2000
**验收硬门**：
1. placeholder_content_diagnosis.md 存在且字节 > 500
2. 文件里能 grep 到 _is_placeholder_content 真实函数体（不是占位）
3. partner/mind/harness.py mtime 未变（git diff 显示无修改）
4. 03 实例 QQ 真实发送"收到/计划/每步完成/最终结果"四条
5. pytest 全量仍 333 passed

**核验方式**（我看的不是报告内容，是这4 项实物）：
1. ls -la <working_dir>/placeholder_content_diagnosis.md（文件存在 + 字节数）
2. grep -c "_is_placeholder_content" <working_dir>/placeholder_content_diagnosis.md（含函数引用）
3. git -C /mnt/e/work/partner diff partner/mind/harness.py 为空（未修改）
4. pytest -q --tb=line 全量通过（333 passed in ~16s）

## 2. 任务 2/3：单文件小修复

**目标**：找一个真实小 bug，单文件修复 + 加 1 个针对性 pytest + 全量回归通过。

**前提**：任务 1/3 通过（说明 03 能精准读懂代码）。

**Bug 候选**（任务 1/3 后根据实际诊断结果确定，或采用备选）：
- 备选 1：partner/planner/batch_planner.py 中未使用的 import / dead branch
- 备选 2：partner/governance/manual_runtime.py 中某个 edge case 缺处理
- 备选 3：基于任务 1/3 的诊断结果，针对 _is_placeholder_content 阈值做小修
（不修改判定逻辑本身，只补 1-2 行边界处理）

**任务原文（任务 1/3 通过后实际填入）**：

```
[manual_stable 任务 2/3：03 单文件修复]
请修改 partner/<FILE> 中 <FUNCTION> 函数的 <BUG>：
1. 先读取 <FILE> 第 N 行附近的代码
2. 确认 bug 存在
3. 修改 1 个文件，改动控制在 <10 行
4. 在 partner/tests/test_<name>.py 加 1 个测试覆盖这个 bug
5. 跑针对性测试：pytest partner/tests/test_<name>.py -q
6. 跑全量：pytest -q（必须 333 passed 不变）
7. 写修复说明到 <working_dir>/fix_explanation.md
8. git diff 检查无意外修改

完成后停止，等用户消息。必须真实发送：收到、计划、每步完成、最终结果四条 QQ 消息。
```

**endpoint 白名单**：atomic_inspect_file、atomic_read_state、atomic_list_project_files、
atomic_write_artifact、create_file、run_shell（pytest）、send_user_text、push_files
**禁止 endpoint**：app_*、analyze、check_quality、atomic_compose_structured_result
**绝对路径**：partner 仓库根（只读 partner/ 和 partner/tests/）、当前任务 working_dir（写）
**产出文件**：1 个修改文件 + 1 个新 pytest + 1 个 fix_explanation.md
**验收硬门**：
1. git diff --stat 显示改动控制在 1 个文件 + 1 个新测试文件
2. pytest partner/tests/test_<name>.py -q PASS（真实 stdout）
3. pytest -q 全量仍 333 passed
4. fix_explanation.md 含：bug 描述、修改前/后代码对比、为什么这样修、回滚步骤
5. 03 实例 QQ 真实发送"收到/计划/每步完成/最终结果"四条

## 3. 任务 3/3：两文件以内修改

**目标**：跨文件小修改（如改一个函数 + 加一个 import 或新工具模块），跑全量回归。

**前提**：任务 2/3 通过。

**Bug 候选**：基于任务 2/3 的修复方向，自然延伸。

**任务原文（任务 2/3 通过后实际填入）**：

```
[manual_stable 任务 3/3：03 两文件修改]
请完成 partner/<FILE_A> + partner/<FILE_B> 的两文件修改：
1. <FILE_A> 修改 <FUNCTION_A>：<具体改动>
2. <FILE_B> 修改 <FUNCTION_B> 或新增小工具：<具体改动>
3. 在 partner/tests/ 加 2 个测试覆盖新行为
4. 跑针对性：pytest partner/tests/test_*.py -q
5. 跑全量：pytest -q（必须 333 passed 不变）
6. 写修复说明 + 回滚方案到 <working_dir>/two_file_fix.md
7. git diff 自检：只动这两个文件 + 新增测试

完成后停止，等用户消息。必须真实发送：收到、计划、每步完成、最终结果四条 QQ 消息。
```

**验收硬门**：
1. git diff --stat 显示只动 2 个源文件 + 新增测试文件
2. pytest partner/tests/test_*.py -q PASS
3. pytest -q 全量仍 333 passed
4. two_file_fix.md 含：跨文件影响分析、回滚步骤（具体到 git checkout 命令）
5. 03 实例 QQ 真实发送"收到/计划/每步完成/最终结果"四条

## 4. 通用执行纪律（按 manual_stable_core.md）

**实例切换**：
- 任务开始前：python scripts/partner_control.py switch 03 02
- 任务完成后：python scripts/partner_control.py switch 01 02
- 每轮切换一次，03 任务不接力跑

**消息验收**（每轮 03 必须发齐）：
1. 用户原始消息只处理一次
2. 第一条是自然语言"收到"，不是 Campaign 模板
3. 每个计划步骤都有 step_start + step_complete
4. 失败步骤明确失败原因
5. 文件只有渠道 callback 确认后才称"已送达"
6. 最终消息概括实际结果和边界，结束后不生成下一轮
7. 不允许 thinking-only、未闭合 JSON、空模板、虚假能力声明、伪造运行指标

**失败处理**：
- LLM 行为限制（content placeholder / thinking-only / 相对路径）：如实记录，不算"修"
- framework bug（路径安全、endpoint 错配、JSON 截断）：停任务，按 sprint 7/change_log 模式修根因
- pytest < 333：立即回滚

## 5. 监督的 7 个观测点

1. 03 执行是否真实发生：git diff 有修改、pytest 真实 stdout、产物字节 > 0、QQ 真实送达
2. LLM 行为限制 vs framework bug 区分：如实分类，不混为一谈
3. 05 任务前的准备：05 不会在本阶段执行，等 03 三轮跑完后才进入
4. 业务密度 vs 空转密度：03 任务必须产生真实产物
5. 5 实例真实健康：pgrep + heartbeat + inbox 三信号
6. pytest 全量基线不被破坏：333 passed 必须保持
7. 文档纪律同步：change_log.md + ADR（仅在 framework bug 修复时）

## 6. 不做的事（明确边界）

- 不让 03 跑 Campaign / Campaign work_item / 长期项目
- 不让 03 跨实例写文件（partner/instances/03/ 之外的实例状态）
- 不让 03 改生产 prompt / config / control_policy.json
- 不让 03 自动 promotion 或自动续轮
- 不在没有 03 通过任务 1/3 的情况下让 03 跑任务 2/3
- 不在没有 03 通过任务 2/3 的情况下让 03 跑任务 3/3
- 不在没有 03 全部通过的情况下进入任务 4（05 独立审查）

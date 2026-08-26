# 手动稳定核心

**层级**：L1 canonical  
**状态**：当前唯一生产默认  
**配置**：`runtime.mode=manual_stable`

## 目标

先把五个 Partner 实例作为可靠的手动协作伙伴做好。用户向某个实例发一条消息后，该实例直接确认、
完成这条指令所需的实际步骤、逐步汇报并结束。长期轮转、自动续轮和自进化暂不参与这条路径。

## 单一执行路径

```text
QQ 用户消息
  → QQ bridge 写入对话证据并调用 enqueue_user_message
  → USER_MESSAGE 立即发送“收到指令”
  → 简单聊天：生成一次 direct reply
  → 复杂任务：生成一个有界 BatchPlan
      → 发送任务/步骤概览
      → 每个真实步骤发送 step_start
      → 执行并验收该步骤
      → 发送 step_complete（成功或真实失败）
  → 发送最终结果和已确认送达的文件
  → Harness/交付硬门通过后，由治理层生成唯一项目 Receipt 与 RL 轨迹
  → 停止，等待下一条用户消息
```

不得把普通消息改送到 Campaign controller、deterministic Campaign handler、Research Loop 或另一套
固定报告模板。内部事件名、JSON、路径和 tool transcript 不应直接发给用户。

规划器生成的叶子 `post_message/send_user_text` 会被执行前移除：收到、计划、步骤和最终回执只由
执行器的统一消息协议负责，避免计划正文与真实验收互相矛盾。`record_iteration`、
`continuous_project_step` 仍为硬禁止事件；Receipt 只在任务、产物、交付和最终验收都通过后生成。

用户明确点名的同实例历史 Task 产物可以只读，并会被确定性注入计划、连接到证据提取步骤；这用于
“承接上一轮”，不开放任意跨实例可变目录。输出文件不能反过来计入本轮输入。

04 已晋升 `manual_stable_truth_audit_v2`，但作用域仅为
`literature_github_learning:manual_final_artifact_truth`：普通 04 任务读取文件并生成 Markdown/TXT 时，
Planner 必须为每个输入保留连续的 `source_path/evidence_quote`，治理层再打开源文件逐项核验。
缺源、改写引文或虚假能力声明会拒绝 Receipt。该策略不启动下一轮，也不自动作用到其他实例或代码产物。
生产路径会在直接来源读取与报告生成之间确定性插入命名源抽取，先固化真实路径、逐字引文和受限摘录，
再交给 LLM 做语义组织；最终门仍重新打开原文件独立复核。任务生成后用于校验的回读文件不算外部输入，
避免报告被错误要求“引用自己”。

## 五实例角色

| 实例 | 手动职责 | 特殊要求 |
|---|---|---|
| 01 | 小红书账户与内容维护 | 浏览器关键步骤截图、视觉模型中文说明、图片和说明送达；发布仍需授权 |
| 02 | 分子生成方法与实验 | 真实数据/源码/命令/指标/边界；PDF 应有领域分析而非通用模板 |
| 03 | Partner 框架与前端 | 小改动、测试、兼容性和回滚证据；不得自行开启 self-heal |
| 04 | 文献与 GitHub 学习 | 官方来源、固定版本、最小复现、借鉴与未集成边界 |
| 05 | Agent 自进化探索 | 当前仅响应手动研究指令；不得自动修改生产代码或自动 promotion |

## 运行门

- 同时最多两个实例，由 `python scripts/partner_control.py switch <id> [id]` 手动切换。
- `switch` 会停止移出的实例并重启选中实例，使代码/config 更新真正加载。
- 默认常驻槽为 01/02；验证其他实例时按 03/04、05/01 轮换，结束后恢复 01/02。
- 共享 config 只能由显式配置操作修改。实例启动不得把自己的 instance workspace 写回共享配置。
- `python scripts/partner_status.py` 必须显示 `mode=manual_stable`；Campaign 只能显示历史终态，不能 active。

## 文件与证据边界

- 新文件只能写入当前 TaskInstance 的 `working_dir`；绝对输出路径会被约束或拒绝。
- `atomic_inspect_file` 是只读能力，可读取 Partner 的 `partner/tests/docs`、workspace 的
  `external/code`、`external/literature`，以及受治理的 `share/evidence`、
  `share/mind/governance`、`share/projects`。不在白名单的绝对路径仍拒绝。
- 不允许用 `run_shell + cat/cp` 绕过上述边界；跨实例学习必须先进入 immutable evidence bundle，
  05 不能直接读取另一个实例的可变 task 目录。
- Planner 必须在执行前核验 event、依赖、输入存在性、写入位置和依赖引用；失败后只允许有界语义修复。

## 来源型 LLM 步骤

`extract` 只允许看到步骤参数中明确提供的 `data/content/sources`，不得混入动态 Partner 上下文。
输出必须是完整 JSON；存在 `evidence_quote` 时必须逐字出现在输入中，按命名源输出时还必须来自
对应源。无证据写 `not_found`。截断 JSON、跨源错引、空内容和 thinking-only 都是失败，不能写成产物。

## 在该模式强制关闭

- Campaign 创建和入队；
- Research Loop 和任务结束自动续轮；
- `strict_reflect`、`next_iteration`；
- 自动 self-heal/tree search；
- CRON_TICK、WAKE_UP 产生业务任务。

缺配置、配置读取失败或未知能力都要 fail closed，不得偷偷恢复自治。

## 消息验收

一次复杂任务至少检查：

1. 用户原始消息只处理一次；QQ/桌面双写不能形成两个任务。
2. 第一条是自然语言确认收到，不是 Campaign 模板或最终文件。
3. 每个计划步骤都有开始和完成消息，内容说明动作与结果。
4. 失败步骤明确失败原因；不能用“完成”包装异常。
5. 文件只有渠道 callback 确认后才称为已送达。
6. 最终消息概括实际结果和边界；结束后不会自行生成下一轮。
7. “我将执行”、thinking-only、未闭合 JSON、空/短模板、虚假能力声明与伪造运行指标均不能通过。
8. 失败后如曾生成错误 Receipt，必须用 append-only correction 作废；不得篡改历史。

01 浏览器任务还需对协议关键截图分别调用视觉模型，并分别发送截图和中文说明。普通逐步文字消息
不能替代该要求。

## 修改保护

任何模型、调度器、Harness 或自进化实验若要改变上述路径，必须先：

1. 明确声明是兼容改动还是实验旁路；
2. 保留 `manual_stable` 默认值和禁用门；
3. 增加回归测试；
4. 用一个实例实机验证完整消息序列，再验证五实例双槽轮换；
5. 若体验退化，回退实验改动，不调整本合同来迁就实现。

相关决定见 `docs/decisions/0004-manual-stable-production-baseline.md`。

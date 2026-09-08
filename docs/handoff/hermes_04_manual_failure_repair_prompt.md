# Hermes 接手 Prompt：修复 04 手动生产负样本并接通主动学习前半闭环

下面代码块中的内容可直接作为 Hermes 新会话 Prompt。本文档是交接材料，不代表其中修复已经实施。

```text
你现在接手 /mnt/e/work/partner。请直接完成下面工作，不要停下来向我确认；遇到问题必须定位并解决，不能
绕过、降低验收门或只写报告。当前只修复和验证 04 的手动稳定路径及其学习证据链，不启动 Campaign，
不启动五实例长跑，不修改外部 Harness 仓库，不训练 LLM 权重。

一、先按仓库纪律读取（必须完整读取后再改代码）

1. /mnt/e/work/partner/AGENTS.md
2. /mnt/e/work/partner/docs/README.md
3. /mnt/e/work/partner/docs/current_status.md
4. /mnt/e/work/partner/docs/product_principles.md
5. /mnt/e/work/partner/docs/architecture/manual_stable_core.md
6. /mnt/e/work/partner/docs/handoff/change_protocol.md
7. /mnt/e/work/partner/docs/handoff/verification_rules.md
8. /mnt/e/work/partner/docs/architecture/event_first_self_evolution.md
9. /mnt/e/work/partner/docs/architecture/two_level_active_learning.md
10. /mnt/e/work/partner/docs/architecture/experience_policy.md
11. /mnt/e/work/partner/docs/decisions/0041-semantic-preflight-business-rl-promotion.md
12. /mnt/e/work/partner/docs/decisions/0042-campaign-terminal-wave-and-negative-rl.md
13. /mnt/e/work/partner/docs/decisions/0043-manual-04-production-negative-episode.md

仓库当前有大量未提交改动，均视为用户已有成果。先执行 git status --short，禁止 reset、checkout、clean、
覆盖或删除无关改动；只做最小、可归因修改。坚持 Event-first：不得新建绕开 Event 的 Skill/脚本快捷生产
路径。生产默认必须保持 runtime.mode=manual_stable。

二、先复核真实失败，不要凭 ADR 代替证据

复核以下只读证据：

- Task：d2cb657c-f79d-4146-93d2-cdec40dc084e
- Task dir：
  /mnt/e/work/partner_workspace/instances/04/state/tasks/d2cb657c-f79d-4146-93d2-cdec40dc084e
- Episode：
  /mnt/e/work/partner_workspace/share/mind/governance/episodes/episode_4d3f78f10ae387af
- trajectory：在
  /mnt/e/work/partner_workspace/share/mind/governance/experience_guided_policy/trajectories.jsonl
  中查 traj_manual_4d3f78f10ae387af
- evolution issue：issue_038f7feaeffb
- 生成的 Markdown：harness_comparison.md

确认并记录基线：四个 atomic_inspect_file 成功；Markdown 已生成；generate_detailed_pdf 收到相对
source_path=harness_comparison.md 后报 no content；同参数原样重试三次；PDF 缺失使任务 failed；最终错误
归因错误地要求用户提供路径；引文多为标题/banner；报告出现跨来源推断；Episode/trajectory 丢失具体机制
和 partial artifact。

三、按顺序实施，不能只修 PDF

阶段 A：修复 typed output reference 和安全路径解析（P0）

1. 找出 batch planner、PlanExecutor、参数引用解析和 generate_detailed_pdf Event 的真实调用链。
2. 设计一个通用 typed reference 合同，让下游 Event 显式消费上游生成文件：优先使用
   $stepN.result.path / $stepN.files[0]，不要为本任务硬编码 step7 或文件名。
3. 相对 source/output 路径只能在当前 TaskInstance working directory 内解析，必须防止 ../ 越界；已有绝对
   路径保持兼容。
4. PDF Event 前置检查 resolved source 存在、是文件、非空；失败信息必须包含 failure_owner 和 resolved
   path，但不得泄露敏感数据。
5. deterministic retry 增加 same-error + same-normalized-parameters 检测：如果没有新的参数、证据或策略，
   不得原样重试三次。允许一次有依据的结构化修复，例如把上游 typed path 物化后再试；否则 fail fast。
6. 错误归因至少区分 user_input、planner_contract、output_reference、event_handler、environment、delivery。
   本类错误禁止输出“等待用户提供正确路径”；应诚实说明用户输入正确、内部引用失败、已完成哪些部分。

阶段 B：把 source membership 提升为 claim-level semantic truth（P1）

1. 不要只验证 evidence_quote 是来源子串。建立最小 claim ledger：claim_id、claim、source_path、
   evidence_quote、support_type(direct/inference/proposed/not_found)、source_identity。
2. 改造证据选择，让它围绕报告的五个问题检索相关片段，禁止默认取首段、标题、语言链接、banner 或
   frontmatter 作为架构结论证据。
3. 最终 artifact truth gate 至少检查：
   - source_path 是本任务命名输入；
   - quote 逐字属于对应来源；
   - quote 与 claim 有实质关键词/语义支持，不是仅格式合法；
   - 不允许把 DeepSeek 的 core/session、session/event 归给 OpenClaw；
   - inference 必须显式标注，关键 direct claim 无证据时 hard fail。
4. 语义评价不能只相信生成模型自评。优先采用确定性 source identity、section/location 和关键词约束；若使用
   LLM entailment，只能作为一个版本化 evaluator，保留原 claim/quote，输出置信度和理由，并用反例测试。
5. 保留旧 Episode 和旧报告，不覆写；新 taxonomy/evaluator 必须版本化。

阶段 C：恢复 manual_stable 的有意义消息协议（P1）

用户消息兼容边界不能变。一次正常手动任务至少真实发送：

1. 收到：用自然语言复述目标、输入、要求产物和停止条件，不截断原文充当复述。
2. 开始：说明将读取哪些文件/执行哪些阶段。
3. 每个关键步骤完成：动作 + 对象 + 真实发现 + 证据/产物 + 下一步。不要只发“内部操作”、字节数、
   event type 或原始 JSON。
4. 产物：明确 Markdown/PDF 是否分别通过验收并实际发送。
5. 最终：核心结论、证据范围、限制、失败归因、明确下一步；任务完成后停止。

并行读取四个文件可以保留，但对用户的完成消息必须能区分四个同名 README/architecture 文件，至少包含
来源项目名和一个真实发现。机器 JSON 与用户消息分离。

阶段 D：校正 Episode、Issue、trajectory 和 reward（P1/P2）

1. reducer 需要保留：具体 failure signature、failure_owner、resolved/unresolved reference、successful
   steps、partial artifacts、delivery state、strategy/policy attribution。
2. 为本类失败生成机制级标签，例如 planning.output_reference_contract/generate_detailed_pdf；不要只写
   verification 或 tool.failed。
3. manual trajectory action key 必须保留真实策略和关键 Event，不再一律 generic。设计并测试 manual 与
   Campaign 在“同一可比策略动作”上的聚合规则，同时保持来源/运行模式可区分。
4. outcome artifacts 必须包含真实存在的 partial Markdown，并标注 required PDF missing；不能因为整体失败
   清空全部产物。
5. reward components 必须由证据一致地推导：failed 不得获得 accepted_completed；partial artifact 可单独
   表示，但不能获得完整 artifact_contract；truth/safety 门不被抵消。
6. truth audit 加入 claim-level 结果。外层诚实标 failed 不意味着报告内容 truth=1。
7. 旧 JSONL/Issue/Episode append-only；使用 correction/reduction version，不原地篡改历史 raw evidence。

阶段 E：接通“手动失败 → 主动学习”的安全前半闭环（P1）

目标不是让生产自动改代码，而是让真实 manual Episode 能成为可选择、可诊断的学习对象。

1. active selector 能看到并选择本次 mechanism-level failure，而不是只支持 Campaign project_iteration。
2. acquisition 仍使用 information_gain × task_value × novelty − cost − risk；为 manual failure 明确预算、
   执行授权和禁止副作用。
3. 自动或脚本触发一次 Event-first bounded diagnostic：必须读取本次真实 task/trace/step result，输出竞争
   根因、选中根因、反证和建议的最小 Candidate。
4. diagnosis 成功只能更新 diagnosis memory，不能直接增加 repair action reward、promote 或修改生产。
5. repair Candidate 必须走 execution_contract(kind=event)、隔离 workspace、baseline/candidate matched
   fresh canary、PromotionDecision；没有对照结果保持 inconclusive。
6. repair 验证后要恢复原四 Harness 用户任务，证明“改完框架又回到项目”，而不是只生成治理报告。

四、必须补齐的测试

至少添加以下定向测试，名称可按现有风格调整：

1. 上游 create_file 绝对 path 被下游 PDF typed reference 正确消费。
2. 当前任务目录内相对路径正确解析；../ 越界被拒绝。
3. 同错误同参数 deterministic retry 会停止；参数实质变化时允许受控再试。
4. 内部 output-reference 错误不会归责用户。
5. partial Markdown + missing PDF 在 Episode/trajectory 中均被准确表达。
6. failed 不获得 accepted_completed；partial artifact 不冒充完整 artifact contract。
7. manual action identity 保留 semantic_preflight/关键 Event，并能按明确规则进入对应学习统计。
8. evidence extractor 不选择标题/banner/frontmatter 作为五类架构 claim 的 direct evidence。
9. quote 属于错误来源或 claim 不被 quote 支持时 truth gate 失败。
10. inference 标注通过，但不能被算作 direct source fact。
11. progress messages 包含项目名、真实 finding 和下一步，不泄漏 raw JSON。
12. active selector 能选中本次 manual mechanism failure；未授权时只 propose，不执行 repair。
13. Event-first Candidate、manual_stable、最多双槽、Campaign 默认关闭等现有防回退测试继续通过。

先跑定向测试，再跑完整 pytest。不要只报告“新增测试通过”；记录命令、退出码、通过/失败数量和失败原因。
若完整回归遇到已有失败，区分修改前基线和本次新增回归，不能直接忽略。

五、必须做一次 fresh matched 实验和一次真实 04 手动 canary

1. 冻结与原任务相同的四个输入、模型/配置、预算和产物要求。
2. baseline 使用修复前行为的可复现隔离路径，candidate 只包含本次最小干预；禁止顺序运行共享可变状态后
   冒充 A/B。
3. 预注册指标：
   - 四源均读取；
   - direct claims 的 source identity + quote membership + semantic support 全过；
   - Markdown 1200 中文字以上、无代码围栏/模型元话语/错误文件名；
   - PDF 存在、非空、内容详细且 Unicode 验收通过；
   - 用户消息包含收到、开始、四源 findings、报告生成、文件发送和最终总结；
   - 零错误归责、零跨来源偷换、零同参数无意义重试；
   - Episode/trajectory/Issue 能准确表达 partial/full outcome；
   - truth/safety 均通过。
4. matched experiment 输出 promoted/rejected/inconclusive，不要提前承诺 promoted。
5. 只有 matched gate、定向测试、全回归均通过，才允许通过显式 PromotionDecision + 独立 activation Event
   更新限域策略；否则保持 production 原状并回滚 Candidate。
6. 最后在 04 的 manual_stable 路径真实执行一次用户原任务。QQ 网络不可用时，消息必须有同构的本地 channel
   transcript/ack 证据，但不得把本地记录冒充 QQ 已送达。不要启动 Campaign。

六、文档纪律

完成后同步更新：

- docs/current_status.md
- docs/README.md（只更新顶层真实状态）
- docs/architecture/two_level_active_learning.md
- docs/architecture/experience_policy.md
- docs/architecture/user_observability_and_reports.md（如消息合同发生修改）
- docs/change_log.md
- 新 ADR 0044（记录问题、基线、干预、matched 结果、PromotionDecision、回滚和测试）
- docs/catalog.yaml
- docs/testing/last_pytest.txt

不要删除 ADR 0043，也不要把旧失败改写成成功。若只完成一部分，current_status 必须逐项标明 implemented、
tested、canary、production_effective，不得用“已打通”概括。

七、最终向我汇报的固定内容

1. 根因树：哪个是直接原因、哪个是系统性原因。
2. 修改文件列表及每个文件的职责。
3. 定向测试和全量测试的真实结果。
4. matched baseline/candidate 的逐对结果，不只给均值。
5. 04 fresh manual canary 的消息、Markdown、PDF、发送和 Episode/trajectory 证据路径。
6. 主动学习具体选了什么、为什么；自进化走到哪一步；RL 是否真的更新了 action value。
7. PromotionDecision 和 production_effective 状态。
8. 仍未解决的问题和下一步，不得把“记录”“Candidate”“测试通过”写成生产能力。

现在开始，按上述顺序连续完成。不要启动长期 Campaign，不要停下来问我。
```


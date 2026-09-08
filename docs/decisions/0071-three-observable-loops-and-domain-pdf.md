# ADR 0071：三条可观察闭环与领域化 PDF

**状态**：Accepted（短期闭环已接入；长期统计门仍 open）  
**日期**：2026-09-08

## 决策

Partner 不再用“任务跑了很多轮”统称自主进化。生产状态分为三条独立证据链：

1. 项目迭代：Receipt 承接、动作改变、真实产物与 NextAction/诚实停止；
2. 主动学习：Episode 观察、信息价值选择、诊断/实验、后验，以及学习后下一动作的实际改变；
3. 自进化：受机制约束的代码/策略 Candidate、匹配对照、回归、晋升/拒绝和回滚。

新增 `project/action_selected` 与 `active_learning/project_action_selected` 账本事件。后者保存正常选择、干预选择、
全部备选和选择原因，解决过去“学习执行了但不知道是否影响业务”的断链。新增四个可调用审计 Event：
`project_iteration_audit`、`active_learning_effect_audit`、`self_evolution_effect_audit`、
`sprint19_acceptance`。

## Candidate 语义修正

Candidate 是待验证改进，不是第二套运行时。已生效策略的重复探测不得生成新的生产 Candidate；旧实现中
`already_effective / production_effective=true` 会夸大成功数量，现改为
`no_new_candidate / production_effective=false / existing_production_effective=true`。
该结果现在进入独立学习观察，不再推进 ProjectState 或领取 `business_progress`；首次真实确认可获得有限学习
Reward，完全相同的再次确认按重复负样本处理。判定必须读取真实 Candidate JSON，不能仅相信消息文本。

第三个真实行为型 Candidate `code_candidate_code_surface_novelty_20260908014833` 从 05 的重复结果轨迹提出，
向白名单策略表加入 `turn_code_surface_rotation_v1`。baseline turn 7/8 均无输出，Candidate 分别给出
`code_variant=7/8`；15 项聚焦回归通过后写入完整 propose/experiment/promote/activate Event 链。

## PDF 决策

机器 JSON 继续统一，用户 PDF 不统一成一种写作模板。渲染器支持 `editorial`、`research`、`lab_note`、
`source_review`、`engineering_change` 五种领域视觉语法：标题、左对齐封面、副标题、页眉、主色和强调色按领域
变化；正文继续由各领域独立的信息架构生成。PDF 美观是用户体验硬门，但不计业务 Reward。

## 实机证据与边界

`scripts/run_seed_all.py --run-id sprint19_three_loops_20260908 --reopen-blocked` 已投递五实例。01、02、04、05
最新真实结果为 Reward 0.85、business progress true、duplicate false。03 的首轮由仍缓存旧 Event 白名单的
热进程报 `unsupported evolution event_type: project/action_selected`，没有假记成功；它随后自动生成 Episode
并进入五步 Event-first 学习链。新增 rolling-upgrade 兼容降级后，redrive 的学习干预把正常候选
`03_md_integrator_smoke` 改选为 `03_md_temperature_sweep`，产生成功 Receipt `receipt_9b7735ed7653`：4/4 模拟
通过，最坏相对能量漂移 `1.9449e-05`，Reward 0.85。旧失败作为真实负样本保留。

最终生产 `sprint19_acceptance` 为 passed：五个项目迭代审计 5/5；主动学习的观察、完成、选项记录、改选、
成功执行和后验六门全过；自进化四门全过。最终完整仓库回归为 949 passed、2 warnings。

随后 05 生产复验 Task `86e1e1d6-ce35-4bb7-95aa-5a36f4f17102` 命中已饱和的 Candidate recipe，结果被正确
归为 `candidate_no_change_observation_recorded`：`project_state_mutated=false`、`business_progress=false`、
`production_effective=false`，Reward 仅来自一次新的学习证据。它没有篡改此前通过的项目迭代证据。

短期审计通过不代表长期 RL 成熟。长期结论仍需三种机制、三个真实日期、足够样本、回滚和零 false-success。

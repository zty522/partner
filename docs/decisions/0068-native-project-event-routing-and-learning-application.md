# ADR 0068：原生项目 Event 路由与学习后应用闭环

**状态**：Accepted / Production verified; superseded in truth details by ADR 0069  
**日期**：2026-09-07  
**取代范围**：修正 ADR 0062 中“动态并发可超过双槽”和通用 LLM 自由规划原生续跑的实现；不改写历史账本。

## 背景与真实故障

QQ 5/5 恢复后，五实例仍不能持续推进。真实生产证据显示：原生项目请求进入通用 LLM planner 后，常被规划成读取项目文件再生成报告，或引用不存在的路径；真实动作门随后拒绝。失败虽能形成 Episode 并执行主动学习，但原有 matched Event 只验证 Claim Ledger 与输出引用的固定夹具，未验证该 Episode 对应的项目机制，恢复项目后仍会选择同类报告动作。旧失败计数最终使五实例全部 `BLOCKED`，活动槽为 0。

## 决策

1. 保持 Event-first。带有 `[instance_native=true] [native_kind=project]` 的生产续跑不再交给通用 LLM 自由生成 Event 图，而由 `_native_project_execution_plan` 按实例、规范 project_id 和持久 `project_steps` 选择一个已注册、有界、可审计的真实 Event。
2. 普通用户消息继续走 `manual_stable`；`continuous_project_step` 等长期 Event 仅对通过实例原生标记与项目归属校验的请求开放。
3. 五实例分别执行：01 来源/主张/风险队列；02 分子生成、骨架多样性、合成可及性、多目标优化；03 velocity-Verlet 数值实验；04 Harness 源码概念与采用缺口；05 Hermes/Partner 源码合同与聚焦回归 Candidate。
4. `real_action_contract` 接受上述 Event 名称只是“动作信号”，仍同时要求真实文件存在、findings 非重复和完成真值，不能以 Event 名称绕过产物门。
5. 原生学习 matched 实验增加 Episode→instance→project→candidate Event 的机制绑定门。学习观察继续 `production_effective=false`、不修改 ProjectState、不自批准 promotion；之后由真实项目执行结果验证是否改善。
6. 每次槽位重新授予时复位上一量子的失败/学习预算；最多双槽不变，`slot_quantum_project_steps=1` 时完成一个项目动作或一次“失败→学习→重试”后让槽。

## 真实性与边界

- 自进化不是 RL 的同义词：主动学习决定查什么问题；自进化形成并验证可回滚 Candidate；RL/后验更新只根据真实轨迹调整后续选择概率。
- 本 ADR 证明执行与学习应用路径已连接，不证明长期 RL 已成熟。长期成熟仍需要跨日期、多个真实项目轮次、匹配双臂、负样本和回滚统计。
- 03 当前是最小数值积分器实验，不冒充真实 Amber/GROMACS 生物体系。
- 01 不自动发布；Candidate 不自批生产；Hermes Skill 不取代 Partner Event 根基。

## 验收

- 本 ADR 当时全仓：`920 passed, 2 warnings`，0 failed，110.85 秒；最新基线与生产真实性收口见 ADR 0069 和 `docs/testing/last_pytest.txt`。
- 新增：五实例原生路由、跨项目标记拒绝、03 数值积分、05 源码/回归、native Episode 机制匹配测试。
- 生产验收必须另外证明：活动子进程不超过 2；至少一个真实 Event 获得项目 Receipt；失败能形成 Episode/学习观察并返回项目；完成量子后实际轮转到其他实例。

## 回滚

移除原生确定性路由分支即可恢复通用 planner；已有 Episode、学习观察、项目 Receipt 和失败记录保持 append-only，不删除、不重写。

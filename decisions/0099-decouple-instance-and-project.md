# ADR 0099：解除实例与项目绑定，实例降级为无差别 worker

状态：Accepted
决定日期：2026-09-12（用户批准，4 个待决问题决议如下）
日期：2026-09-12
前置：ADR 0060（instance-native runtime）、ADR 0065（project pipeline + scaffold）、
      ADR 0087（project-first application layer）、ADR 0095（Event Flow）、
      ADR 0096（inflight slot + orphan recovery）、ADR 0098（sprint36 cutover）
取代（部分）：ADR 0060 中"实例 = 长期项目承接者"假设、ADR 0087 中"Project = 长期对象、实例是专业角色"假设
触发：用户在 2026-09-12 明确要求"实例没有固定方向，这些只是用来测试的"

## 背景与动机

截至 ADR 0098，Partner 架构的核心假设是：

> 5 个实例各自承担一个固定长期项目（01=小红书、02=分子生成、03=分子动力学、
> 04=外部学习、05=自进化），实例 = 专业角色，项目 = 长期对象。

这个假设在过去两周（sprint30–36）主导了所有架构改动：

- `_PROJECT_MARKERS`（`application/service.py:31`）用关键词硬绑定消息到5 个项目
- `PROJECTS`（`governance/instance_native.py`）硬编码 instance_id → project_id
- `PROJECT_TO_INSTANCE` + `PROJECT_TITLES` 反向映射
- `active_project.txt` 写在每个实例工作目录下，作为实例"当前 focus"指针
- 调度时 `assigned_instance = PROJECT_TO_INSTANCE[selected_project]`
- 5 个实例的 `instance_native.py` 状态机假设一个 instance 同一时间只跑一个 project_id
- sprint36 ADR 0098 的 canary 9/9 节点验证的是"按项目归属跑 instance-native"

用户 2026-09-12 明确表示：

> 这些（5 个项目绑定）只是我现在用来测试的。每个实例没有固定方向。

也就是说——**5 instance × 5 fixed project 是一次性的测试配置，不是产品架构**。当前架构把测试配置固化成了核心假设，违背了用户意图。

ADR 0098 的 canary 9/9 + 561 passed 测试证明了"按 instance 绑 project 跑得通"，但这不等于"产品架构必须如此"。既然测试目的已经达到、现在要回到无固定方向的设计，5 个实例应该被还原为**无差别 worker pool**，实例 = 一个能执行任意 Event Flow 的进程，而不是一个只能跑某项目的"专业角色"。

## 决策

### 1. 实例与项目解绑

`PROJECTS` 字典从 `partner/governance/instance_native.py` 移除，instance 不再"拥有"任何项目。

实例只剩两个属性：
- `instance_id`（用于资源仲裁、delivery 路由、heartbeat 区分）
- `available_slots`（resource scheduler 用）

实例不知道、也不需要知道"当前绑了什么项目"。

### 2. 项目是动态的一等公民

项目从**配置驱动的固定集合**改为**运行时动态注册**：

- 已有 `partner/projects/project_registry.py` 已经是动态的（`register_project` / `release_project` / `keep_project_private`），保留并升级为主入口
- `share/projects/<project_id>/` 继续作为项目真值目录
- 5 个旧项目（xiaohongshu_operations / molecular_generation / molecular_dynamics_study / literature_github_learning / hermes_partner_explore）**作为普通项目保留在 registry 里**，仅作为"启动 seed 项目"，不与任何实例绑定
- 新方向由 `interaction.project_init`（sprint36 已实现）创建，自动进 registry

### 3. 入口分类改为四级优先级

`application/service.py:_project()` 的关键词扫描完全删除。新分类按以下顺序：

```
1. 显式 project_id      （用户原话带 [project_id=xxx] 或 attachment 携带）→ 直接命中
2. 会话上下文 project_id （同一会话上一轮用的 project_id，session-scoped）→ 直接命中
3. 项目注册表语义检索   （project_registry + brief 摘要做 embedding / keyword 检索）→ 命中 ≥ 阈值
4. LLM 判断新项目       （前三级都没命中，调用 LLM 输出 new_project_kind + project_hint）
                         → 调 interaction.project_init 建新项目 → 注册到 registry
                         → 下一轮 1/2/3 级就能命中
```

关键词硬匹配（`_PROJECT_MARKERS`）完全删除。

### 4. NEW_PROJECT 不再特判

`interaction.project_init` 从"特殊入口"降级为"普通操作"。任何时候三级分类失败、LLM 给出 new_project_kind，就走它。它和其他 Event 一样：注册 → scaffold → contract → 入库。

### 5. 调度按 slot / 负载，不按项目归属

`assigned_instance = PROJECT_TO_INSTANCE[selected_project]` 这一行删除。

新调度逻辑（`partner/governance/scheduler.py` + `runtime/event_worker.py`）：

```
next_instance = slot_arbiter.pick_idle_instance()    # 按 slot/负载选
job.assigned_instance = next_instance
```

slot arbiter 不知道 job 属于哪个项目，只看"哪个实例空闲 / 哪个 slot 没被占"。如果所有 instance 都在忙（达到 resource scheduler 上限），job 进 queue 等 slot 释放。

### 6. active_project.txt 改为 active_focus（语义弱化）

实例工作目录下仍然保留 `projects/active_focus.txt`，但语义从"绑定的项目"降级为"上一轮正在执行的项目（仅为缓存/审计）"。**不是路由依据**——路由看的是 Job.project_id，不是 active_focus。

新代码可以完全不写 active_focus.txt（不强制要求），仅在 worker 真在做某 project 的事时记录一次。

### 7. 旧 5 个项目作为普通项目保留

xiaohongshu_operations / molecular_generation / molecular_dynamics_study / literature_github_learning / hermes_partner_explore 在 migration step 完成：

- 从 `PROJECTS` 字典移除（实例不引用）
- 在 `share/projects/registry.json` 里保留为 `status=archived`（不再被自动路由，但保留历史 Receipt / brief / external_artifacts）
- 旧 Evidence（`share/evidence/molecular_generation/manual/<uuid>/` 等）原封不动保留
- 旧 active_plan.json（`instances/<id>/state/active_plan.json`）保留但改为 `legacy=true` 标记
- instance 的 active_project.txt / active_focus.txt 内容保留但不再被读

**历史记录 = 历史证据**，不删除（符合 AGENTS.md 的 "Preserve user changes and historical records"）。

### 8. Feature flag 分阶段切

```json
{
  "application": {
    "instance_project_binding": "legacy|new",     // 默认 legacy，分阶段切
    "semantic_retrieval_backend": "tfidf|embedding|none",  // 默认 none（仅 1+2+4 兜底）
    "new_project_fallback": "project_init|chat_reply|error"  // 默认 project_init
  }
}
```

`legacy`（默认）：保留现有 5 instance × 5 project 路由，不变。
`new`：全部走新分类 + 调度。

切到 `new` 后，可以一个 instance 一个 instance 地灰度（先 05、再 01/04、最后 02/03）。

## 实施阶段

### Phase 1：ADR + 迁移计划（已写完）

- 本 ADR + 单独的 migration_plan.md
- 影响范围盘点（下方"影响范围"）

### Phase 2：基础设施（并行，不影响生产）

不动现有路径，加新的：

1. `partner/projects/dynamic_project_registry.py`（或扩展现有 registry）：支持 embedding-based / tfidf-based 语义检索（Phase 3 才用，Phase 2 只建接口）
2. `partner/projects/session_context.py`：会话 → project_id 映射，TTL 24h
3. `partner/application/project_classifier.py`：四级分类实现，默认走 1+2+4，3 留 stub
4. `partner/governance/slot_arbiter.py`：从 instance_native 拆出来，按 slot/负载选 idle_instance

### Phase 3：分类器 + 调度切换

1. `application/service.py:_project()` 重写为调用 `project_classifier.classify()`
2. 删除 `_PROJECT_MARKERS`、`PROJECT_TO_INSTANCE`、`PROJECT_TITLES` 常量
3. `assigned_instance` 由 `slot_arbiter.pick_idle_instance()` 填充
4. 新增 4 级 fallback + LLM `new_project_kind` 触发 `interaction.project_init`
5. `instance_native.py` 里所有 `state.project_steps / project_steps_since_yield / slot_quantum_project_steps` 等"项目维度"字段降级为 advisory（仍记录但不参与路由）

### Phase 4：旧项目归档 + flag 切换

1. 旧 5 项目在 `share/projects/registry.json` 改为 `status=archived`
2. `instances/<id>/state/active_plan.json` 改为 `legacy=true`
3. `config/partner_config.json` 加 `application.instance_project_binding: "new"`
4. **灰度**：先 05，确认 24h 无回归 → 01/04 → 02/03
5. 任意阶段出问题：`application.instance_project_binding: "legacy"` 立即回滚

### Phase 5：清理与文档更新

1. 删除 `_PROJECT_MARKERS`、`PROJECT_TO_INSTANCE`、`PROJECT_TITLES` 代码（Phase 4 灰度 OK 后）
2. `docs/catalog.yaml` 更新架构文档入口
3. `docs/handoff/reading_order.md` 更新
4. 新增 ADR 0100（如果引入 embedding backend）

## 影响范围（盘点）

### 必须改的代码

- `partner/application/service.py`：`_project()` 重写、`assigned_instance` 计算改、`route` 加 `new_project_kind` 值
- `partner/governance/instance_native.py`：移除 `PROJECTS`、`state.project_steps` 降级、slot 仲裁拆出
- `partner/event_flows/builtins.py`：可选——`DIRECT_ANSWER` Flow 的 classify 节点可加项目分支
- `partner/events/interaction.py`：`project_init` 移除 special-case 注释
- `partner/state/config.py`：加 feature flag
- `partner/application/models.py`：`JobRecord.assigned_instance` 改为 advisory 字段

### 必须新建

- `partner/projects/session_context.py`
- `partner/projects/dynamic_project_registry.py`（或扩展现有）
- `partner/application/project_classifier.py`
- `partner/governance/slot_arbiter.py`
- `docs/decisions/0099-decouple-instance-and-project.md`（本文）
- `docs/decisions/migration_plan_0099.md`（具体步骤）
- `tests/test_project_classifier.py`
- `tests/test_slot_arbiter.py`

### 暂不改但需要新 ADR 后续处理

- ResourceScheduler（5 → 4 缩容逻辑）：与 instance_project_binding flag 解耦，单独 ADR
- project_loop.request_next_action：仍可能假设 instance 绑 project，单独 ADR 处理

### 影响的 ADR / Sprint

- **ADR 0060** 部分取代："实例原生 = 长期项目承接者" 假设改为"实例原生 = 无差别 worker pool，按 slot 仲裁"
- **ADR 0087** 部分取代："Project = 长期对象、实例是专业角色" 改为"Project = 长期对象、实例是 slot worker"
- **Sprint 36 / ADR 0098** canary：保留作为历史证据。Phase 4 灰度时用新的 canary 替代，不依赖 9/9 历史数字
- **Sprint 35 / ADR 0095** Event Flow 不动，Flow 与实例解耦本来就是设计意图

## 验收门

### Phase 1（本 ADR）
- ADR 与迁移计划文档完成 OK
- 影响范围盘点完成 OK
- 用户批准 ADR Proposed → Accepted

### Phase 2（基础设施）
- 4 个新模块各 ≥ 5 个测试
- 全仓回归通过（feature flag = legacy 时）

### Phase 3（分类器 + 调度）
- 旧 5 instance × 5 project 行为在 legacy flag 下完全不变
- 新分类器在 4 级 fallback 下行为正确
- 全仓回归通过

### Phase 4（灰度切换）
- 05 单实例切到 new flag 24h 无回归
- 01/04 灰度无回归
- 02/03 灰度无回归
- 期间任何 instance 出现 false-success、route 错配、queue 堆积 → 立即回滚 legacy

### Phase 5（清理）
- 删除临时模块 + flag 默认值改 new
- 文档同步更新
- 旧 5 项目在 registry 里 status=archived，brief/contract/Receipt 全保留
- 旧 PROJECTS 字典从 instance_native.py 移除

## 后果

### 短期

- 5 个实例可以并行接任何项目，"02 在做什么"这种问题变得没意义
- instance 健康监控从"哪个项目停了"变成"哪个 slot 没被填满"
- 跨项目接 Job 变成天然行为（步骤 2 之前的未竟事项自动实现）

### 中期

- 多项目并行推成为常态（不再需要"同项目串行"的硬保证）
- active_focus 不再是路由依据 → 多 project 并发时 worker 可在任何空闲 project 间切换
- slot 仲裁从"per-instance" 变成"per-slot"，实例只是 slot 的载体

### 长期

- 5 个固定实例可以动态伸缩（未来加 06/07 只需改 resource scheduler 上限）
- 用户发新方向不再被"实例有没有接" 阻塞——slot 空着就接，没有就排队
- "实例作为长期专业角色" 假设彻底拆掉，可以引入"实例 = 用户代理 / 任务代理"等新概念

## 不变量

- **历史不删除**：旧 5 项目的 brief / contract / Receipt / Evidence / External Sources 全保留
- **Event-first**：所有改动继续走 Event Flow，不引入新的 inbox 兼容层
- **资源仲裁权威**：slot 仲裁是 single source of truth，project_id 不参与 slot 分配
- **Failure-closed**：Phase 4 任何 instance 出问题立即回滚 legacy，不接受"切回去再说"
- **App 边界**：三端（GUI/TUI/QQ）继续通过 `PartnerApplicationService`，不直接接触 slot arbiter

## 拒绝的方案

### 方案 A：删除 `PROJECTS` 改成空字典

直接让 `PROJECTS = {}`，期望下游 `_project()` / `PROJECT_TO_INSTANCE` 自动 fall through 到 default。

拒绝原因：会让所有 `_project()` 调用方在没有默认值时报错或退化成 `None`——这不是"重构"，是"破坏"。当前项目通过 `_project()` 默认返回 `PROJECTS["04"][0]` 在 5 个 instance 上"总能跑"，删除会让 routing 完全乱掉，必须由新分类器显式接管。

### 方案 B：保留 `PROJECTS` 但加一个 `instance_id == None` 表示"无绑定"

让 instance 可以显式声明"我现在不绑项目"。但这等于"双模式"，同一份代码两种语义，长期看比"完全重构"更难维护。

### 方案 C：只改 `intent_classify` 让 LLM 决定，不动 `PROJECTS`

保持 5 instance × 5 project 硬绑定，只让 LLM 分类时多一个 `route == "new_project"` 选项。

拒绝原因：用户明确说"实例没有固定方向，这些只是测试配置"。保留 PROJECTS 硬绑定等于不承认测试配置是临时的，跟用户意图直接冲突。

## 用户决议（2026-09-12）

1. ADR 状态：Proposed → **Accepted**
2. Phase 2：**立即开始写** 4 个新模块（session_context / project_classifier / slot_arbiter + tfidf stub）
3. Phase 4 灰度顺序：**同意** 05 → 01/04 → 02/03 顺序
4. embedding backend：Phase 2 stub 先写 **`none`** 起步，Phase 3 跑通 LLM fallback 后再评估 tfidf vs embedding

附加决议：
- ADR 0060 / 0087 的 `Superseded by: 0099 (partially)` 注释**暂不加**，等 Phase 5 完成后再按流程加
- 不 commit（按用户要求保持 staged 状态，等用户给 commit message）


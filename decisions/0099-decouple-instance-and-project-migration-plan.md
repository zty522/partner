# Migration Plan：ADR 0099 实例-项目解绑

> 配套 ADR 0099。本文是**操作步骤**——按此文档逐步执行，每步有显式验收门。
> **更新 ADR 不在此处**——ADR 改动要用户批准，本文随时可改。

---

## 状态看板（每次执行更新）

- [ ] Phase 1：ADR + 迁移计划（本文） ✅
- [ ] Phase 2：基础设施（4 个新模块）
- [ ] Phase 3：分类器 + 调度切换
- [ ] Phase 4：旧项目归档 + flag 灰度切换
- [ ] Phase 5：清理与文档更新

每个 Phase 完成时，更新"已完成项"，并在 docs/handoff/ 留 changelog 链接。

---

## Phase 1：ADR + 迁移计划（已完成）

**目标**：用户看到完整方案，拍板。

**步骤**：
1. 写 ADR 0099 ✅
2. 写 migration_plan.md（本文） ✅
3. 影响范围盘点 ✅
4. 待用户回复 ADR 0099 末尾的4 个待决问题

**验收**：ADR 状态 Proposed → Accepted

**未做**：不动任何代码。

---

## Phase 2：基础设施（不破坏现有行为）

**目标**：新建 4 个模块 + feature flag，feature flag 默认 `legacy`，现有行为完全不变。

### 步骤

#### 2.1 feature flag 加进 config

文件：`partner/state/config.py`

```python
@dataclass
class ApplicationConfig:
    instance_project_binding: str = "legacy"  # "legacy" | "new"
    semantic_retrieval_backend: str = "none"  # "none" | "tfidf" | "embedding"
    new_project_fallback: str = "project_init"  # "project_init" | "chat_reply" | "error"
```

并且在 `config/partner_config.json` 加对应字段（保持默认 `legacy` / `none` / `project_init`）。

#### 2.2 新建 `partner/projects/session_context.py`

```python
class SessionContext:
    """ Maps conversation_id → project_id with TTL (default 24h).
    Backed by partner_workspace/state/application/session_context.jsonl.
    """
    def lookup(self, conversation_id: str) -> str | None: ...
    def bind(self, conversation_id: str, project_id: str) -> None: ...
    def evict_expired(self, ttl_seconds: int = 86400) -> int: ...
```

测试 ≥ 5：lookup miss / lookup hit / TTL 过期 / 多次 bind 同 conversation 取最新 / evict_expired 返回正确数。

#### 2.3 新建 `partner/application/project_classifier.py`

```python
class ProjectClassifier:
    """ 4-level classification:
      L1: explicit project_id in request
      L2: session context lookup
      L3: semantic retrieval (stub for now, returns [] if backend=none)
      L4: LLM fallback returning (kind, project_hint)
    Returns: ProjectDecision(kind, project_id, project_hint, source_level, confidence)
    """
    def __init__(self, registry, session_context, llm_adapter, config): ...
    def classify(self, request: str, conversation_id: str,
                 explicit_project_id: str = "") -> ProjectDecision: ...
```

测试 ≥ 5：
- 显式 project_id 优先
- 显式缺 + session 有 → 用 session
- 显式缺 + session 无 + LLM 返回 existing_kind → 用 registry 查
- 显式缺 + session 无 + LLM 返回 new_project_kind → 返回 new_project hint
- 全部失败 → fallback 到 project_init 或 chat_reply（按 config）

L3 stub：直接返回 `[]`，不调任何 embedding/tfidf。
L4 stub：调 `intent_synthesize` 的 LLM，加 `kind` enum 字段，默认返回 `existing_kind` + 最佳 project_hint（避免在 Phase 2 阶段就开始乱建新项目）。

#### 2.4 新建 `partner/governance/slot_arbiter.py`

```python
class SlotArbiter:
    """ Picks idle_instance from a pool, returns None if all slots full.
    Reads resource scheduler state, returns (instance_id, slot_id).
    """
    def __init__(self, workspace: str): ...
    def pick_idle_instance(self) -> tuple[str, str] | None: ...
    def report_instance_busy(self, instance_id: str, job_id: str) -> None: ...
    def report_instance_free(self, instance_id: str, job_id: str) -> None: ...
```

测试 ≥ 5：
- 全空 → 第一个 instance 被选中
- 部分忙 → 跳过忙的
- 全忙 → 返回 None
- report_busy 后 pick 不再选
- report_free 后又能选

**Phase 2 不做的事**：
- 不改 `application/service.py:_project()`，现有代码继续工作
- 不改 `instance_native.py`，旧 slot 仲裁逻辑保留
- 不改 `intent_synthesize`，prompt 不变
- 不删 `_PROJECT_MARKERS`、`PROJECTS` 等任何现有常量

**Phase 2 验收**：
- 全仓 `pytest` ≥ 561 passed（与 Phase 1 持平，证明零回归）
- 4 个新模块各 ≥ 5 个测试
- `instance_project_binding=legacy` 路由实机跑 02 一条消息仍命中 molecular_generation（行为不变）

---

## Phase 3：分类器 + 调度切换

**目标**：`feature flag = new` 时，`_project()` 改为调用 `ProjectClassifier`，`assigned_instance` 改为 `SlotArbiter.pick_idle_instance()`。

### 步骤

#### 3.1 改 `application/service.py:_project()`

旧签名：
```python
def _project(text, persona_hint="", explicit="") -> str
```

新实现：
```python
def _project(text, persona_hint="", explicit="", conversation_id="") -> str:
    if config.instance_project_binding == "new":
        decision = ProjectClassifier(...).classify(text, conversation_id, explicit)
        return decision.project_id or decision.project_hint
    # legacy: keep current _PROJECT_MARKERS logic
    ...
```

**保留旧路径**——legacy flag 下完全走原代码。

#### 3.2 改 `assigned_instance` 计算

旧：
```python
assigned = PROJECT_TO_INSTANCE[selected_project]
```

新：
```python
if config.instance_project_binding == "new":
    picked = SlotArbiter(...).pick_idle_instance()
    if picked is None:
        # all slots busy — push to queue (legacy behavior)
        assigned = ""  # let queue handle
    else:
        assigned = picked[0]
else:
    assigned = PROJECT_TO_INSTANCE[selected_project]  # legacy
```

#### 3.3 改 `route` enum

`intent_synthesize` prompt 加 `route` enum 值：
- `chat_reply` — 直接回答
- `enqueue_work` — 进项目队列（最常见）
- `new_project_kind` — 新方向，需要 `project_init`
- `modify_work` / `approval_response` / `cancel_or_pause`（已存在）

旧 `_route(text)` 函数（按关键词判 modify/cancel/approval）保留。

`service.py` 第 747 行 `flow_name` 计算：
```python
if route == "chat_reply":
    flow_name = "direct_answer"
elif route == "new_project_kind":
    flow_name = "new_project"
else:
    flow_name = "project_iteration"
```

**旧路径完全不变**——legacy flag 下 `flow_name = "direct_answer" if route == "chat_reply" else "project_iteration"` 仍是字面代码。

#### 3.4 `instance_native.py` 字段降级

`state.project_steps / project_steps_since_yield / slot_quantum_project_steps` 等项目维度字段从"参与路由"降为"advisory"——仍然写入但不再作为 slot yield 的硬门。

新逻辑（`legacy` 路径不变）：
```python
if config.instance_project_binding == "new":
    # ignore project_steps_since_yield, yield by queue depth + cpu/load only
    ...
else:
    # existing logic with slot_quantum_project_steps
    ...
```

#### 3.5 `intent_synthesize` prompt 升级

旧 prompt 已要求 `route` 字段。新 prompt 加 `kind` 字段（值：`existing_project | new_project_kind | chat_reply`），LLM 决定是已有项目 / 新项目 / 直接聊。

**注意**：升级 prompt 后 ALL 5 个实例的意图合成都会变化（因为 prompt 改了）。所以**必须在 Phase 3 末尾**才做 prompt 升级，否则 legacy 行为也会被破坏。

**Phase 3 验收**：
- legacy flag 下行为与 Phase 2 完全一致（`pytest` ≥ 561 passed）
- new flag 下：
  - 显式 project_id 优先
  - session lookup 工作
  - LLM 给出 existing_project 时匹配到正确项目
  - LLM 给出 new_project_kind 时触发 `interaction.project_init` 自动建目录
  - slot 仲裁正确选择空闲实例
- 灰度测试：把05 切到 new flag，单实例跑 1h 不出错

---

## Phase 4：旧项目归档 + flag 灰度切换

**目标**：5 个旧项目从 instance 解绑，flag 灰度切到 new。

### 步骤

#### 4.1 旧项目改 status=archived

```python
from partner.projects.project_registry import register_project

for old_project in ["xiaohongshu_operations", "molecular_generation",
                     "molecular_dynamics_study", "literature_github_learning",
                     "hermes_partner_explore"]:
    register_project(workspace, old_project, status="archived",
                     reason="ADR 0099: archived old test seed, retained for history")
```

`share/projects/registry.json` 里这5 个项目 `status` 字段改为 `archived`，其余不动。

#### 4.2 旧 active_plan.json 加 legacy=true

```python
for inst in ["01", "02", "03", "04", "05"]:
    ap_path = f"instances/{inst}/state/active_plan.json"
    data = json.loads(open(ap_path).read())
    data["legacy"] = True
    data["legacy_reason"] = "ADR 0099: instance-project binding retired"
    open(ap_path, "w").write(json.dumps(data, indent=2))
```

不删 active_plan.json 内容（保留作为历史证据）。

#### 4.3 灰度切 flag

**Step A**：只切 05 到 new flag。

`config/partner_config.json`：
```json
"application": {
  "instance_project_binding": "new",
  "instance_native_":": {
    "instance_id": "05"  // only 05 uses new path
  }
}
```

`systemctl --user restart partner-instance-native.service`（instance 进程会重新加载 config）

观察 24h：
- 05 是否还正常接收消息？
- 05 是否被分配到非 hermes_partner_explore 项目？
- 05 是否会因为 slot 仲裁被频繁切换？

如无问题：
**Step B**：切 01/04 到 new flag。
观察 24h：
- 01（小红书）是否还能继续 xiaohongshu_operations？
- 04（外部学习）是否还能继续 literature_github_learning？

如无问题：
**Step C**：切 02/03 到 new flag。
观察 24h：
- 02（分子）是否还能继续 molecular_generation？还是被分到别的实例？
- 03（分子动力学）同上

**任意阶段出问题**：`application.instance_project_binding: "legacy"` 立即回滚。

#### 4.4 灰度期同步观察

每个灰度阶段需要看的指标：
- 5 instance 的 heartbeat 是否正常
- QQ 是否有 ack 失败
- share/projects/registry.json 是否被大量创建新项目（防止 LLM 误判成 new_project_kind 太多）
- share/projects/ 下的 5 个旧项目目录是否还偶尔被访问（brief.md mtime）
- Application Outbound 是否有堆积

**Phase 4 验收**：
- 5 instance 全切到 new flag 后 24h 无回归
- 5 个旧项目 brief 仍可读、Receipt 链路完整
- 旧 5 项目仍能被"显式 project_id"或"session lookup"路由到（不破坏用户历史证据可访问性）
- 全仓回归 ≥ 561 passed

---

## Phase 5：清理与文档更新

**目标**：删除旧路径，更新文档，flag 默认值改 new。

### 步骤

#### 5.1 删代码

- 删除 `_PROJECT_MARKERS`、`PROJECT_TO_INSTANCE`、`PROJECT_TITLES` 常量
- 删除 `_project()` 函数的 legacy 分支
- 删除 `instance_native.py` 中 PROJECTS 字典
- 删除 `intent_synthesize` 旧 prompt 的 `route` enum（保留新 prompt 的 `kind` enum）

#### 5.2 feature flag 默认值改 new

```json
"application": {
  "instance_project_binding": "new"
}
```

但保留 config schema 让用户可临时切回 legacy（写 "legacy" 立即回滚）。

#### 5.3 文档更新

- `docs/catalog.yaml`：更新架构入口
- `docs/handoff/reading_order.md`：移除"按实例绑项目"的描述
- `docs/architecture/instance_native_runtime.md`：标记 deprecated，新增 `slot_arbiter_runtime.md`
- `docs/current_status.md`：ADR 0099 摘要

#### 5.4 ADR 更新

- 0099 状态：Proposed → Implemented
- 0060 / 0087 加 `Superseded by: 0099 (partially)` 注释

**Phase 5 验收**：
- 全仓回归 ≥ 561 passed
- legacy 代码全部删除
- docs/ 同步更新
- 旧 5 项目仍可读 / 不被自动路由

---

## 回滚预案

任意 Phase 失败时：

| Phase | 回滚动作 | 风险 |
|---|---|---|
| Phase 2 | 删除新增的 4 个模块，flag 默认值保持 legacy | 低：根本没接入 |
| Phase 3 | flag 全切 legacy，删 Phase 3 改的代码 | 中：可能要 revert 一些 commit |
| Phase 4 | flag 全切 legacy | 低：旧路径仍然可用 |
| Phase 5 | flag 切 legacy，Phase 5.1 删除的代码从 git 恢复 | 中：Phase 5 已删代码，恢复成本高 |

Phase 4 之前任意点回滚成本 < 30 分钟。
Phase 5 回滚成本 = 代码 review + 部分代码重建，约 1-2 小时。

---

## 时间估算

| Phase | 工作量 | 风险 | 估计耗时 |
|---|---|---|---|
| Phase 1 | ADR + 计划 | 0 | ✅ 已完成 |
| Phase 2 | 4 模块 + 测试 | 低 | 1-2 天 |
| Phase 3 | 分类器 + 调度切换 | 中 | 2-3 天 |
| Phase 4 | 灰度切换 | 中（用户验收门多） | 3-4 天（含 24h×3 观察窗口） |
| Phase 5 | 清理 + 文档 | 低 | 0.5-1 天 |

**总计**：约 7-10 天可完成全链路。Phase 3/4 是关键路径，Phase 2 可以并行启动。

---

## 不在本计划范围

这些事项**不在本次 migration 范围**——单独 ADR 处理：

1. **embedding backend 选型**（tfidf vs sentence-transformer vs API call）：需要单独 ADR（候选 ADR 0100），等 Phase 3 跑通 LLM fallback 后再决定
2. **ResourceScheduler 重构**：把 5→4 缩容逻辑从 instance 解耦，需要单独 ADR
3. **`project_loop.request_next_action`**：仍可能假设 instance 绑 project 的地方，需要单独 ADR
4. **`dialog_history` 的 project_hint 字段**：旧的 hint 字符串格式与新分类器的 hint 格式可能不一致
5. **GUI/TUI 的项目工作台**：旧 UI 可能假设 5 个固定项目
6. **Campaign 调度**（sprint17 ADR 0054）：如果启用 Campaign，需要让它也走新分类器

---

## 沟通要点（跟用户对齐用）

1. **本计划不动生产**：Phase 2 / 3 全程 legacy flag 默认，新代码默认不接入
2. **历史不删**：5 个旧项目只是改 status=archived，brief/Receipt/Evidence 全部保留
3. **可逆**：任意 Phase 切回 legacy flag 立即回到今天的状态
4. **灰度而非一刀切**：Phase 4 一个一个 instance 切，每个 instance 观察 24h
5. **5 个固定 instance 保留**：架构反转不是说"以后不要 5 个 instance"，是说"5 个 instance 不再绑固定项目"。未来加 06/07 只需改 resource scheduler 上限，不改任何业务逻辑。

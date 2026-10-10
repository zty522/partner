# Partner Mind Lab (L4: 认知与学习)

L4 负责 partner **知道什么、学到什么**。所有"长在脑子里"的内容在这里统一管理。

## 组成（逻辑归属）

| 组件 | 物理位置 | 说明 |
|---|---|---|
| **统一笔记库 Mind Notes** | `partner/mind_lab/notes.py` | 唯一笔记账本 `share/mind/notes/notes.jsonl`；类型注册表 NOTE_TYPES |
| **笔记四事件** | `partner/events/notes.py` | `notes.recall`（召回+注入）/ `notes.judge`（落地/记笔记/丢弃）/ `notes.promote`（升级/关闭）/ `notes.evolution_sync`（进化决策同步） |
| **LLM 统一注入** | `partner/events/_llm.py` | `call_model` 顶部自动注入【长期笔记参考】段 |
| 主动学习 | `partner/events/active_learning.py`、`local_learning.py` | 外部资料读取 → borrowable_cards → synthesize → judge |
| 事件记忆 | `partner/memory/event_memory.py` | observations/lessons/preferences/habits/beliefs/growth，存储已并入笔记库 |
| 世界模型/JEV | `partner/world_model/`、`partner/events/core.py` | 外部世界模型与 JEV 判定通道 |
| 候选注册表 | `partner/mind_lab/registry.py` | 已具备 vs 蓝图（8 个预留位 EVENT.md 登记为 instantiate-on-evidence） |
| 预留蓝图 | `partner/events/`（cross-pollination 等 8 个 EVENT.md） | 只读蓝图，由笔记/自进化证据驱动实例化 |

## 笔记如何被"用起来"（不是占位）

1. **写入**：`notes.judge` 在 content_read_reply / active_learning / learning_improvement_cycle
   的 synthesize 之后自动聚合提炼点（borrowable_cards / findings / local_ideas），
   LLM 三分类：action_now（直接落地）/ record_note（记笔记+pending）/ dismiss。
2. **读取**：`notes.recall` 在主要 flow 启动时召回相关笔记 + open pending，
   渲染成【长期笔记参考】段注入运行上下文；`_llm.call_model` 对**每一次** LLM 调用自动附加。
3. **更新**：`notes.promote` 在证据充分时升级（pending→resolved、habit→active、
   growth→confirmed），无证据保持 open；用户发的每条资料/每个运行问题都先记录、再判断。

## 使用纪律

- 笔记 ≠ 已验证事实：使用前核对 `evidence_refs`。
- 没想清楚的先记 pending（给 gap + trigger_signal），不硬改不硬激活。
- 新 L4 代码一律落 `partner/mind_lab/`，禁止散落新文件。
- 预留位蓝图不默认启用：证据充分时由笔记/自进化驱动实例化（见 `registry.py`）。

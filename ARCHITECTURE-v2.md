# ARCHITECTURE v2 — Partner 重构设计

## 核心理念

```
    ┌─────────────────────────────────────────────────────────────────┐
    │                  SHELL LAYER (纯视图)                           │
    │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
    │  │  CLI (cli.py) │  │  Windows GUI │  │  QQ Bot              │  │
    │  │  命令行交互    │  │  (gui.py)    │  │  (qq_official_bridge) │  │
    │  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
    │         │                  │                     │              │
    │         └──────────────────┼─────────────────────┘              │
    │                            │ 都只读写 state/ 目录下的 JSON      │
    │                            ▼                                     │
    ├─────────────────────────────────────────────────────────────────┤
    │                   STATE LAYER (单一事实源)                       │
    │  ┌────────────┐ ┌────────────┐ ┌──────────┐ ┌──────────────┐  │
    │  │ active_plan│ │ event_bus  │ │ knowledge│ │ journal.jsonl│  │
    │  │ .json      │ │ .jsonl     │ │ .json    │ │               │  │
    │  └────────────┘ └────────────┘ └──────────┘ └──────────────┘  │
    │         ▲              ▲              ▲              ▲          │
    ├─────────┼──────────────┼──────────────┼──────────────┼─────────┤
    │         │              │              │              │         │
    │  ┌──────┴──────────────┴──────────────┴──────────────┴──────┐  │
    │  │               ENGINE LAYER (cron驱动)                     │  │
    │  │                                                          │  │
    │  │  Partner-research skill (Hermes cron, 每15分钟)           │  │
    │  │  5-branch decision tree:                                 │  │
    │  │    A: 有active plan → 执行当前phase                       │  │
    │  │    B: plan完成 → 复盘→文献搜索→新计划/PushEvent           │  │
    │  │    C: 空闲+队列有任务 → 自动接续                          │  │
    │  │    D: 完全空闲 → 知识扫描→生成新假设                      │  │
    │  │    E: Event Bus有PushEvent → 立即推送                    │  │
    │  │                                                          │  │
    │  │  每次心跳后:                                              │  │
    │  │    1. 自检(轻量3项)                                       │  │
    │  │    2. 推QQ报告                                            │  │
    │  │    3. 更新heartbeat                                       │  │
    │  └──────────────────────────────────────────────────────────┘  │
    └─────────────────────────────────────────────────────────────────┘
```

**关键变化**：删掉了 `core.py`（旧编排器）、`event_engine.py`、`event.py`（旧Event体系）。所有逻辑下沉到 Hermes cron 驱动的 `partner-research` skill 中执行，Python 只保留状态读写/UI shell。

---

## 1. Event Bus 设计 (新)

**文件**：`state/event_bus.jsonl`（追加日志，非覆写）

每条事件记录：

```json
{"id": "ev_1748382910", "type": "push", "subtype": "breakthrough",
 "title": "MAE 下降 8%！新策略生效",
 "body": "Round 15: stable_select(tv=0.5,tc=0.3) 的 PLS 集成在 Test 上 MAE=7.10，相比基线 7.73 下降 8.1%",
 "priority": 9,
 "created_at": "2026-05-28T00:15:00",
 "pushed": false,
 "push_target": "qq"}
```

**Event Type 体系**：

| type | subtype | 触发条件 | push 目标 |
|------|---------|----------|-----------|
| `result` | `breakthrough` | 实验指标提升 > 5% | QQ |
| `result` | `stuck` | 连续 3 次实验无进展 | QQ |
| `result` | `completed` | 项目达到自然停止点 | QQ |
| `self_check` | `contradiction` | 知识库发现冲突 | QQ |
| `self_check` | `staleness` | 知识超过 14 天未刷新 | 仅日志 |
| `self_check` | `leak_warning` | 检测到数据泄漏 | QQ |
| `heartbeat` | `alive` | 正常心跳 | QQ（轻量） |
| `heartbeat` | `warning` | 环境异常（bot断连等） | QQ |
| `evolution` | `pattern_learned` | 发现新策略模式 | 仅日志 |

---

## 2. 主动推送机制

**不再只靠 cron 轮询**，而是通过 Event Bus 触发的半实时推送：

```
cron (每15分钟)
  │
  ├─ [Research] 执行 active_plan 的当前 phase
  │   └─ 如果有重大发现 → 写入 PushEvent 到 event_bus.jsonl
  │
  ├─ [Push] 扫描 event_bus.jsonl 中 pushed=false 的事件
  │   ├─ priority >= 8 → 立即推送到 QQ
  │   ├─ priority 5-7 → 累积到下次心跳推送
  │   └─ priority < 5 → 仅记入日志
  │
  ├─ [Self-Check] 轻量自检
  │   ├─ 知识冲突检测：相同主题不同置信度？
  │   ├─ 卡死检测：当前 phase 超过 2h？
  │   └─ 心跳连续性检测：上次心跳正常？
  │
  ├─ [Report] 推 QQ 心跳报告
  │   └─ 格式：纯文本，含当前状态+最新事件摘要
  │
  └─ [Log] 更新 heartbeat.json
```

**频率升级**：从 30 分钟→15 分钟，确保更及时的反应。

---

## 3. 自检设计（轻量，每次心跳做）

**三步自检**（总耗时 <10 秒）：

### 3.1 知识冲突检测
- 扫描 knowledge.json 中 title 相似（Levenshtein < 3 字符差异）的条目
- 如果置信度差异 > 0.3 → 产生 `self_check/contradiction` 事件
- 如果完全相同 title 但 content 矛盾 → 产生事件

### 3.2 卡死检测
- 检查 active_plan 中当前 phase 的 `started_at`
- 如果 > 2h 未完成 → 产生 `result/stuck` 事件
- 同时尝试：缩小 scope / 跳过该 phase / 标记已完成

### 3.3 代码泄漏检测
- 检查实验脚本中 `GroupKFold` 和 `ComBat`/`age_aware_correction` 的调用位置
- 如果校正代码在 CV 循环外部 → 产生 `self_check/leak_warning` 事件

---

## 4. 自进化设计（每次计划完成后做）

**删除旧版 603 行 self_evolution.py**，替换为轻量 `self_check.py`：

```python
def after_plan_completed(plan, journal, knowledge):
    """计划完成后执行自进化"""
    # 1. 策略复盘
    recent_entries = journal.get_recent(5)
    pattern = analyze_patterns(recent_entries)
    # → 输出到 state/learned_patterns.json
    
    # 2. 知识整理
    archive_old_knowledge(knowledge, days=30)
    merge_duplicates(knowledge)
    
    # 3. 生成下一步建议
    next_action = suggest_next(plan, pattern)
    # → 写入 queue 或直接创建新 plan
```

---

## 5. 文件结构清理（重构后）

```
partner/
├── __init__.py          # 版本号
├── __main__.py          # python -m partner 入口
├── cli.py               # CLI 界面 (命令解析 & 打印)
├── gui.py               # Windows tkinter GUI
├── config.py            # 配置读取/写入
├── setup.py             # 安装向导
├── state.py             # 状态读写 (heartbeat, stats)
├── active_plan.py       # Plan 管理 (读写 active_plan.json)
├── event_bus.py         # 新增: Event Bus 读写
├── self_check.py        # 新增: 自检逻辑 (替换self_evolution.py)
├── knowledge.py         # 知识库读写
├── journal.py           # 日志追加
├── task_queue.py        # 任务队列
├── qq_official_bridge.py  # QQ Bot 桥接 (唯一消息推送通道)
├── qq_official_bot.py     # QQ Bot 底层 WebSocket
├── napcat_bridge.py        # 🟡 保留但标记 deprecated (仅兼容旧配置)
├── napcat_onebot.py        # 🟡 同上
├── adapter.py              # 🟡 简化: 只保留 HermesAdapter
├── conversation.py         # 🟡 保留用于 QQ 对话(简化)
├── router.py               # 🟡 保留(简化)
├── response_generator.py   # 🟡 保留(简化)
├── dialog_history.py       # 🟡 保留
├── context.py              # 🟡 保留
├── user_prefs.py           # 🟡 保留
├── proactive_notifier.py   # 🟡 保留(但只保留推QQ的核心路径)
├── workspace_manager.py    # 🟡 保留
├── wsl_bridge.py           # 🟡 保留(sdk.py 依赖)
├── voice.py                # ❌ 删除(从未使用)
customer.py            # ❌ 删除(从未使用)
```

最终目标：~20 个文件，~8,000 行代码。

---

## 6. CLI 与 GUI 的统一

**CLI (cli.py)**：精简到只做命令路由，不内联逻辑

```python
partner setup          → setup.interactive_setup()
partner status         → state_manager.read() + active_plan.read() + display()
partner bot start qq   → qq_bridge.start()
partner bot stop qq    → qq_bridge.stop()
partner queue          → task_queue.manage()
partner update         → git_pull() + pip_install()
partner config set     → config.set()
```

**GUI (gui.py)**：同上，调同一组 API

**共享模块 (可提取为 partner.api 或直接走 parse + subprocess)**
- `find_workspace()` — 统一实现
- `load_state()` — 读取所有 state JSON
- `start_bot()` / `stop_bot()` — 统一实现

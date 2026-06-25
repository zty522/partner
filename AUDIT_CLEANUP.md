# Partner Codebase Audit: Dead & Duplicate Code Report

Generated: 2026-06-17
Scope: /mnt/e/work/partner/

## 1. mind/scheduler.py — DEAD
| Metric | Value |
|--------|-------|
| Size | 549 bytes / 15 lines |
| Last modified | 2026-06-14 |
| Imports (into) | **0** — no file imports this |
| Imports (from) | logging |
| Content | Marked "Deprecated — MindPool has been removed." Contains no-op `mind_loop()` stub. |
| **Recommendation** | **DELETE** — fully dead, no callers |

## 2. qq_official_bridge.py (root) vs qq_bot/qq_official_bridge.py — DUPLICATE
### Root: `partner/qq_official_bridge.py`
| Metric | Value |
|--------|-------|
| Size | 62,513 bytes / 1,458 lines |
| Last modified | 2026-06-17 00:30 |
| Imports (into) | **1** (self-referencing: `from partner.qq_official_bridge import QQQfficialBridge` — circular) |
| Diff from qq_bot version | 115 lines differ |

### Active: `partner/qq_bot/qq_official_bridge.py`
| Metric | Value |
|--------|-------|
| Size | 62,205 bytes / 1,461 lines |
| Last modified | 2026-06-15 22:29 |
| Imports (into) | **2** (`__main__.py` line 56, `desktop_gui/gui_qt.py` references) |
| Notes | Uses absolute imports (`partner.qq_bot.qq_official_bot`). Has `_append_qq_chat_history()` method. |

**Verdict:** Root file is an older copy. Active file is `qq_bot/qq_official_bridge.py`.
| **Recommendation** | **DELETE** root `partner/qq_official_bridge.py` — it's the stale copy |

## 3. desktop_gui/ — ACTIVE
| Metric | Value |
|--------|-------|
| Size | 636 KB total |
| Files | `gui.py` (1,923 lines, Tkinter), `gui_qt.py` (12,336 lines, Qt), `__init__.py` |
| Last modified | 2026-06-16 |
| Imports (into) | Referenced in `__main__.py`, `mind/executor.py`, `cli.py` (via `gui.py` reference to `gui_qt`) |
| **Recommendation** | **KEEP** — actively used by desktop GUI mode |

## 4. gui_qt.py — ACTIVE (but too large)
| Metric | Value |
|--------|-------|
| Size | 558,688 bytes / 12,336 lines |
| Last modified | 2026-06-16 19:01 |
| Imports (into) | 1 (`desktop_gui/gui.py` line 1913) |
| Verdict | Real Qt desktop app — dashboard, chat, QQ bot control, instance management. Not dead, just monolithic. |
| **Recommendation** | **KEEP** but **REFACTOR** — split into modules (~5-6 files) |

## 5. events/ subdirs — ACTIVE (config templates)
| Metric | Value |
|--------|-------|
| Size | 32 KB / 546 lines total across 8 EVENT.md files |
| Content | YAML frontmatter event type definitions (phases, inputs, triggers) for the harness engine |
| Subdirs | cross-pollination, deep-analysis, exploration, idea-exploration, literature-deep-dive, method-learning, project-health-check, synthesis-review |
| **Recommendation** | **KEEP** — event type configuration for the event-driven engine |

## 6. projects/ — LEGACY USER DATA
| Metric | Value |
|--------|-------|
| Size | 0 (disk empty) — contains `.legacy_seeded` marker + metadata files |
| Content | One Chinese-named legacy project (`整理目前基于转录组测序预测年龄...`) seeded from old layout |
| Referenced in | `project_state.py` reads `.legacy_seeded` marker |
| **Recommendation** | **KEEP** — contains user project data, not safe to delete automatically |

## 7. docs/ — KEEP
| Metric | Value |
|--------|-------|
| Size | 88 KB, 13 files |
| Content | ARCHITECTURE.md (v2), AUDIT_REPORT.md, release notes (v0.4–0.7), CONTRIBUTING.md, SOUL.md, HARNESS_ARCHITECTURE.md, index.html |
| **Recommendation** | **KEEP** — documentation is useful and maintained |

## 8. scripts/ — RELEVANT
| Metric | Value |
|--------|-------|
| Size | 88 KB, 12 files |
| Files | `install.sh`/`install.ps1` (distribution), `start_three_partners.sh` (ops), `bot_startup.sh`, `partner-skill.sh`, `partner_ollama_reverse_tunnel.sh`, `release.sh`, `normalize_partner_workspace.py`, `run_workspace_maint.py`, `send_qq_report.py`, `uninstall.sh` |
| **Recommendation** | **KEEP** — operational and distribution scripts |

## 9. mind/executor.py — MONOLITHIC (needs refactoring)
| Metric | Value |
|--------|-------|
| Size | 446,299 bytes / 9,373 lines / **206 function definitions** + 1 class |
| Last modified | 2026-06-16 23:49 |
| Functions | All seem to be used internally within the file — the `execute_event()` dispatcher calls handler functions like `_handle_user_message`, `_handle_batch_plan_event`, `_handle_action_event`, etc. |
| Classes | `_NoopPool` (deprecated MindPool stub) |
| Dead items | `_NoopPool` class + `ensure_pool()` — both marked "MindPool removed, no-op stub" |
| **Recommendation** | **KEEP** but **REFACTOR** — split into modules: event_handlers.py, email.py, batch_check.py, reports.py, research.py, etc. Monolithic file is maintenance risk. |

## 10. task_router.py — ACTIVE
| Metric | Value |
|--------|-------|
| Size | 5,080 bytes / 132 lines |
| Last modified | 2026-06-13 |
| Imports (into) | **2** (`interaction_orchestrator.py` line 1248, `router.py` line 37) |
| Content | `route()` and `classify()` functions for routing tasks |
| **Recommendation** | **KEEP** — actively used |

## 11. ADDITIONAL DEAD FILES FOUND

### utils/message_filter.py — DEAD (duplicate)
| Metric | Value |
|--------|-------|
| Size | 48 lines / 1,761 bytes |
| Imported? | **No** — no file imports it |
| Duplicate of | `llm/message_filter.py` (156 lines, actively imported by `mind/executor.py`) |
| **Recommendation** | **DELETE** — superseded by `llm/message_filter.py` |

### utils/summary_generator.py — DEAD
| Metric | Value |
|--------|-------|
| Size | 27 lines / 973 bytes |
| Imported? | **No** — no file imports it |
| Duplicate of | `llm/summary_generator.py` (140 lines, same purpose, also NOT imported) |
| **Recommendation** | **DELETE** — not imported anywhere. `llm/summary_generator.py` also has no importers. |

### llm/summary_generator.py — DEAD
| Metric | Value |
|--------|-------|
| Size | 140 lines / 4,810 bytes |
| Imported? | **No** — zero import references |
| **Recommendation** | **DELETE** (or keep if planned for future use) |

### scheduler_aps.py — DEAD
| Metric | Value |
|--------|-------|
| Size | 124 lines / 5 KB |
| Imported? | **No** — zero import references |
| Content | APScheduler-based scheduling — appears to be abandoned in favor of cron tick handling in executor.py |
| **Recommendation** | **DELETE** — no callers |

### router.py — MOSTLY DEAD (stub)
| Metric | Value |
|--------|-------|
| Size | 164 lines / 6,119 bytes |
| Content | `ConversationRouter` stub class + duplicated route()/classify() functions from task_router.py |
| Status | `ConversationRouter` stub preserves old interface for `conversation.py`. Most of the file is duplicated code. |
| **Recommendation** | **REFACTOR** — strip to only the backward-compat stub (remove duplicated route/classify functions) |

### migrate_to_multi.py — MIGRATION TOOL (possibly one-time)
| Metric | Value |
|--------|-------|
| Size | 198 lines / 7,387 bytes |
| Used | Run directly as `python -m partner.migrate_to_multi` |
| **Recommendation** | **KEEP** (migration tools should stay until all workspaces migrated) |

### migrate_workspace.py — MIGRATION TOOL (possibly one-time)
| Metric | Value |
|--------|-------|
| Size | 590 lines |
| Used | Run directly as `python -m partner.migrate_workspace` |
| Duplicate function | `migrate_workspace()` also exists in `workspace_manager.py` |
| **Recommendation** | **KEEP** (migration tools), but deduplicate with `workspace_manager.py` |

## 12. EMPTY SCAFFOLDING DIRECTORIES

| Directory | Content | Recommendation |
|-----------|---------|---------------|
| `partner/dialogue/` | Empty | **DELETE** — no files |
| `partner/system/` | Empty | **DELETE** — no files |
| `partner/state/` | Empty | **DELETE** — no files |
| `partner/decision/` | `__init__.py` only (1-line docstring) | **DELETE** — empty scaffold |

## 13. ACTIVE DIRECTORIES (keep as-is)

| Directory | Size | Notes |
|-----------|------|-------|
| `mind/` | 1.3 MB | Core engine — executor.py, event_types.py, harness.py, scheduler.py (dead) |
| `skills/` | 201 KB | Skill center, registry, discovery |
| `harness_core/` | 97 KB | Task execution harness |
| `qq_bot/` | 240 KB | QQ bot bridge (active) |
| `msg_queue/` | 16 KB | Message dispatcher + broker |
| `planner/` | 60 KB | Batch planner + prompt builder |
| `curiosity/` | 12 KB | Curiosity engine |
| `project/` | 8 KB | Project registry |
| `memory/` | 8 KB | Memory manager |
| `meta/` | 104 KB | Learning module |
| `locales/` | 8 KB | i18n (en.json, zh.json) |
| `assets/` | 128 KB | UI icons (SVG) |
| `utils/` | 36 KB | Utility modules (2 dead files noted above) |
| `llm/` | 40 KB | LLM utilities (1 dead file noted above) |
| `prompts/` | 33 KB (root) + 4 KB (subdir) | Prompt templates (active) |

## SUMMARY: ACTION PLAN

### DELETE (safe to remove immediately):
1. `partner/mind/scheduler.py` — dead deprecation stub
2. `partner/qq_official_bridge.py` — stale copy (active is `qq_bot/qq_official_bridge.py`)
3. `partner/utils/message_filter.py` — superseded by `llm/message_filter.py`
4. `partner/utils/summary_generator.py` — unused
5. `partner/llm/summary_generator.py` — unused
6. `partner/scheduler_aps.py` — unused
7. `partner/dialogue/` — empty directory
8. `partner/system/` — empty directory
9. `partner/state/` — empty directory
10. `partner/decision/__init__.py` — empty scaffold

### REFACTOR (keep but restructure):
1. `mind/executor.py` (9,373 lines) — split into modules
2. `desktop_gui/gui_qt.py` (12,336 lines) — split into modules
3. `partner/router.py` — strip to backward-compat stub, remove duplicated code
4. `partner/setup.py` (2,441 lines) — consider splitting

### KEEP (actively used):
- `desktop_gui/`, `task_router.py`, `events/`, `projects/`, `docs/`, `scripts/`
- All migration scripts (until migrations completed)
- All active directories listed above

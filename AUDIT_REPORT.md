# 🔍 Partner Project — Code Audit Report

**Date:** 2026-05-23  
**Auditor:** Hermes Agent (automated)  
**Scope:** Full code review, security check, test run, compatibility check

---

## Summary

| Category         | Issues Found |
|------------------|:------------:|
| 🔴 Bugs          | 4            |
| 🟡 Security      | 1            |
| 🔵 Code Quality  | 8            |
| 🟢 Tests         | ✅ All pass  |
| 🟠 Package/Config| 5            |
| 🔴 README        | 3            |
| 🟡 Compatibility | 2            |
| **Total**        | **23**       |

---

## 1. 🔴 Bugs

### BUG-1: Fragile variable-scope check using `dir()` — core.py:113

**File:** `partner/core.py`, line 113  
**Severity:** High  
**Code:**
```python
return result if 'result' in dir() else str(e)
```
**Problem:** Uses `dir()` to check whether the `result` variable was assigned in the `try` block. While this happens to work on CPython 3.13, it relies on implementation details of how `dir()` reports local variables. On alternative Python implementations (PyPy, GraalPy) or different CPython versions, behavior may differ. The `e` variable in the `except` block also has the same scoping issue — if the `except` block itself fails (e.g., `self.journal.log()` throws), neither `result` nor `e` is in scope, causing an unhandled `NameError`.

**Fix:** Use a flag variable:
```python
result = None
try:
    result = self._execute_task(task)
    ...
except Exception as e:
    ...
return result if result is not None else str(e)
```

---

### BUG-2: Config format mismatch between setup.py and PartnerConfig.load() — setup.py:399 vs config.py:50

**File:** `partner/setup.py`, lines 399–407 vs `partner/config.py`, lines 49–58  
**Severity:** High  
**Problem:** `setup.py` saves config as a **flat dict**:
```json
{"workspace": "/home/user/partner_workspace", "backend": "hermes", ...}
```
But `PartnerConfig.load()` expects a **nested dict**:
```json
{"workspace": {"path": "/home/user/partner_workspace", "readonly_dirs": []},
 "agent": {"backend": "hermes", ...}, ...}
```
Loading the setup.py output with `PartnerConfig.load()` raises `TypeError: argument after ** must be a mapping, not str`.

**Impact:** The two config systems are completely incompatible. The CLI's `cmd_status` works around this by reading raw JSON directly, but any code using `PartnerConfig.load()` on a setup-created config will crash.

**Fix:** Either make `setup.py` save in the `PartnerConfig` format, or add a migration/fallback in `PartnerConfig.load()`.

---

### BUG-3: Misleading "partner chat" message — core.py:64

**File:** `partner/core.py`, line 64  
**Severity:** Medium  
**Code:**
```python
print("✅ Partner is running. Use 'partner chat' to talk to me.")
```
**Problem:** No `partner chat` subcommand exists in `cli.py`. The CLI only has `setup`, `status`, and a default action. This message is misleading to users.

**Fix:** Change to: `"✅ Partner is running. Say 'partner 最近在研究什么？' to interact."`

---

### BUG-4: `SchedulerConfig.max_tasks_per_cycle` and `heartbeat_timeout_minutes` never used — config.py:33-34

**File:** `partner/config.py`, lines 33–34; `partner/core.py`  
**Severity:** Low  
**Problem:** `max_tasks_per_cycle` is defined but `run_cycle()` always processes exactly 1 task. `heartbeat_timeout_minutes` is defined but `is_alive()` always uses a hardcoded default or caller-supplied value. These config values are dead configuration.

---

## 2. 🟡 Security

### SEC-1: Script injection in hermes_adapter.py — hermes_adapter.py:132-142

**File:** `partner/hermes_adapter.py`, lines 132–142  
**Severity:** Medium  
**Code:**
```python
script = f'''
import json
import sys
sys.path.insert(0, "{self.workspace}")
prompt = """{full_prompt}"""
print(prompt)
'''
```
**Problem:** `full_prompt` includes user-controlled `task.title` and `task.description`. If a task title contains `"""`, the Python script string breaks, potentially allowing code injection. While this is an internal tool, it's a defense-in-depth concern.

**Fix:** Use `json.dumps()` or `shlex.quote()` to safely embed the prompt.

---

### SEC-2: No hardcoded secrets found ✅

No API keys, passwords, or tokens were found hardcoded in any source files.

---

## 3. 🔵 Code Quality

### QUAL-1: Duplicate HermesAdapter classes (dead code) — hermes_adapter.py

**File:** `partner/hermes_adapter.py` (206 lines)  
**Severity:** Medium  
**Problem:** There are **two** `HermesAdapter` classes:
1. `adapter.py:51` — stub implementation (used by `create_adapter()`)
2. `hermes_adapter.py:16` — full implementation (NEVER imported by any module)

The full `hermes_adapter.py` is completely dead code. No file imports from it.

**Fix:** Either integrate `hermes_adapter.py` into `adapter.py`'s `create_adapter()` factory, or remove it.

---

### QUAL-2: Unused imports (22 instances across 12 files)

| File | Line | Unused Import |
|------|------|---------------|
| `partner/cli.py` | 12 | `sys` |
| `partner/cli.py` | 13 | `Path` |
| `partner/cli.py` | 18 | `_json` (redundant — `json` already imported on line 10) |
| `partner/cli.py` | 48 | `detect_hermes`, `detect_claude_code` |
| `partner/core.py` | 10 | `KnowledgeEntry` |
| `partner/core.py` | 13 | `AgentAdapter` |
| `partner/config.py` | 5 | `Path` |
| `partner/router.py` | 18 | `timedelta` |
| `partner/conversation.py` | 9 | `timedelta` |
| `partner/conversation.py` | 10 | `Optional` |
| `partner/adapter.py` | 5 | `Optional` |
| `partner/adapter.py` | 75 | `tempfile` |
| `partner/hermes_adapter.py` | 10 | `tempfile` |
| `partner/hermes_adapter.py` | 13 | `ExecutionResult` |
| `partner/hermes_adapter.py` | 115 | `write_file` |
| `partner/journal.py` | 6 | `Tuple` |
| `partner/knowledge.py` | 7 | `Optional` |
| `partner/state.py` | 5 | `shutil` |
| `partner/setup.py` | 5 | `shutil` |
| `partner/setup.py` | 7 | `sys` |
| `tests/test_router.py` | 9 | `ParsedQuery` |

---

### QUAL-3: Bare `except:` clauses (6 instances)

**Severity:** Medium  
Bare `except:` catches `KeyboardInterrupt`, `SystemExit`, and `GeneratorExit`, which is almost always wrong.

| File | Line | Context |
|------|------|---------|
| `partner/setup.py` | 145 | `detect_hermes()` — `which hermes` |
| `partner/setup.py` | 163 | `detect_hermes()` — config file read |
| `partner/setup.py` | 196 | `detect_claude_code()` — `which claude` |
| `partner/setup.py` | 214 | `detect_codex()` — `which codex` |
| `run_cycle.py` | 204 | `do_project_scan()` — `terminal("ls")` |
| `run_cycle.py` | 230 | `do_knowledge_synthesis()` — json load |

**Fix:** Replace all `except:` with `except Exception:`.

---

### QUAL-4: Hardcoded domain-specific keywords in router.py:62

**File:** `partner/router.py`, line 62  
**Severity:** Low  
**Code:**
```python
(r"(知道|了解).+?[关于]?(扩散|VAE|scGPT|年龄|衰老|AMP|抗菌肽|鲍曼|因果|批次校正|XGBoost)", Intent.KNOWLEDGE, 0.9, None),
```
**Problem:** Contains hardcoded research domain terms (aging, antimicrobial peptides, scGPT, etc.) that only make sense for a specific research group. This makes the router non-portable.

---

### QUAL-5: `run_cycle.py` not integrated into package — run_cycle.py:23

**File:** `run_cycle.py`, line 23  
**Severity:** Low  
**Code:**
```python
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
```
**Problem:** Uses `sys.path` manipulation instead of proper package imports. The `run_cycle.py` script lives at the project root but isn't included in `pyproject.toml` as a console script or entry point. It's a standalone script that duplicates some functionality from `core.py`.

---

### QUAL-6: `get_workspace()` returns `None` but typed as `-> str` — cli.py:16,43

**File:** `partner/cli.py`, line 16 (signature) and line 43 (return)  
**Severity:** Low  
**Code:**
```python
def get_workspace() -> str:
    ...
    return None
```
**Problem:** Return type annotation says `str` but `None` is returned. Should be `-> Optional[str]`.

---

### QUAL-7: SchedulerConfig values not enforced — config.py:32-34

**File:** `partner/config.py`, lines 32–34  
**Severity:** Low  
`interval_minutes`, `max_tasks_per_cycle`, and `heartbeat_timeout_minutes` are defined but never actually enforced in the code. The research loop and task limits are not implemented.

---

### QUAL-8: `__pycache__` and `.egg-info` in git — project root

**Severity:** Low  
The `.gitignore` lists `__pycache__/` and `*.egg-info/` but these directories appear to have been committed. The gitignore patterns should prevent future additions, but the existing ones should be removed from tracking.

---

## 4. 🟢 Test Coverage

### Test Results

```
tests/test_basic.py:  ✅ All tests passed (9 assertions)
tests/test_router.py: ✅ All tests passed (20/20 intent, 5/5 topic, 5/5 routing)
```

### Coverage Gaps

| Component | Tested? | Notes |
|-----------|:-------:|-------|
| `config.py` | ✅ | save/load tested |
| `task_queue.py` | ✅ | add/get_next/complete tested |
| `knowledge.py` | ✅ | add/search tested |
| `journal.py` | ✅ | log/get_recent tested |
| `state.py` | ⚠️ | heartbeat tested, but StateManager initialized with wrong path (workspace instead of workspace/state) |
| `conversation.py` | ✅ | status/help tested |
| `core.py` | ⚠️ | start/status/chat tested, but `run_cycle()` never tested |
| `router.py` | ✅ | Comprehensive intent/topic/routing tests |
| `cli.py` | ❌ | No CLI tests |
| `setup.py` | ❌ | No setup wizard tests |
| `adapter.py` | ❌ | No adapter tests |
| `hermes_adapter.py` | ❌ | Dead code, no tests |
| `run_cycle.py` | ❌ | No tests |

---

## 5. 🟠 Package Structure

### PKG-1: Missing `tests/__init__.py`

The `tests/` directory has no `__init__.py`. Tests use `sys.path.insert()` workaround. Not critical but prevents `pytest` auto-discovery.

### PKG-2: Stale egg-info committed to git

The `partner_research.egg-info/` directory is committed but the `.gitignore` lists `*.egg-info/`. The PKG-INFO inside contains an **older, very different version** of the README that describes commands (`partner init`, `partner start`, `partner chat`, `partner task list`, etc.) that don't exist in the current code.

### PKG-3: Placeholder URLs in pyproject.toml — pyproject.toml:32-35

```toml
Homepage = "https://github.com/YOUR_USERNAME/partner"
Repository = "https://github.com/YOUR_USERNAME/partner"
```
These are template placeholders. The actual repo appears to be `https://github.com/zty522/partner`.

### PKG-4: `run_cycle.py` not registered as entry point

`run_cycle.py` is a standalone script at the project root. It's not registered in `pyproject.toml` as a console script. Users would need to run `python run_cycle.py --workspace PATH` manually.

### PKG-5: No dependencies listed — pyproject.toml

```toml
# No [project.dependencies] section
```
The project has zero declared dependencies, which is correct since it only uses stdlib modules. However, the optional `hermes_tools` dependency should be documented (perhaps as an optional/extras dependency).

---

## 6. 🔴 README Accuracy

### README-1: License mismatch (CRITICAL)

| Source | License |
|--------|---------|
| `pyproject.toml` line 10 | **MIT** |
| `LICENSE` file | **Apache 2.0** |
| `README.md` badge (line 9) | **Apache 2.0** |
| `README.md` text (line 178) | **Apache 2.0** |
| PKG-INFO (egg-info) | **MIT** |

**This is a legal inconsistency.** The `pyproject.toml` must match the `LICENSE` file. Fix `pyproject.toml` to say Apache 2.0.

### README-2: Commands section doesn't match actual CLI — README.md:155-162

README shows:
```bash
partner              # Guide to start talking
partner setup        # First-time configuration
partner status       # Check Partner status
partner setup --status  # Quick status check
```

But the stale PKG-INFO in egg-info describes a completely different command set:
```bash
partner init                    # Initialize workspace
partner start                   # Start Partner
partner chat                    # Interactive conversation
partner task list               # View task queue
partner knowledge search "query" # Search knowledge
partner run                     # Run one research cycle
```

The current README is accurate for the actual code. The egg-info PKG-INFO is stale.

### README-3: "Quick Start" uses `git clone` but package is pip-installable

README shows `git clone` + `pip install -e .` which is correct for development, but the egg-info PKG-INFO says `pip install partner-research` (the PyPI package name), which may not be published.

---

## 7. 🟡 Compatibility

### COMPAT-1: `datetime.fromisoformat()` — state.py:63

**File:** `partner/state.py`, line 63  
**Code:**
```python
last = datetime.fromisoformat(hb.last_heartbeat)
```
**Problem:** `datetime.fromisoformat()` in Python 3.9–3.10 doesn't handle all ISO 8601 formats (e.g., `Z` suffix, certain timezone formats). Fixed in Python 3.11+. Since `heartbeat()` uses `datetime.now().isoformat()` (no timezone), this works in practice, but the code would break if external code wrote a timezone-aware timestamp.

**Fix:** For Python 3.9 compatibility, either strip timezone info before parsing or use `datetime.fromisoformat(hb.last_heartbeat.replace("Z", "+00:00"))`.

### COMPAT-2: Python 3.13 not listed in classifiers — pyproject.toml:16-26

The classifiers list Python 3.9–3.12 but the code was tested with Python 3.13. Add `"Programming Language :: Python :: 3.13"`.

---

## Appendix: Files Reviewed

| File | Lines | Status |
|------|-------|--------|
| `pyproject.toml` | 38 | Issues found |
| `README.md` | 188 | Issues found |
| `partner/__init__.py` | 12 | Clean |
| `partner/cli.py` | 191 | Issues found |
| `partner/core.py` | 168 | Issues found |
| `partner/config.py` | 58 | Issues found |
| `partner/router.py` | 395 | Issues found |
| `partner/conversation.py` | 72 | Issues found |
| `partner/adapter.py` | 152 | Issues found |
| `partner/hermes_adapter.py` | 205 | Dead code |
| `partner/journal.py` | 90 | Clean |
| `partner/knowledge.py` | 89 | Clean |
| `partner/state.py` | 157 | Minor issue |
| `partner/task_queue.py` | 104 | Clean |
| `partner/setup.py` | 530 | Issues found |
| `run_cycle.py` | 326 | Issues found |
| `tests/test_basic.py` | 104 | Pass |
| `tests/test_router.py` | 224 | Pass |
| `LICENSE` | 189 | Apache 2.0 (correct) |
| `.gitignore` | 14 | Clean |

---

## Priority Recommendations

1. **P0 (Fix now):** License mismatch in `pyproject.toml` (MIT vs Apache 2.0)
2. **P0 (Fix now):** Config format mismatch between `setup.py` and `PartnerConfig.load()`
3. **P1 (Fix soon):** Replace `dir()` hack in `core.py:113` with proper flag
4. **P1 (Fix soon):** Remove or integrate dead `hermes_adapter.py`
5. **P1 (Fix soon):** Replace bare `except:` with `except Exception:`
6. **P2 (Clean up):** Remove unused imports across all files
7. **P2 (Clean up):** Update placeholder URLs in `pyproject.toml`
8. **P2 (Clean up):** Fix stale egg-info PKG-INFO
9. **P3 (Enhancement):** Add tests for CLI, setup, adapters, run_cycle
10. **P3 (Enhancement):** Add `tests/__init__.py`

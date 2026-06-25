"""Research guardrails and visible mind state for long-running Partner projects.

This module keeps project execution honest without making prompts heavier:
- baseline/metric contracts for comparability checks
- completion and shortcut gates
- report priority classification
- visible journey/growth/reflection artifacts for users
- lightweight mind-state files inspired by working/episodic/semantic memory

The implementation is deliberately generic. It does not special-case one
instance; it detects risky patterns and records reusable habits.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clip(text: str, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "_", name or "project").strip("_") or "project"


def _read_text(path: str, max_chars: int = 12000) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read(max_chars)
    except Exception:
        return ""


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")


def _append_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")


def _load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception:
        return default


def _load_recent_jsonl(path: str, limit: int = 5) -> list[dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            rows = [line.strip() for line in f if line.strip()][-limit:]
        out = []
        for row in rows:
            data = json.loads(row)
            if isinstance(data, dict):
                out.append(data)
        return out
    except Exception:
        return []


def _write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _project_dir(workspace: str, project: str) -> str:
    from .project_state import get_project_dir

    return get_project_dir(workspace, project)


def _user_dir(workspace: str) -> str:
    path = os.path.join(workspace, "state", "user")
    os.makedirs(path, exist_ok=True)
    return path


def _mind_dir(workspace: str) -> str:
    path = os.path.join(workspace, "system", "mind")
    os.makedirs(path, exist_ok=True)
    return path


def _workspace_root(workspace: str) -> str:
    """Return the multi-instance workspace root when called from an instance dir."""
    norm = os.path.normpath(workspace)
    if os.path.basename(os.path.dirname(norm)) == "instances":
        return os.path.dirname(os.path.dirname(norm))
    return norm


def _global_mind_dir(workspace: str) -> str:
    path = os.path.join(_mind_dir(workspace), "global")
    os.makedirs(path, exist_ok=True)
    return path


def _shared_mind_dir(workspace: str) -> str:
    path = os.path.join(_workspace_root(workspace), "shared_mind", "system")
    os.makedirs(path, exist_ok=True)
    return path


def _shared_user_dir(workspace: str) -> str:
    path = os.path.join(_workspace_root(workspace), "shared_mind", "user")
    os.makedirs(path, exist_ok=True)
    return path


def _global_user_dir(workspace: str) -> str:
    path = os.path.join(_user_dir(workspace), "partner_mind")
    os.makedirs(path, exist_ok=True)
    return path


def _project_mind_dir(workspace: str, project: str) -> str:
    path = os.path.join(_mind_dir(workspace), "projects", _safe_name(project))
    os.makedirs(path, exist_ok=True)
    return path


def _project_user_dir(workspace: str, project: str) -> str:
    path = os.path.join(_user_dir(workspace), "projects", _safe_name(project))
    os.makedirs(path, exist_ok=True)
    return path


def ensure_mind_files(workspace: str, project: str) -> None:
    """Create user-visible and system-visible mind files."""
    ensure_global_mind_files(workspace)
    mind = _project_mind_dir(workspace, project)
    user_project = _project_user_dir(workspace, project)
    defaults = {
        os.path.join(mind, "working_memory.md"): f"# {project} Working Memory\n",
        os.path.join(mind, "semantic_memory.md"): f"# {project} Semantic Memory\n",
        os.path.join(mind, "habits.json"): json.dumps({"habits": []}, ensure_ascii=False, indent=2),
        os.path.join(mind, "blockers.json"): json.dumps({"blockers": []}, ensure_ascii=False, indent=2),
        os.path.join(mind, "progress_score.jsonl"): "",
        os.path.join(mind, "episodes.jsonl"): "",
        os.path.join(user_project, "research_journey.md"): f"# {project} Research Journey\n\n",
        os.path.join(user_project, "growth_journal.md"): f"# {project} Growth Journal\n\n",
        os.path.join(user_project, "habit_applications.md"): f"# {project} Habit Applications\n\n",
        os.path.join(user_project, "reflection_log.md"): f"# {project} Reflection Log\n\n",
        os.path.join(user_project, "breakthroughs.md"): f"# {project} Breakthroughs\n\n",
        os.path.join(user_project, "insight_log.md"): f"# {project} Insight Log\n\n",
        os.path.join(user_project, "mind_status.md"): f"# {project} Mind Status\n\n",
    }
    for path, content in defaults.items():
        if not os.path.exists(path):
            if path.endswith(".json"):
                _write_text(path, content)
            else:
                _write_text(path, content)


def ensure_global_mind_files(workspace: str) -> None:
    """Create Partner-level memory files shared by all instances/projects."""
    ensure_shared_mind_files(workspace)
    mind = _global_mind_dir(workspace)
    user = _global_user_dir(workspace)
    defaults = {
        os.path.join(mind, "habits.json"): json.dumps({"habits": []}, ensure_ascii=False, indent=2),
        os.path.join(mind, "episodes.jsonl"): "",
        os.path.join(mind, "semantic_memory.md"): "# Partner Semantic Memory\n\n",
        os.path.join(mind, "progress_score.jsonl"): "",
        os.path.join(user, "partner_growth_journal.md"): "# Partner Growth Journal\n\n",
        os.path.join(user, "cross_project_habits.md"): "# Cross-project Habits\n\n",
        os.path.join(user, "partner_reflection_log.md"): "# Partner Reflection Log\n\n",
        os.path.join(user, "partner_mind_status.md"): "# Partner Mind Status\n\n",
        os.path.join(user, "partner_evolution.md"): "# Partner Evolution\n\n",
        os.path.join(user, "transferable_lessons.md"): "# Transferable Lessons\n\n",
    }
    for path, content in defaults.items():
        if not os.path.exists(path):
            _write_text(path, content)


def ensure_shared_mind_files(workspace: str) -> None:
    """Create workspace-level Partner memory shared across all instances."""
    mind = _shared_mind_dir(workspace)
    user = _shared_user_dir(workspace)
    defaults = {
        os.path.join(mind, "habits.json"): json.dumps({"habits": []}, ensure_ascii=False, indent=2),
        os.path.join(mind, "episodes.jsonl"): "",
        os.path.join(mind, "semantic_memory.md"): "# Shared Partner Semantic Memory\n\n",
        os.path.join(mind, "resource_policy.md"): (
            "# Resource Access Policy\n\n"
            "- 不绕过登录、验证码、反爬或平台访问限制。\n"
            "- API key、账号、预算、真实数据、源目录缺失时，记录 blocker 并向用户说明需要什么。\n"
            "- 用户暂未回复时，切换到公开替代来源、baseline 设计、证据审计、方法预案等无阻塞分支。\n"
            "- 用户分享的外部内容先分为项目指令、项目参考、普通学习、访问受限；普通学习不改变项目主线。\n"
        ),
        os.path.join(user, "shared_partner_mind_status.md"): "# Shared Partner Mind Status\n\n",
        os.path.join(user, "shared_cross_instance_habits.md"): "# Shared Cross-instance Habits\n\n",
        os.path.join(user, "shared_growth_journal.md"): "# Shared Partner Growth Journal\n\n",
        os.path.join(user, "shared_partner_evolution.md"): "# Shared Partner Evolution\n\n",
    }
    for path, content in defaults.items():
        if not os.path.exists(path):
            _write_text(path, content)


def baseline_contract_path(workspace: str, project: str) -> str:
    return os.path.join(_project_mind_dir(workspace, project), "baseline_contract.json")


def metric_contract_path(workspace: str, project: str) -> str:
    return os.path.join(_project_mind_dir(workspace, project), "metric_contract.json")


def load_baseline_contract(workspace: str, project: str) -> dict[str, Any]:
    path = baseline_contract_path(workspace, project)
    data = _load_json(path, {})
    return data if isinstance(data, dict) else {}


def load_metric_contract(workspace: str, project: str) -> dict[str, Any]:
    path = metric_contract_path(workspace, project)
    data = _load_json(path, {})
    return data if isinstance(data, dict) else {}


def _infer_metric_contract(project: str, text: str) -> dict[str, Any]:
    metrics: list[dict[str, Any]] = []
    for metric in re.findall(r"\b(?:MAE|MSE|RMSE|R2|R²|AUC|ACC|F1|precision|recall|score|rate|ratio)\b", text or "", re.I):
        name = metric.upper().replace("R²", "R2")
        metrics.append({
            "name": name,
            "unit": "unknown",
            "direction": "must_be_defined_by_project",
            "comparable_only_when": "same data, same target, same split/evaluation protocol",
            "proxy": False,
        })
    unique = []
    seen = set()
    for item in metrics:
        key = item["name"]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return {"project": project, "updated_at": _now(), "metrics": unique}


def ensure_baseline_and_metric_contracts(workspace: str, project: str) -> None:
    """Create baseline/metric contracts if missing, using local project text only."""
    ensure_mind_files(workspace, project)
    pdir = _project_dir(workspace, project)
    source_texts = []
    for name in (
        "project_brief.md",
        "state.md",
        "current_best_result.md",
        "FINAL_REPORT.md",
        "FINAL_COMPREHENSIVE_REPORT.md",
        "README.md",
    ):
        source_texts.append(_read_text(os.path.join(pdir, name), 4000))
    text = "\n".join(source_texts)

    bpath = baseline_contract_path(workspace, project)
    if not os.path.exists(bpath):
        numbers = re.findall(r"\b(?:metric|score|rate|ratio|accuracy|acc|auc|f1|mae|mse|rmse|r²|r2|完成率)[^。\n]{0,80}", text, re.I)
        baseline = {
            "project": project,
            "created_at": _now(),
            "source": "local_project_files",
            "task_definition": _clip(_first_nonempty_section(text, ["项目目标", "目标", "当前主线"]) or project, 300),
            "data_scope": _clip(_first_matching_line(text, r"(数据|dataset|source|split|protocol|合成|真实|benchmark)") or "", 300),
            "best_known_result": _clip("；".join(numbers[:5]), 500),
            "allowed_comparisons": [
                "same task",
                "same data or explicitly marked as synthetic/method probe",
                "same evaluation protocol",
                "same metric unit",
            ],
            "notes": "Auto-generated from local files. User or agent should refine when project baseline is known.",
        }
        _write_json(bpath, baseline)

    mpath = metric_contract_path(workspace, project)
    if not os.path.exists(mpath):
        _write_json(mpath, _infer_metric_contract(project, text))


def _first_nonempty_section(text: str, headings: list[str]) -> str:
    for heading in headings:
        match = re.search(rf"##?\s*{re.escape(heading)}\s*\n(?P<body>.*?)(?=\n##|\Z)", text, re.S)
        if match:
            body = re.sub(r"\s+", " ", match.group("body")).strip()
            if body and "待补充" not in body:
                return body
    return ""


def _first_matching_line(text: str, pattern: str) -> str:
    for line in text.splitlines():
        if re.search(pattern, line, re.I):
            return line.strip()
    return ""


def _is_abstract_habit(item: dict[str, Any]) -> bool:
    """Keep transferable habits, not concrete cross-project case details."""
    text = " ".join(str(item.get(k) or "") for k in ("cue", "routine", "check", "source"))
    if not text.strip():
        return False
    if re.search(r"\b(?:v\d+|task[-_ ]?\d+)\b|[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+", text, re.I):
        return False
    if len(re.findall(r"\d+(?:\.\d+)?", text)) >= 3:
        return False
    return True


def _ensure_core_shared_habits(workspace: str) -> None:
    """Ensure generic shared habits exist without logging growth every round."""
    ensure_shared_mind_files(workspace)
    path = os.path.join(_shared_mind_dir(workspace), "habits.json")
    data = _load_json(path, {"habits": []})
    habits = data.get("habits") if isinstance(data, dict) else []
    if not isinstance(habits, list):
        habits = []
    cores = [
        {
            "cue": "准备汇报最佳结果/突破/完成结论",
            "routine": "先检查证据文件、运行日志和最小复现是否存在；证据不足只能标为待复核，不继续包装或调参",
            "check": "用户汇报前必须能指向真实证据路径",
        },
        {
            "cue": "新项目或资料依赖任务起步",
            "routine": "由 LLM 判断是否需要先查文献/资料/公开数据路线；需要时先做起步卡，不需要时直接做最小可验证动作",
            "check": "不机械搜索，也不跳过必要背景核验",
        },
        {
            "cue": "容易获取的材料与用户原始意图不一致",
            "routine": "回到用户问题本身；易获取材料只能作旁证，不能替代用户要求",
            "check": "输出 verified / inferred / hypothesis 分层",
        },
    ]
    existing = {
        (str(item.get("cue") or ""), str(item.get("routine") or ""))
        for item in habits
        if isinstance(item, dict)
    }
    changed = False
    for core in cores:
        key = (core["cue"], core["routine"])
        if key in existing:
            continue
        habits.append({
            "created_at": _now(),
            "last_seen_at": _now(),
            "cue": core["cue"],
            "routine": core["routine"],
            "check": core["check"],
            "source": "core_policy",
            "projects": [],
            "instances": [],
        })
        changed = True
    if changed:
        _write_json(path, {"habits": habits[-120:]})


def build_mind_context(workspace: str, project: str) -> str:
    """Small prompt context: only the most relevant contracts and habits."""
    ensure_baseline_and_metric_contracts(workspace, project)
    ensure_global_mind_files(workspace)
    _ensure_core_shared_habits(workspace)
    baseline = load_baseline_contract(workspace, project)
    metric = load_metric_contract(workspace, project)
    habits = _load_json(os.path.join(_project_mind_dir(workspace, project), "habits.json"), {"habits": []})
    global_habits = _load_json(os.path.join(_global_mind_dir(workspace), "habits.json"), {"habits": []})
    shared_habits = _load_json(os.path.join(_shared_mind_dir(workspace), "habits.json"), {"habits": []})
    habit_items = habits.get("habits") if isinstance(habits, dict) else []
    global_habit_items = global_habits.get("habits") if isinstance(global_habits, dict) else []
    shared_habit_items = shared_habits.get("habits") if isinstance(shared_habits, dict) else []
    habit_lines = []
    for item in (habit_items or [])[-3:]:
        if isinstance(item, dict):
            habit_lines.append(
                f"- cue={_clip(str(item.get('cue') or ''), 70)}; routine={_clip(str(item.get('routine') or ''), 110)}"
            )
    global_habit_lines = []
    for item in (global_habit_items or [])[-4:]:
        if isinstance(item, dict):
            global_habit_lines.append(
                f"- cue={_clip(str(item.get('cue') or ''), 70)}; routine={_clip(str(item.get('routine') or ''), 110)}"
            )
    shared_habit_lines = []
    for item in reversed(shared_habit_items or []):
        if isinstance(item, dict) and _is_abstract_habit(item):
            shared_habit_lines.append(
                f"- cue={_clip(str(item.get('cue') or ''), 70)}; routine={_clip(str(item.get('routine') or ''), 120)}"
            )
        if len(shared_habit_lines) >= 6:
            break
    shared_habit_lines = list(reversed(shared_habit_lines))
    metric_lines = []
    for item in (metric.get("metrics") or [])[:6]:
        metric_lines.append(
            f"- {item.get('name')} [{item.get('unit')}]: {item.get('comparable_only_when')}"
        )
    parts = [
        "类脑/科研习惯上下文（只读，必须影响行动）：",
        f"- baseline task: {_clip(str(baseline.get('task_definition') or ''), 180)}",
        f"- best known result: {_clip(str(baseline.get('best_known_result') or ''), 180)}",
    ]
    if metric_lines:
        parts.append("指标契约：\n" + "\n".join(metric_lines))
    if shared_habit_lines:
        parts.append("Partner 共享习惯（所有实例复用）：\n" + "\n".join(shared_habit_lines))
    if global_habit_lines:
        parts.append("Partner 跨项目习惯：\n" + "\n".join(global_habit_lines))
    if habit_lines:
        parts.append("当前习惯：\n" + "\n".join(habit_lines))
    parts.append("行动前必须检查：是否同任务/同数据/同评估协议/同指标单位；simulation、proxy、synthetic 不能写成真实最佳。")
    return "\n".join(parts)


def _add_habit(workspace: str, project: str, cue: str, routine: str, check: str, source: str) -> None:
    ensure_mind_files(workspace, project)
    _add_global_habit(workspace, cue, routine, check, source, project)
    path = os.path.join(_project_mind_dir(workspace, project), "habits.json")
    data = _load_json(path, {"habits": []})
    habits = data.get("habits") if isinstance(data, dict) else []
    key = (cue.strip(), routine.strip())
    for item in habits:
        if isinstance(item, dict) and (item.get("cue"), item.get("routine")) == key:
            item["last_seen_at"] = _now()
            _write_json(path, {"habits": habits})
            return
    habits.append({
        "created_at": _now(),
        "cue": cue,
        "routine": routine,
        "check": check,
        "source": source,
        "applied_count": 0,
    })
    _write_json(path, {"habits": habits[-50:]})
    _append_user_growth(
        workspace,
        project,
        f"## {_now()} | 新习惯\n\n- 触发：{cue}\n- 动作：{routine}\n- 检查：{check}\n- 来源：{source}\n",
    )


def _add_global_habit(workspace: str, cue: str, routine: str, check: str, source: str, project: str) -> None:
    ensure_global_mind_files(workspace)
    _add_shared_habit(workspace, cue, routine, check, source, project)
    path = os.path.join(_global_mind_dir(workspace), "habits.json")
    data = _load_json(path, {"habits": []})
    habits = data.get("habits") if isinstance(data, dict) else []
    key = (cue.strip(), routine.strip())
    for item in habits:
        if isinstance(item, dict) and (item.get("cue"), item.get("routine")) == key:
            item["last_seen_at"] = _now()
            projects = item.setdefault("projects", [])
            if project not in projects:
                projects.append(project)
            _write_json(path, {"habits": habits})
            _publish_global_mind_status(workspace)
            return
    habits.append({
        "created_at": _now(),
        "last_seen_at": _now(),
        "cue": cue,
        "routine": routine,
        "check": check,
        "source": source,
        "projects": [project],
    })
    _write_json(path, {"habits": habits[-80:]})
    block = (
        f"\n## {_now()} | 跨项目新习惯\n\n"
        f"- 来源项目：{project}\n"
        f"- 触发：{cue}\n"
        f"- 动作：{routine}\n"
        f"- 检查：{check}\n"
        f"- 来源：{source}\n"
    )
    _append_text(os.path.join(_global_user_dir(workspace), "partner_growth_journal.md"), block)
    _append_text(os.path.join(_global_user_dir(workspace), "cross_project_habits.md"), block)
    _publish_global_mind_status(workspace)


def _add_shared_habit(workspace: str, cue: str, routine: str, check: str, source: str, project: str) -> None:
    ensure_shared_mind_files(workspace)
    path = os.path.join(_shared_mind_dir(workspace), "habits.json")
    data = _load_json(path, {"habits": []})
    habits = data.get("habits") if isinstance(data, dict) else []
    instance_id = os.path.basename(os.path.normpath(workspace))
    key = (cue.strip(), routine.strip())
    for item in habits:
        if isinstance(item, dict) and (item.get("cue"), item.get("routine")) == key:
            item["last_seen_at"] = _now()
            projects = item.setdefault("projects", [])
            if project and project not in projects:
                projects.append(project)
            instances = item.setdefault("instances", [])
            if instance_id and instance_id not in instances:
                instances.append(instance_id)
            _write_json(path, {"habits": habits[-120:]})
            _publish_shared_mind_status(workspace)
            return
    habits.append({
        "created_at": _now(),
        "last_seen_at": _now(),
        "cue": cue,
        "routine": routine,
        "check": check,
        "source": source,
        "projects": [project] if project else [],
        "instances": [instance_id] if instance_id else [],
    })
    _write_json(path, {"habits": habits[-120:]})
    block = (
        f"\n## {_now()} | 共享新习惯\n\n"
        f"- 实例：{instance_id or 'unknown'}\n"
        f"- 来源项目：{project}\n"
        f"- 触发：{cue}\n"
        f"- 动作：{routine}\n"
        f"- 检查：{check}\n"
        f"- 来源：{source}\n"
    )
    _append_text(os.path.join(_shared_user_dir(workspace), "shared_growth_journal.md"), block)
    _append_text(os.path.join(_shared_user_dir(workspace), "shared_cross_instance_habits.md"), block)
    _publish_shared_mind_status(workspace)


def _record_habit_application(workspace: str, project: str, cue: str, original: str, actual: str, result: str) -> None:
    ensure_mind_files(workspace, project)
    now = _now()
    key_src = f"{cue}|{actual}|{result}"
    key = re.sub(r"\s+", " ", key_src.strip().lower())[:260]
    mind_path = os.path.join(_project_mind_dir(workspace, project), "habit_applications.json")
    data = _load_json(mind_path, {"items": []})
    items = data.get("items") if isinstance(data, dict) else []
    if not isinstance(items, list):
        items = []
    found = None
    for item in items:
        if isinstance(item, dict) and item.get("key") == key:
            found = item
            break
    if found:
        found["count"] = int(found.get("count") or 1) + 1
        found["last_seen_at"] = now
        found["last_result"] = _clip(result, 260)
    else:
        items.append({
            "key": key,
            "first_seen_at": now,
            "last_seen_at": now,
            "count": 1,
            "cue": _clip(cue, 180),
            "original": _clip(original, 220),
            "actual": _clip(actual, 260),
            "last_result": _clip(result, 260),
        })
    _write_json(mind_path, {"items": items[-80:]})
    path = os.path.join(_project_user_dir(workspace, project), "habit_applications.md")
    lines = [
        f"# {project} Habit Applications",
        "",
        "同一习惯的重复触发会聚合计数，不再刷屏写多段重复内容。",
        "",
        "| 次数 | 最近触发 | 触发 | 以前可能会 | 现在改为 | 最近结果 |",
        "|---:|---|---|---|---|---|",
    ]
    for item in sorted(items, key=lambda x: str(x.get("last_seen_at", "")), reverse=True)[:30]:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"| {int(item.get('count') or 1)} | {item.get('last_seen_at','')} | "
            f"{str(item.get('cue','')).replace('|','/')} | "
            f"{str(item.get('original','')).replace('|','/')} | "
            f"{str(item.get('actual','')).replace('|','/')} | "
            f"{str(item.get('last_result','')).replace('|','/')} |"
        )
    _write_text(path, "\n".join(lines))
    if not found or int(found.get("count") or 0) in {2, 5, 10, 20, 50}:
        _append_text(
            os.path.join(_global_user_dir(workspace), "partner_reflection_log.md"),
            f"\n## {now} | {project} / {cue}\n\n- 原本可能动作：{original}\n- 实际动作：{actual}\n- 结果：{result}\n",
        )
    should_record_evolution = (not found) or int(found.get("count") or 0) in {2, 5, 10, 20, 50}
    if should_record_evolution:
        _append_partner_evolution(
            workspace,
            project=project,
            trigger=cue,
            before=original,
            after=actual,
            evidence=result,
        )
    _publish_global_mind_status(workspace)


def _append_partner_evolution(workspace: str, *, project: str, trigger: str, before: str, after: str, evidence: str) -> None:
    """Write a user-readable evolution card at project/global/shared levels."""
    instance_id = os.path.basename(os.path.normpath(workspace))
    block = (
        f"\n## {_now()} | Evolution Card\n\n"
        f"- 实例：{instance_id or 'unknown'}\n"
        f"- 项目：{project or 'unknown'}\n"
        f"- 触发：{trigger}\n"
        f"- 以前可能会：{before}\n"
        f"- 现在改为：{after}\n"
        f"- 证据/结果：{evidence}\n"
        "- 后续验证：观察这个习惯是否在其他项目或下一轮中被复用。\n"
    )
    _append_text(os.path.join(_project_user_dir(workspace, project), "growth_journal.md"), block)
    _append_text(os.path.join(_global_user_dir(workspace), "partner_evolution.md"), block)
    _append_text(os.path.join(_shared_user_dir(workspace), "shared_partner_evolution.md"), block)


def _append_user_growth(workspace: str, project: str, block: str) -> None:
    _append_text(os.path.join(_project_user_dir(workspace, project), "growth_journal.md"), block)


def _append_episode(workspace: str, project: str, kind: str, content: str, data: dict[str, Any] | None = None) -> None:
    ensure_mind_files(workspace, project)
    row = {"ts": _now(), "project": project, "kind": kind, "content": _clip(content, 700)}
    if data:
        row.update(data)
    _append_text(os.path.join(_project_mind_dir(workspace, project), "episodes.jsonl"), json.dumps(row, ensure_ascii=False))
    _append_text(
        os.path.join(_project_user_dir(workspace, project), "research_journey.md"),
        f"\n## {_now()} | {kind}\n\n{content.strip()}\n",
    )
    global_row = {"ts": _now(), "project": project, "kind": kind, "content": _clip(content, 700)}
    if data:
        global_row.update(data)
    _append_text(os.path.join(_global_mind_dir(workspace), "episodes.jsonl"), json.dumps(global_row, ensure_ascii=False))


def _append_insight(workspace: str, project: str, content: str) -> None:
    _append_text(os.path.join(_project_user_dir(workspace, project), "insight_log.md"), f"\n## {_now()}\n\n{content.strip()}\n")
    _append_text(
        os.path.join(_global_user_dir(workspace), "transferable_lessons.md"),
        f"\n## {_now()} | {project}\n\n{content.strip()}\n",
    )


def _append_reflection(workspace: str, project: str, trigger: str, conclusion: str, action: str) -> None:
    _append_text(
        os.path.join(_project_user_dir(workspace, project), "reflection_log.md"),
        f"\n## {_now()} | {trigger}\n\n- 反思结论：{conclusion}\n- 行动变化：{action}\n",
    )


def _append_breakthrough(workspace: str, project: str, problem: str, clue: str, verification: str, why: str, next_step: str) -> None:
    _append_text(
        os.path.join(_project_user_dir(workspace, project), "breakthroughs.md"),
        (
            f"\n## {_now()} | 突破记录\n\n"
            f"- 突破前问题：{problem}\n"
            f"- 触发线索：{clue}\n"
            f"- 验证动作：{verification}\n"
            f"- 为什么算突破：{why}\n"
            f"- 下一步：{next_step}\n"
        ),
    )


def _progress_score(workspace: str, project: str, parsed: dict[str, Any], issues: list[str], report_type: str) -> int:
    text = _round_text(parsed)
    score = 0
    if re.search(r"(运行|执行|测试|验证|审计|计算|训练|对接|bootstrap|make test|HTTP 200)", text, re.I):
        score += 3
    if re.search(r"(提升|下降|推翻|不可信|失败原因|瓶颈|根因|泄露|overfit|bias|gap)", text, re.I):
        score += 2
    if re.search(r"(hypothesis|模拟|simulation|synthetic|proxy|计划|指南|文件|目录|更新)", text, re.I):
        score -= 1
    if issues:
        score -= min(3, len(issues))
    if report_type == "breakthrough":
        score += 3
    score = max(-5, min(10, score))
    _append_text(
        os.path.join(_project_mind_dir(workspace, project), "progress_score.jsonl"),
        json.dumps({"ts": _now(), "project": project, "score": score, "report_type": report_type, "issues": issues[:5]}, ensure_ascii=False),
    )
    _append_text(
        os.path.join(_global_mind_dir(workspace), "progress_score.jsonl"),
        json.dumps({"ts": _now(), "project": project, "score": score, "report_type": report_type, "issues": issues[:5]}, ensure_ascii=False),
    )
    return score


def _action_signature(parsed: dict[str, Any]) -> str:
    text = "\n".join(
        str(parsed.get(k) or "")
        for k in ("action", "step_done", "next_action", "evidence")
    ).lower()
    text = re.sub(r"/(?:mnt|home|tmp)/[^\s，,；;]+", "<path>", text)
    text = re.sub(r"\b[\w.-]+\.(?:md|py|json|csv|txt|pdf|pptx)\b", "<file>", text)
    text = re.sub(r"\d+", "<n>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _clip(text, 180)


def _source_recovery_state(text: str) -> str:
    lower = text.lower()
    if re.search(r"(pipeline restored|流水线.*恢复|audit.*通过|路径真实性审计通过)", lower):
        return "pipeline_restored"
    if re.search(r"(audit_rerun|重跑.*审计|重新.*审计|复查)", lower):
        return "audit_rerun"
    if re.search(r"(copied|复制.*缺失|已复制|恢复.*文件)", lower):
        return "copied"
    if re.search(r"(candidate_verified|候选源.*校验通过|确认.*候选源)", lower):
        return "candidate_verified"
    if re.search(r"(candidate_found|候选源|找到.*候选|source_lookup)", lower):
        return "candidate_found"
    if re.search(r"(missing_detected|缺失|missing|源目录|真实源|source recovery)", lower):
        return "missing_detected"
    return ""


def _detect_stagnation_and_recovery(
    workspace: str,
    project: str,
    parsed: dict[str, Any],
    current_issues: list[str],
) -> list[str]:
    """Detect repeated loops generically and force a strategy change."""
    ensure_mind_files(workspace, project)
    mind = _project_mind_dir(workspace, project)
    path = os.path.join(mind, "loop_state.json")
    state = _load_json(path, {})
    if not isinstance(state, dict):
        state = {}

    text = _round_text(parsed)
    sig = _action_signature(parsed)
    prev_sig = str(state.get("last_signature") or "")
    repeat_count = int(state.get("repeat_count") or 0) + 1 if sig and sig == prev_sig else 1

    source_state = _source_recovery_state(text)
    source_prev = str(state.get("source_recovery_state") or "")
    source_repeat = int(state.get("source_recovery_repeat") or 0) + 1 if source_state and source_state == source_prev else (1 if source_state else 0)

    state.update({
        "updated_at": _now(),
        "last_signature": sig,
        "repeat_count": repeat_count,
        "source_recovery_state": source_state or source_prev,
        "source_recovery_repeat": source_repeat,
    })
    _write_json(path, state)

    issues: list[str] = []
    if repeat_count >= 3:
        issues.append("同类动作连续重复 3 轮以上且缺少新证据，必须切换策略或标记阻塞。")
        _add_habit(
            workspace,
            project,
            cue="同类动作连续重复且无新证据",
            routine="停止重复生成同类文件，切换为验证、复制、执行、对照或明确 blocked",
            check="下一步必须和上一轮动作类型不同，并产生可验证证据",
            source="LoopStagnationDetector",
        )
        _record_habit_application(
            workspace,
            project,
            cue="停滞检测",
            original="继续重复同一类动作",
            actual="强制切换策略或标记阻塞",
            result=f"repeat_count={repeat_count}",
        )
    if source_state and source_repeat >= 3 and source_state != "pipeline_restored":
        issues.append(f"源恢复状态卡在 {source_state} 已连续 {source_repeat} 轮，必须进入下一状态或向用户报告明确阻塞。")
        _add_habit(
            workspace,
            project,
            cue="源恢复状态反复停留",
            routine="按 missing_detected→candidate_found→candidate_verified→copied→audit_rerun→pipeline_restored 推进",
            check="每轮必须推进状态；不能反复写 source lookup",
            source="SourceRecoveryStateMachine",
        )
    return issues


def _round_text(parsed: dict[str, Any]) -> str:
    findings = "；".join(str(x) for x in (parsed.get("findings") or []))
    return "\n".join(
        str(parsed.get(k) or "")
        for k in ("action", "step_done", "evidence", "next_action", "files", "state_delta", "artifact_content")
    ) + "\n" + findings


INFLATED_COMPLETION_RE = re.compile(
    r"(ultimate|cosmic|eternal|transcendent|supreme|final_final|最终.*最终|项目已完成|可进行实验室合成|发布条件)",
    re.I,
)


def _detect_blocker(text: str) -> str:
    if re.search(r"(api key|api_key|密钥|预算|费用|账号|登录|token|真实\s*API|real api)", text, re.I):
        return "需要 API key/预算/账号等外部资源"
    if re.search(r"(真实数据|原始数据|样本|备份|源目录|source path|数据集)", text, re.I):
        return "需要真实数据、样本或源目录"
    return ""


def _detect_shortcut(project: str, text: str) -> str:
    return ""


def _detect_comparability_issues(workspace: str, project: str, parsed: dict[str, Any]) -> list[str]:
    text = _round_text(parsed)
    issues: list[str] = []
    baseline = load_baseline_contract(workspace, project)
    metric = load_metric_contract(workspace, project)
    if re.search(r"(最佳|提升|突破|优于|超过|更好|当前最佳|best)", text, re.I):
        if re.search(r"(synthetic|合成|模拟|simulation|proxy|代理)", text, re.I):
            issues.append("结果疑似来自合成/模拟/代理分数，不能直接写成真实项目最佳。")
        evidence = str(parsed.get("evidence") or "").strip().lower()
        if not evidence or evidence in {"hypothesis", "empty"}:
            issues.append("声称最佳/提升但缺少明确证据文件或可复现输出。")
        if baseline.get("best_known_result") and not re.search(r"(同数据|同协议|同评估|same|对照|baseline)", text, re.I):
            issues.append("声称提升但没有说明和 baseline 是否同任务、同数据、同协议、同指标。")
    if re.search(r"真实 API|real api", text, re.I) and re.search(r"simulation|模拟|dry", text, re.I):
        issues.append("真实 API 与 simulation/dry-run 边界混淆。")
    if metric.get("metrics") and re.search(r"(score|得分|MAE|R²|完成率)", text, re.I):
        if re.search(r"(单位不明|自定义)", text, re.I):
            issues.append("指标单位或可比性不明确，需要 metric contract 审计。")
    return issues


def _evidence_paths_exist(workspace: str, project: str, evidence: str, files: str = "") -> tuple[bool, list[str]]:
    """Check whether cited local evidence files actually exist."""
    project_dir = _project_dir(workspace, project)
    legacy_project_dir = os.path.join(workspace, "projects", _safe_name(project))
    candidates: list[str] = []
    for field in (evidence or "", files or ""):
        for item in re.split(r"[；;,\n]+", field):
            item = item.strip()
            if not item or item.lower() in {"hypothesis", "empty"} or item.startswith("system:"):
                continue
            if re.search(r"^https?://", item):
                continue
            # Strip natural-language explanations after paths, e.g.
            # "/path/a.md（精读笔记）" or "methods.md (updated)".
            match = re.search(
                r"(?P<path>(?:/[^；;,\n（）()]+|(?:20_records/)?projects/[^；;,\n（）()]+|[^；;,\n（）()]+)"
                r"\.(?:md|csv|json|txt|py|pkl|joblib|parquet|xpt))",
                item,
                re.I,
            )
            if match:
                candidates.append(match.group("path").strip())
    if not candidates:
        return False, []
    missing: list[str] = []
    for item in candidates:
        path = item
        possible: list[str] = []
        if not os.path.isabs(path):
            cleaned = re.sub(r"^20_records/projects/[^/]+/", "", path)
            cleaned = re.sub(r"^projects/[^/]+/", "", cleaned)
            possible.extend([
                os.path.join(project_dir, cleaned),
                os.path.join(legacy_project_dir, cleaned),
                os.path.join(workspace, path),
            ])
        else:
            possible.append(path)
        if not any(os.path.exists(p) for p in possible):
            missing.append(item)
    return len(missing) == 0, missing[:5]


def apply_round_guardrails(
    workspace: str,
    project: str,
    parsed: dict[str, Any],
    hermes_response: str = "",
) -> dict[str, Any]:
    """Apply generic gates to a structured project result.

    Returns a dict with modified parsed result, issues and reporting policy.
    """
    ensure_baseline_and_metric_contracts(workspace, project)
    parsed = dict(parsed or {})
    text = _round_text(parsed) + "\n" + (hermes_response or "")[:2000]
    issues = _detect_comparability_issues(workspace, project, parsed)
    evidence_ok, missing_evidence = _evidence_paths_exist(
        workspace,
        project,
        str(parsed.get("evidence") or ""),
        str(parsed.get("files") or ""),
    )
    metric_or_best_claim = bool(
        re.search(r"(MAE|R²|R2|AUC|准确率|当前最佳|最佳模型|新最佳|达成.*目标|score|得分)", text, re.I)
    )
    if metric_or_best_claim and not evidence_ok:
        issues.append(
            "关键指标/最佳结果的证据文件未完整落盘或不可复查，必须先做证据审计后再向用户确认。"
        )
        if missing_evidence:
            parsed["next_action"] = (
                "先补齐或重跑证据文件："
                + "；".join(missing_evidence[:3])
                + "，再判断当前指标是否可信。"
            )
        else:
            parsed["next_action"] = "先把本轮指标落成可复查结果文件，并执行一次复跑/泄露审计，再判断是否可信。"
        parsed["state_delta"] = (parsed.get("state_delta") or "") + "\n证据审计：关键指标尚未通过证据文件存在性检查，当前只按待复核结果处理。"
    blocker = _detect_blocker(text)
    shortcut = _detect_shortcut(project, text)
    inflated = bool(INFLATED_COMPLETION_RE.search(text))

    if blocker:
        issues.append(blocker)
        _add_habit(
            workspace,
            project,
            cue="遇到 API key、预算、账号、真实数据等外部阻塞",
            routine="主动向用户说明需要什么，同时切换到无阻塞分支继续推进",
            check="blocker 已记录，next action 是可绕开的具体动作",
            source="BlockerEscalationPolicy",
        )
        _record_habit_application(
            workspace,
            project,
            cue="阻塞处理",
            original="继续围绕阻塞项打转或等待用户",
            actual="记录 blocker，并要求下一步改为无阻塞分支",
            result=blocker,
        )
        parsed["next_action"] = _fallback_next_action_for_blocker(project, blocker)
        parsed["state_delta"] = (parsed.get("state_delta") or "") + f"\n阻塞记录：{blocker}；已切换到无阻塞替代路线。"
    if shortcut:
        issues.append(shortcut)
        _add_habit(
            workspace,
            project,
            cue="任务因为资料容易获取而偏离用户原始意图",
            routine="回到用户输入/外部内容观点本身，GitHub/README 只作为验证渠道",
            check="下一步必须处理用户内容、观点、争议或趋势，而不是继续扫 README",
            source="AntiShortcutPolicy",
        )
        _record_habit_application(
            workspace,
            project,
            cue="反捷径",
            original="继续验证容易访问的 GitHub README",
            actual="回到用户分享内容和 agent 趋势/争议/灵感",
            result=shortcut,
        )
        parsed["next_action"] = "回到用户分享/公开内容本身，提炼观点和假设，再选择一个来源做验证；GitHub 只能作为证据渠道。"
    if inflated:
        issues.append("完成声明膨胀或最终态污染，必须降级为阶段性原型/待验证结论。")
        _add_habit(
            workspace,
            project,
            cue="出现 final/ultimate/已完成/发布条件等完成膨胀词",
            routine="触发 completion claim gate，降级为阶段性结果并列出缺失验证",
            check="没有同协议证据不得说完成/可发布/可合成",
            source="CompletionClaimGate",
        )
        parsed["step_done"] = re.sub(INFLATED_COMPLETION_RE, "阶段性结果", str(parsed.get("step_done") or ""))
        parsed["next_action"] = parsed.get("next_action") or "先补充同协议证据和风险审计，再判断是否能进入阶段汇报。"

    if issues:
        parsed["findings"] = _dedupe((parsed.get("findings") or []) + issues)[:3]
        parsed["state_delta"] = (parsed.get("state_delta") or "") + "\n守门检查：" + "；".join(issues[:3])

    stagnation_issues = _detect_stagnation_and_recovery(workspace, project, parsed, issues)
    if stagnation_issues:
        issues = _dedupe(issues + stagnation_issues)
        parsed["findings"] = _dedupe((parsed.get("findings") or []) + stagnation_issues)[:3]
        parsed["next_action"] = _strategy_shift_next_action(project, parsed, stagnation_issues)
        parsed["state_delta"] = (parsed.get("state_delta") or "") + "\n停滞/恢复检查：" + "；".join(stagnation_issues[:3])

    report_type = classify_report_type(workspace, project, parsed, issues)
    score = _progress_score(workspace, project, parsed, issues, report_type)
    update_visible_mind_state(workspace, project, parsed, issues, report_type, score)
    return {"parsed": parsed, "issues": issues, "report_type": report_type, "progress_score": score}


def _dedupe(items: list[Any]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        text = _clip(str(item), 240)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _fallback_next_action_for_blocker(project: str, blocker: str) -> str:
    if "API" in blocker or "api" in blocker:
        return "先记录需要 API key/预算的问题，同时切换到不依赖该资源的证据审计、公开资料核验或最小可复现整理。"
    if "数据" in blocker or "源目录" in blocker:
        return "先记录需要真实数据/源目录的问题，同时继续做现有结果审计、baseline 对照和最小可复现脚本整理。"
    return "记录阻塞问题，同时切换到一个不依赖该外部资源的最小推进动作。"


def _strategy_shift_next_action(project: str, parsed: dict[str, Any], issues: list[str]) -> str:
    text = _round_text(parsed)
    if _source_recovery_state(text):
        return (
            "停止重复查找源路径，按状态机推进：校验候选源归属；若通过则复制缺失项并重跑路径审计；"
            "若无法校验则记录明确阻塞，同时改做现有证据和 baseline 可比性审计。"
        )
    if re.search(r"(计划|方案|总结|整理|文档|文件)", text):
        return "停止继续写计划或整理文件，选择一个最小可执行动作运行，并用真实输出作为证据。"
    return "切换策略：不要重复上一轮动作，改做证据审计、真实执行、baseline 对照或明确 blocked。"


def classify_report_type(workspace: str, project: str, parsed: dict[str, Any], issues: list[str] | None = None) -> str:
    text = _round_text(parsed)
    issues = issues or []
    if _detect_blocker(text) or any("需要 API" in x or "需要真实数据" in x for x in issues):
        return "blocker_question"
    if re.search(r"(突破|新最佳|推翻|根因|显著|通过.*测试|86 passed|真实实验|完成.*验证|泄露|不可信|阻止)", text, re.I):
        return "breakthrough"
    if issues:
        return "meaningful_progress"
    if re.search(r"(验证|审计|运行|测试|计算|分析|HTTP 200|benchmark|对比|发现|失败原因)", text, re.I):
        if not re.search(r"(只是|仅|文件|目录|更新.*md|写入|创建|整理旧文件|无新信息)", text, re.I):
            return "meaningful_progress"
    return "meaningful_progress"


def should_send_user_report(report_type: str, progress_score: int | None = None) -> bool:
    return bool(report_type)


def improve_user_report(content: str, report_type: str) -> str:
    text = (content or "").strip()
    if not text:
        return ""
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def update_visible_mind_state(workspace: str, project: str, parsed: dict[str, Any], issues: list[str], report_type: str, score: int) -> None:
    ensure_mind_files(workspace, project)
    text = _round_text(parsed)
    done = _clip(str(parsed.get("step_done") or ""), 260)
    next_action = _clip(str(parsed.get("next_action") or ""), 260)
    findings = "；".join(_dedupe([str(x) for x in (parsed.get("findings") or [])])[:3])
    ts = _now()
    _append_episode(
        workspace,
        project,
        "round",
        f"- 完成：{done or '未形成明确完成项'}\n- 发现：{findings or '无明确新增发现'}\n- 下一步：{next_action or '待定'}",
        {"report_type": report_type, "score": score, "issues": issues[:5]},
    )
    if issues:
        _append_reflection(
            workspace,
            project,
            "守门检查发现风险",
            "；".join(issues[:3]),
            next_action or "切换到证据审计或无阻塞替代路线",
        )
    if report_type == "breakthrough":
        _append_breakthrough(
            workspace,
            project,
            problem=_clip(_first_problem(text), 180),
            clue=_clip(findings or done, 220),
            verification=_clip(str(parsed.get("evidence") or "需要补证据"), 220),
            why=_clip(done, 220),
            next_step=next_action,
        )
    if re.search(r"(为什么|因此|说明|启发|迁移|边界|趋势|设计原则|gap)", text):
        _append_insight(workspace, project, f"- 观察：{findings or done}\n- 启发：{next_action}")
    _write_working_memory(workspace, project, done, findings, next_action, issues, report_type, score)
    _publish_mind_status(workspace, project, done, findings, next_action, issues, report_type, score)
    _publish_global_mind_status(workspace, latest_project=project, latest_done=done, latest_findings=findings, latest_next=next_action)


def _first_problem(text: str) -> str:
    for line in text.splitlines():
        if re.search(r"(问题|瓶颈|风险|失败|缺失|不可信|阻塞)", line):
            return line.strip()
    return "上一轮推进中发现需要突破的问题"


def _write_working_memory(workspace: str, project: str, done: str, findings: str, next_action: str, issues: list[str], report_type: str, score: int) -> None:
    text = (
        f"# {project} Working Memory\n\n"
        f"- 更新时间：{_now()}\n"
        f"- 本轮类型：{report_type}\n"
        f"- 推进评分：{score}\n"
        f"- 最近完成：{done or 'EMPTY'}\n"
        f"- 关键发现：{findings or 'EMPTY'}\n"
        f"- 当前风险/阻塞：{'；'.join(issues[:3]) if issues else 'EMPTY'}\n"
        f"- 下一步：{next_action or 'EMPTY'}\n"
    )
    _write_text(os.path.join(_project_mind_dir(workspace, project), "working_memory.md"), text)


def _publish_mind_status(workspace: str, project: str, done: str, findings: str, next_action: str, issues: list[str], report_type: str, score: int) -> None:
    runtime_line = ""
    try:
        from .runtime_monitor import publish_runtime_cost_summary

        runtime = publish_runtime_cost_summary(workspace)
        if runtime.get("calls"):
            runtime_line = (
                f"- 最近调用：{runtime.get('calls')} 次，估算 token {runtime.get('total_tokens_est')}，"
                f"失败 {runtime.get('failed')} 次\n"
            )
    except Exception:
        runtime_line = ""
    habits = _load_json(os.path.join(_project_mind_dir(workspace, project), "habits.json"), {"habits": []})
    habit_lines = []
    for item in (habits.get("habits") if isinstance(habits, dict) else [] or [])[-3:]:
        if isinstance(item, dict):
            habit_lines.append(f"- {item.get('cue')}: {item.get('routine')}")
    text = (
        f"# {project} Mind Status\n\n"
        f"更新时间：{_now()}\n\n"
        f"## 当前工作记忆\n"
        f"- 本轮类型：{report_type}\n"
        f"- 推进评分：{score}\n"
        f"- 最近完成：{done or 'EMPTY'}\n"
        f"- 关键发现：{findings or 'EMPTY'}\n"
        f"- 下一步：{next_action or 'EMPTY'}\n\n"
        f"## 当前阻塞/风险\n"
        f"{chr(10).join('- ' + x for x in issues[:5]) if issues else '- EMPTY'}\n\n"
        f"## 当前重要习惯\n"
        f"{chr(10).join(habit_lines) if habit_lines else '- EMPTY'}\n"
    )
    _write_text(os.path.join(_project_user_dir(workspace, project), "mind_status.md"), text)
    current_dir = os.path.join(_user_dir(workspace), "current_project")
    os.makedirs(current_dir, exist_ok=True)
    summary = (
        f"# {project}\n\n"
        f"当前关注：{project}\n\n"
        f"## 最近真实进展\n"
        f"{done or '暂无明确新进展。'}\n\n"
        f"## 关键判断\n"
        f"{findings or '暂无明确新增判断。'}\n\n"
        f"## 风险/阻塞\n"
        f"{'；'.join(issues[:5]) if issues else '暂无明确阻塞。'}\n\n"
        f"## 下一步\n"
        f"{next_action or '继续做一个最小可验证推进动作。'}\n\n"
        f"## 运行消耗\n"
        f"{runtime_line or '- 暂无调用统计。'}"
        f"\n"
        f"## 用户可读记录\n"
        f"- `../projects/{_safe_name(project)}/research_journey.md`\n"
        f"- `../projects/{_safe_name(project)}/growth_journal.md`\n"
        f"- `../projects/{_safe_name(project)}/mind_status.md`\n"
        f"- `../runtime_cost.md`\n"
    )
    _write_text(os.path.join(current_dir, "summary.md"), summary)
    journey = _read_text(os.path.join(_project_user_dir(workspace, project), "research_journey.md"), 5000)
    if journey:
        _write_text(os.path.join(current_dir, "exploration_log.md"), journey)


def _publish_global_mind_status(
    workspace: str,
    latest_project: str = "",
    latest_done: str = "",
    latest_findings: str = "",
    latest_next: str = "",
) -> None:
    ensure_global_mind_files(workspace)
    habits = _load_json(os.path.join(_global_mind_dir(workspace), "habits.json"), {"habits": []})
    habit_items = habits.get("habits") if isinstance(habits, dict) else []
    habit_lines = []
    for item in (habit_items or [])[-8:]:
        if not isinstance(item, dict):
            continue
        projects = ", ".join(str(x) for x in (item.get("projects") or [])[-4:])
        habit_lines.append(
            f"- 触发：{item.get('cue')}\n  动作：{item.get('routine')}\n  来源项目：{projects or 'unknown'}"
        )
    episodes_path = os.path.join(_global_mind_dir(workspace), "episodes.jsonl")
    recent_events: list[str] = []
    try:
        with open(episodes_path, "r", encoding="utf-8") as f:
            rows = [line.strip() for line in f if line.strip()][-5:]
        for row in rows:
            data = json.loads(row)
            recent_events.append(
                f"- {data.get('project')} / {data.get('kind')}: {_clip(str(data.get('content') or ''), 130)}"
            )
    except Exception:
        pass
    text = (
        "# Partner Mind Status\n\n"
        f"更新时间：{_now()}\n\n"
        "## 最近一次项目推进\n"
        f"- 项目：{latest_project or 'EMPTY'}\n"
        f"- 完成：{latest_done or 'EMPTY'}\n"
        f"- 发现：{latest_findings or 'EMPTY'}\n"
        f"- 下一步：{latest_next or 'EMPTY'}\n\n"
        "## 跨项目习惯\n"
        f"{chr(10).join(habit_lines) if habit_lines else '- EMPTY'}\n\n"
        "## 最近事件记忆\n"
        f"{chr(10).join(recent_events) if recent_events else '- EMPTY'}\n\n"
        "## 说明\n"
        "- 这里记录的是 Partner 级别经验，不属于某一个实例。今天 01 学到的泄漏审计习惯，明天 03/04/05 也可以复用。\n"
    )
    _write_text(os.path.join(_global_user_dir(workspace), "partner_mind_status.md"), text)


def _publish_shared_mind_status(workspace: str) -> None:
    ensure_shared_mind_files(workspace)
    habits = _load_json(os.path.join(_shared_mind_dir(workspace), "habits.json"), {"habits": []})
    habit_items = habits.get("habits") if isinstance(habits, dict) else []
    habit_lines = []
    for item in (habit_items or [])[-12:]:
        if not isinstance(item, dict):
            continue
        instances = ", ".join(str(x) for x in (item.get("instances") or [])[-6:])
        projects = ", ".join(str(x) for x in (item.get("projects") or [])[-4:])
        habit_lines.append(
            f"- 触发：{item.get('cue')}\n"
            f"  动作：{item.get('routine')}\n"
            f"  适用实例：{instances or 'unknown'}\n"
            f"  来源项目：{projects or 'unknown'}"
        )
    text = (
        "# Shared Partner Mind Status\n\n"
        f"更新时间：{_now()}\n\n"
        "## 共享习惯\n"
        f"{chr(10).join(habit_lines) if habit_lines else '- EMPTY'}\n\n"
        "## 资源访问策略\n"
        "- API key、账号、预算、真实数据、源目录缺失时，先报告需要什么，再切换到无阻塞分支。\n"
        "- 小红书/B站/知乎等平台正文不可读时，不绕过限制，不编造正文，只记录可见线索并请求截图/正文。\n"
        "- 用户随手分享的科普/长文默认进入普通学习，不自动改变项目主线。\n"
        "- 用户明确要求纳入项目，或内容与项目高度相关时，才转成项目 hypothesis 或下一步动作。\n\n"
        "## 说明\n"
        "- 这是 workspace 级别的 Partner 总脑，所有实例都应读取并复用这里的习惯。\n"
    )
    _write_text(os.path.join(_shared_user_dir(workspace), "shared_partner_mind_status.md"), text)


def maybe_force_stage_report(workspace: str, project: str, step: int, report_type: str) -> tuple[str, str]:
    """Trigger user-readable stage reports earlier for breakthroughs or stagnation."""
    from .stage_report import maybe_stage_report_objective

    objective, path = maybe_stage_report_objective(workspace, project, step)
    if objective:
        return objective, path
    if report_type != "breakthrough":
        return "", ""
    state_path = os.path.join(workspace, "state", "stage_report_state.json")
    state = _load_json(state_path, {})
    project_state = state.get(project) if isinstance(state, dict) else {}
    last = str((project_state or {}).get("last_generated_at") or "")
    if last:
        try:
            last_day = last.split("T", 1)[0]
            if last_day == datetime.now().date().isoformat():
                return "", ""
        except Exception:
            pass
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    pdir = _project_dir(workspace, project)
    path = os.path.join(pdir, "reports", f"stage_report_{ts}.md")
    objective = (
        "本项目出现突破或关键风险节点。请停止扩展实验，生成一份用户可读阶段报告。"
        "必须说明背景、问题、真实进展、证据、风险、失败边界、下一步。"
        "不要列文件清单；不要把 simulation/proxy/synthetic 写成真实结论。"
    )
    return objective, path


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(float(raw))
        return value if value >= 0 else default
    except Exception:
        return default


def _count_project_files(workspace: str, project: str) -> int:
    roots = [
        _project_dir(workspace, project),
        os.path.join(_project_user_dir(workspace, project)),
    ]
    seen: set[str] = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for cur, _, files in os.walk(root):
            for name in files:
                seen.add(os.path.realpath(os.path.join(cur, name)))
    return len(seen)


def _recent_runtime_tokens(workspace: str, limit: int = 120) -> int:
    rows = _load_recent_jsonl(os.path.join(workspace, "state", "logs", "agent_runs.jsonl"), limit=limit)
    total = 0
    for row in rows:
        try:
            total += int(row.get("total_tokens_est") or 0)
        except Exception:
            pass
    return total


def _has_user_stage_report(workspace: str, project: str) -> bool:
    report_dir = os.path.join(workspace, "state", "user", "reports", _safe_name(project))
    if not os.path.isdir(report_dir):
        return False
    try:
        names = os.listdir(report_dir)
    except OSError:
        return False
    return any(name.endswith((".pdf", ".pptx")) for name in names) and "latest_stage_report.md" in names


def _has_showcase(workspace: str, project: str) -> bool:
    show_dir = os.path.join(workspace, "state", "user", "showcase", _safe_name(project))
    return os.path.exists(os.path.join(show_dir, "README.md"))


def is_literature_reference_task(workspace: str, project: str) -> bool:
    """Return true for tasks whose user-facing deliverable is literature/method review.

    This is intentionally generic: if the active goal/brief/recent task says
    "find papers / literature / references / common methods", Partner should
    produce a solid report and pause for user evaluation instead of drifting
    into scripts, simulations, or data analysis unless the user asks for that.
    """
    parts: list[str] = []
    for rel in ("state/active_plan.json", "state/task_queue.json"):
        data = _load_json(os.path.join(workspace, rel), {})
        if isinstance(data, dict):
            if data.get("title") == project:
                parts.extend(str(data.get(k) or "") for k in ("goal", "heartbeat_summary", "title"))
        elif isinstance(data, list):
            for item in data[-10:]:
                if not isinstance(item, dict):
                    continue
                if item.get("status") in {"obsolete", "done", "completed"}:
                    continue
                if project and item.get("title") and item.get("title") != project:
                    continue
                parts.extend(str(item.get(k) or "") for k in ("title", "description", "result_summary"))
    brief = _read_text(os.path.join(_project_dir(workspace, project), "project_brief.md"), 5000)
    parts.append(brief)
    text = "\n".join(parts)
    wants_literature = bool(re.search(r"(找|查|搜|整理|调研|精读|参考).{0,12}(文献|论文|资料|方法|路线|综述|reference|paper|literature)", text, re.I))
    wants_analysis = bool(re.search(r"(运行|训练|建模|分析脚本|原始数据|数据分析|pipeline|建模型|预测|调参|simulation|模拟数据)", text, re.I))
    # If both appear, treat it as literature-only only when the current brief
    # still says the deliverable is reference/method route rather than actual
    # analysis.
    reference_positioning = bool(re.search(r"(文献参考|方法学路线|Methods 参考|参考整理|代码参数案例|证据审计)", brief, re.I))
    return wants_literature and (not wants_analysis or reference_positioning)


def maybe_pause_after_literature_report(
    workspace: str,
    project: str,
    *,
    published_report: bool = False,
    reason: str = "",
) -> tuple[bool, str]:
    contract_reason = ""
    literature_task = is_literature_reference_task(workspace, project)
    try:
        from .project_state import load_project_guardrail

        guardrail = load_project_guardrail(workspace, project)
        criteria = [str(x).strip() for x in (guardrail.get("completion_criteria") or []) if str(x).strip()]
        if criteria and published_report:
            contract_reason = "任务合同内交付物已形成阶段汇报，等待用户评估是否进入下一阶段"
    except Exception:
        contract_reason = ""
    if not contract_reason and not literature_task:
        return False, ""
    if literature_task and not published_report and not _has_user_stage_report(workspace, project):
        return False, ""
    if contract_reason and not published_report:
        return False, ""
    pause_reason = reason or contract_reason or "文献/方法参考任务已形成阶段汇报，等待用户评估是否进入数据分析或代码实现"
    try:
        from .project_state import set_project_status

        set_project_status(workspace, project, "waiting", pause_reason)
    except Exception:
        pass
    _write_text(
        os.path.join(_project_user_dir(workspace, project), "literature_task_pause.md"),
        (
            "# Literature Task Pause\n\n"
            f"时间：{_now()}\n\n"
            f"原因：{pause_reason}\n\n"
            "Partner 已完成当前任务合同内的阶段汇报后应暂停，"
            "不要自动扩展到用户没有要求的新阶段、新实验或新交付物。\n\n"
            "如果用户继续明确要求下一阶段，再按新的任务合同推进。\n"
        ),
    )
    return True, pause_reason


def maybe_pause_project_for_quality_gate(
    workspace: str,
    project: str,
    *,
    next_step: int,
    report_type: str,
    progress_score: int,
) -> tuple[bool, str]:
    """Pause runaway projects after enough user-visible evidence exists.

    This is generic. It is not a hardcoded demo patch: every long-running
    project gets a quality/cost gate so Partner does not keep spending tokens on
    extra wrapping after it has a report/showcase or has exceeded budget.
    """
    max_steps = _env_int("PARTNER_MAX_PROJECT_STEPS", 120)
    max_files = _env_int("PARTNER_MAX_PROJECT_FILES", 220)
    max_recent_tokens = _env_int("PARTNER_MAX_RECENT_TOKEN_EST", 250_000)
    stop_after_showcase = os.getenv("PARTNER_STOP_AFTER_SHOWCASE", "1").strip().lower() not in {"0", "false", "off", "no"}

    reasons: list[str] = []
    file_count = _count_project_files(workspace, project)
    recent_tokens = _recent_runtime_tokens(workspace)
    has_report = _has_user_stage_report(workspace, project)
    has_showcase = _has_showcase(workspace, project)

    if max_steps and next_step >= max_steps:
        reasons.append(f"项目轮次达到上限 {next_step}/{max_steps}")
    if max_files and file_count >= max_files:
        reasons.append(f"项目文件数达到上限 {file_count}/{max_files}")
    if max_recent_tokens and recent_tokens >= max_recent_tokens:
        reasons.append(f"近期估算 token 达到上限 {recent_tokens}/{max_recent_tokens}")
    if stop_after_showcase and has_report and has_showcase and report_type in {"breakthrough", "meaningful_progress", "blocker_question"}:
        reasons.append("已生成阶段汇报和 showcase，适合暂停给用户评估")

    if not reasons:
        return False, ""

    reason = "；".join(reasons[:4])
    try:
        from .project_state import set_project_status

        set_project_status(workspace, project, "waiting", reason)
    except Exception:
        pass
    _write_text(
        os.path.join(_project_user_dir(workspace, project), "quality_gate_pause.md"),
        (
            "# Quality Gate Pause\n\n"
            f"时间：{_now()}\n\n"
            f"暂停原因：{reason}\n\n"
            f"- 当前 step：{next_step}\n"
            f"- 项目文件数：{file_count}\n"
            f"- 最近估算 token：{recent_tokens}\n"
            f"- 已有阶段汇报：{'yes' if has_report else 'no'}\n"
            f"- 已有 showcase：{'yes' if has_showcase else 'no'}\n\n"
            "当前阶段的基本任务已经完成，已有一版可给用户查看的报告/展示材料。\n"
            "Partner 会先把报告交给用户评估，不再继续堆重复材料。\n"
            "用户继续发送新指令、截图、正文、公开链接、真实 API/数据或新的判断后，"
            "项目会被唤醒，先吸收材料并汇报，再继续推进一轮。\n"
        ),
    )
    _record_habit_application(
        workspace,
        project,
        cue="质量/成本门槛触发",
        original="继续生成更多文件、总结和包装材料",
        actual="阶段性暂停自动扩展；收到新材料或新指令时临时唤醒并做一次材料吸收/项目推进",
        result=reason,
    )
    return True, reason


def record_user_signal_to_mind(workspace: str, project: str, text: str, kind: str = "user_signal") -> None:
    """Persist user corrections/ideas/risks as visible growth, not just queue items."""
    if not text:
        return
    project = project or "当前项目"
    ensure_mind_files(workspace, project)
    _append_episode(
        workspace,
        project,
        kind,
        f"用户信号：{text.strip()}",
        {"source": "user"},
    )
    lower = text.lower()
    if re.search(r"(数据泄露|泄露|leakage|异常好|太好|过拟合|不可信|有问题)", text, re.I):
        _add_habit(
            workspace,
            project,
            cue="用户指出结果可能异常、泄露或不可信",
            routine="暂停把结果写成最佳，先做 baseline/comparability/leakage 审计",
            check="审计通过前不更新当前最佳结果",
            source="user_signal",
        )
        _append_reflection(
            workspace,
            project,
            "用户经验风险信号",
            "用户经验可能比当前自动指标更早发现泄露、过拟合或伪提升。",
            "下一轮优先做证据审计，而不是继续调参。",
        )
    elif re.search(r"(不是做|不要做|别做|跑偏|纠正|应该是做|不是.*而是)", text):
        _add_habit(
            workspace,
            project,
            cue="用户纠正项目方向",
            routine="更新项目边界，后续 action 前先检查 forbidden scope",
            check="若下一轮再次命中 forbidden scope，必须阻止并切换回允许主线",
            source="user_correction",
        )
        _append_reflection(
            workspace,
            project,
            "用户纠偏",
            "方向纠偏必须变成长期边界和行动前检查，而不是只回复收到。",
            "下一轮先核对项目 contract 和当前 action 是否一致。",
        )
    elif re.search(r"(老师|导师|文献|论文|推文|视频|小红书|B站|知乎|公众号|灵感|想法|启发)", text, re.I):
        _add_habit(
            workspace,
            project,
            cue="用户分享外部内容或灵感",
            routine="先提取观点和 hypothesis，再寻找证据验证，不直接当事实",
            check="输出 verified / inferred / hypothesis 分层",
            source="user_idea",
        )
        _append_insight(
            workspace,
            project,
            f"- 用户新输入：{_clip(text, 260)}\n- 处理原则：先作为 hypothesis，再做证据验证和项目迁移判断。",
        )

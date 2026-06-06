"""Long-term research memory for Partner.

This is the lightweight "researcher habit" layer:
- project cards: current thesis, bottlenecks, next bets
- lessons: failed methods, working methods, transfer candidates
- idea inbox: user/teacher inspirations that should not be lost

It deliberately stays compact. The executor receives only a small context
slice, not the full memory store.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any


MAX_ITEMS = 200
OPEN_IDEA_STATUSES = {"open", "unprocessed", ""}


def _memory_path(workspace: str) -> str:
    state_dir = os.path.join(workspace, "state")
    os.makedirs(state_dir, exist_ok=True)
    return os.path.join(state_dir, "research_memory.json")


def _system_path(workspace: str, *parts: str) -> str:
    path = os.path.join(workspace, "system", *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _workspace_root(workspace: str) -> str:
    norm = os.path.normpath(workspace)
    if os.path.basename(os.path.dirname(norm)) == "instances":
        return os.path.dirname(os.path.dirname(norm))
    return norm


def _shared_path(workspace: str, *parts: str) -> str:
    path = os.path.join(_workspace_root(workspace), "shared_mind", *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _state_path(workspace: str, *parts: str) -> str:
    path = os.path.join(workspace, "state", *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _append_jsonl(path: str, row: dict[str, Any]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clip(text: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    text = _collapse_repeated_tokens(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _collapse_repeated_tokens(text: str) -> str:
    """Remove prompt/memory pollution such as '最终最终最终...'."""
    if not text:
        return ""
    for token in ("最终", "final", "FINAL", "完成", "下一步"):
        pattern = f"(?:{re.escape(token)})" + r"{4,}"
        text = re.sub(pattern, token, text)
    text = re.sub(r"(_final){4,}", "_final", text, flags=re.I)
    return text


def _looks_like_memory_noise(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return True
    hard_noise = [
        r"[\w.-]+\.md",
        r"[\w.-]+\.py",
        r"/(?:mnt|home|tmp)/",
        r"\d+\s*字节",
        r"\bbytes?\b",
        r"\blines?\b",
        r"\bfiles?\b",
        r"总文件数",
        r"文件数",
        r"Verified文件",
        r"Hypothesis文件",
        r"Inferred文件",
        r"文件膨胀",
        r"目录结构",
        r"关键目录",
        r"system:",
        r"完成态/等待态空转",
        r"path_reality_check",
        r"trace_detail",
        r"breakthrough_queue",
        r"project_brief",
        r"current_best_result",
        r"FINAL_REPORT",
    ]
    return any(re.search(pattern, stripped, re.I) for pattern in hard_noise)


def _clean_memory_text(text: str, limit: int = 180) -> str:
    text = _clip(text, limit)
    if not text or _looks_like_memory_noise(text):
        return ""
    text = re.sub(r"[\w.-]+\.(?:md|py)", "", text)
    text = re.sub(r"/(?:mnt|home|tmp)/[^\s，,；;]+", "", text)
    text = re.sub(r"\([^)]*\b(?:bytes?|files?|lines?)\b[^)]*\)", "", text, flags=re.I)
    text = re.sub(r"\d+\s*字节", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ，,；;")
    if not text or _looks_like_memory_noise(text):
        return ""
    return _clip(text, limit)


def _clip_block(text: str, limit: int = 1100) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _default_memory() -> dict[str, Any]:
    return {
        "meta": {"created_at": _now(), "updated_at": _now(), "version": 1},
        "projects": {},
        "lessons": [],
        "ideas": [],
        "episodes": [],
        "growth_events": [],
    }


def load_memory(workspace: str) -> dict[str, Any]:
    path = _memory_path(workspace)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            base = _default_memory()
            base.update(data)
            return base
    except Exception:
        pass
    return _default_memory()


def save_memory(workspace: str, memory: dict[str, Any]):
    memory.setdefault("meta", {})
    memory["meta"]["updated_at"] = _now()
    for key in ("lessons", "ideas", "episodes", "growth_events"):
        items = memory.get(key) or []
        memory[key] = _dedupe_rows(items)[-MAX_ITEMS:]
    for card in (memory.get("projects") or {}).values():
        if isinstance(card, dict):
            for key in ("bottlenecks", "next_bets", "method_boundaries"):
                cleaned = [_clean_memory_text(str(x), 160) for x in card.get(key, [])]
                card[key] = _dedupe_recent([x for x in cleaned if x], 8)
            if card.get("latest_result"):
                card["latest_result"] = _clean_memory_text(str(card["latest_result"]), 180)
    path = _memory_path(workspace)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)
    _write_user_summary(workspace, memory)
    _write_growth_summary(workspace, memory)


def _dedupe_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = (
            item.get("project", ""),
            item.get("kind", item.get("type", "")),
            _clip(item.get("content", item.get("event", "")), 160),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _project_card(memory: dict[str, Any], project: str) -> dict[str, Any]:
    projects = memory.setdefault("projects", {})
    card = projects.setdefault(project, {})
    card.setdefault("updated_at", _now())
    card.setdefault("current_thesis", "")
    card.setdefault("bottlenecks", [])
    card.setdefault("next_bets", [])
    card.setdefault("method_boundaries", [])
    card.setdefault("latest_result", "")
    return card


def record_user_signal(workspace: str, project: str, text: str, kind: str = "user_idea"):
    """Persist user/teacher ideas without turning them into hard prompt rules."""
    if not text:
        return
    memory = load_memory(workspace)
    idea_row = {
        "ts": _now(),
        "time": _now(),
        "project": project or "",
        "source": _idea_source(kind),
        "kind": kind,
        "idea": _clip(text, 300),
        "content": _clip(text, 300),
        "possibly_related_projects": [project] if project else [],
        "why_it_matters": "",
        "status": "absorbed" if kind == "correction_signal" else "open",
    }
    memory.setdefault("ideas", []).append(idea_row)
    _append_jsonl(_system_path(workspace, "ideas", "idea_inbox.jsonl"), idea_row)
    card = _project_card(memory, project or "未指定项目")
    if kind in ("teacher_advice", "user_idea"):
        card["next_bets"] = _dedupe_recent([_clip(text, 160)] + card.get("next_bets", []), 8)
    if kind == "risk_signal":
        card["bottlenecks"] = _dedupe_recent([_clip(text, 160)] + card.get("bottlenecks", []), 8)
        lesson = {
            "ts": _now(),
            "project": project or "",
            "type": "user_quality_signal",
            "content": _clip(text, 260),
            "transferable": True,
        }
        memory.setdefault("lessons", []).append(lesson)
        _append_method_memory(workspace, lesson)
    save_memory(workspace, memory)


def get_open_idea(workspace: str, project: str) -> dict[str, Any] | None:
    """Return the highest-priority unprocessed user/teacher idea.

    Ordering matters for rapid multi-message sharing: a short card/link sent
    after a long pasted article must not steal the next project round from the
    richer project-specific material.
    """
    memory = load_memory(workspace)
    candidates: list[dict[str, Any]] = []
    for idx, idea in enumerate(memory.get("ideas") or []):
        kind = str(idea.get("kind") or "")
        idea_project = str(idea.get("project") or "").strip()
        if kind == "correction_signal":
            continue
        if idea_project and idea_project != project:
            continue
        # Generic external-learning notes without a project anchor are memory
        # material, not project-driving instructions. Otherwise a metadata-only
        # card/attachment can keep reactivating an unrelated waiting project.
        if kind == "external_learning" and not idea_project:
            continue
        if str(idea.get("status", "open")).lower() in OPEN_IDEA_STATUSES:
            row = dict(idea)
            row["_idx"] = idx
            candidates.append(row)
    if not candidates:
        return None

    def score(idea: dict[str, Any]) -> tuple[int, int, int, int]:
        text = str(idea.get("content") or idea.get("idea") or "")
        kind = str(idea.get("kind") or "")
        has_project = 1 if idea.get("project") == project else 0
        is_user_signal = 1 if kind in {"user_idea", "teacher_advice", "risk_signal"} else 0
        rich_text = min(len(text), 2000)
        return (has_project, is_user_signal, rich_text, int(idea.get("_idx") or 0))

    best = max(candidates, key=score)
    best.pop("_idx", None)
    return best


def mark_idea_processed(workspace: str, project: str, idea_text: str, status: str = "absorbed"):
    if not idea_text:
        return
    memory = load_memory(workspace)
    target = _clip(idea_text, 160)
    for idea in reversed(memory.get("ideas") or []):
        if idea.get("project") and idea.get("project") != project:
            continue
        if _clip(idea.get("content") or idea.get("idea") or "", 160) == target:
            idea["status"] = status
            idea["processed_at"] = _now()
            break
    save_memory(workspace, memory)


def record_round_result(workspace: str, project: str, parsed: dict, raw_response: str = ""):
    """Summarize one project round into long-term memory."""
    if not project:
        return
    step_done = _clip(parsed.get("step_done", ""), 220)
    findings = [_clip(x, 180) for x in (parsed.get("findings") or []) if x]
    next_action = _clip(parsed.get("next_action", ""), 220)
    state_delta = _clip(parsed.get("state_delta", ""), 260)
    if not any([step_done, findings, next_action, state_delta]):
        return

    memory = load_memory(workspace)
    card = _project_card(memory, project)
    card["updated_at"] = _now()
    if step_done:
        card["latest_result"] = step_done
    if next_action:
        card["next_bets"] = _dedupe_recent([next_action] + card.get("next_bets", []), 8)

    episode = {
        "ts": _now(),
        "time": _now(),
        "project": project,
        "event": step_done,
        "evidence": "; ".join(findings[:2]),
        "lesson": (lesson or {}).get("content", ""),
        "risk": "bottleneck" if any(_looks_like_bottleneck(x) for x in findings + [step_done, state_delta]) else "",
        "links": [],
        "done": step_done,
        "findings": findings[:2],
        "next": next_action,
    }
    memory.setdefault("episodes", []).append(episode)
    _append_jsonl(_system_path(workspace, "hippocampus", "episodes.jsonl"), episode)
    save_memory(workspace, memory)


def record_episode(workspace: str, project: str, event: str, evidence: str = "",
                   lesson: str = "", risk: str = "", links: list[str] | None = None):
    row = {
        "ts": _now(),
        "time": _now(),
        "project": project or "",
        "event": _clip(event, 220),
        "evidence": _clip(evidence, 260),
        "lesson": _clip(lesson, 260),
        "risk": _clip(risk, 180),
        "links": links or [],
    }
    memory = load_memory(workspace)
    memory.setdefault("episodes", []).append(row)
    _append_jsonl(_system_path(workspace, "hippocampus", "episodes.jsonl"), row)
    save_memory(workspace, memory)


def record_growth_event(workspace: str, project: str, trigger: str, learned: str,
                        behavior_change: str, evidence: str = "",
                        category: str = "learning"):
    """Record a user-visible change in Partner's future behavior.

    Growth events are stricter than ordinary logs: they should represent a
    durable habit/boundary change, not routine progress.
    """
    if not any([trigger, learned, behavior_change]):
        return
    row = {
        "time": _now(),
        "project": project or "",
        "category": category or "learning",
        "trigger": _clip(trigger, 220),
        "learned": _clip(learned, 260),
        "behavior_change": _clip(behavior_change, 260),
        "evidence": _clip(evidence, 260),
        "status": "active",
    }
    memory = load_memory(workspace)
    memory.setdefault("growth_events", []).append(row)
    _append_jsonl(_system_path(workspace, "growth", "growth_events.jsonl"), row)
    _append_jsonl(_shared_path(workspace, "system", "growth_events.jsonl"), {
        **row,
        "instance": os.path.basename(os.path.normpath(workspace)),
    })
    shared_journal = _shared_path(workspace, "user", "shared_growth_journal.md")
    if not os.path.exists(shared_journal) or os.path.getsize(shared_journal) == 0:
        with open(shared_journal, "w", encoding="utf-8") as f:
            f.write("# Shared Partner Growth Journal\n\n")
    with open(shared_journal, "a", encoding="utf-8") as f:
        f.write(
            f"## {_now()} | {category or 'learning'}\n\n"
            f"- 实例：{os.path.basename(os.path.normpath(workspace))}\n"
            f"- 项目：{project or '通用'}\n"
            f"- 触发：{_clip(trigger, 180)}\n"
            f"- 学到：{_clip(learned, 220)}\n"
            f"- 以后改变：{_clip(behavior_change, 220)}\n"
            f"- 证据：{_clip(evidence, 220)}\n\n"
        )
    save_memory(workspace, memory)


def get_recent_growth_events(workspace: str, project: str = "", limit: int = 3) -> list[dict[str, Any]]:
    memory = load_memory(workspace)
    rows = []
    for row in reversed(memory.get("growth_events") or []):
        if project and row.get("project") and row.get("project") != project:
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def growth_context_for_report(workspace: str, project: str, max_chars: int = 360) -> str:
    rows = get_recent_growth_events(workspace, project=project, limit=2)
    if not rows:
        return ""
    lines = ["最近成长事件："]
    for row in rows:
        changed = row.get("behavior_change") or row.get("learned") or ""
        if changed:
            lines.append(f"- {_clip(changed, 150)}")
    return _clip_block("\n".join(lines), max_chars)


def record_risk_event(workspace: str, project: str, risk: str, evidence: str = "", severity: str = "medium"):
    row = {
        "time": _now(),
        "project": project,
        "risk": _clip(risk, 180),
        "evidence": _clip(evidence, 260),
        "severity": severity,
        "status": "open",
    }
    _append_jsonl(_system_path(workspace, "checks", "risk_events.jsonl"), row)


def scan_workspace_changes(workspace: str, project: str = "", max_files: int = 80) -> list[dict[str, Any]]:
    """Perception layer: notice changed project artifacts without reading them all.

    The manifest keeps prompt weight low. We only store file metadata changes as
    episodes so reflection/project loops can later decide whether to inspect them.
    """
    manifest_path = _state_path(workspace, "file_observation_manifest.json")
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        if not isinstance(manifest, dict):
            manifest = {}
    except Exception:
        manifest = {}

    roots = []
    projects_root = os.path.join(workspace, "20_records", "projects")
    if project:
        try:
            from .project_state import get_project_dir
            roots.append((project, get_project_dir(workspace, project)))
        except Exception:
            roots.append((project, os.path.join(projects_root, project)))
    elif os.path.isdir(projects_root):
        for name in os.listdir(projects_root):
            path = os.path.join(projects_root, name)
            if os.path.isdir(path):
                roots.append((name, path))

    changed: list[dict[str, Any]] = []
    suffixes = (".md", ".json", ".jsonl", ".csv", ".tsv", ".txt", ".log")
    for project_name, root in roots:
        if not os.path.isdir(root):
            continue
        seen = 0
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git", "venv", ".venv"}]
            for filename in files:
                if not filename.endswith(suffixes):
                    continue
                path = os.path.join(base, filename)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                rel = os.path.relpath(path, workspace)
                signature = f"{int(stat.st_mtime)}:{stat.st_size}"
                previous = manifest.get(rel)
                manifest[rel] = signature
                if previous and previous != signature:
                    changed.append({
                        "project": project_name,
                        "path": rel,
                        "size": stat.st_size,
                        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    })
                seen += 1
                if seen >= max_files:
                    break
            if seen >= max_files:
                break

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    for row in changed[:12]:
        record_episode(
            workspace,
            row["project"],
            event=f"检测到项目文件变化：{row['path']}",
            evidence=row["path"],
            lesson="",
            risk="",
        )
    return changed


def build_research_context(workspace: str, project: str, limit_chars: int = 760) -> str:
    """Return a compact, prompt-safe research memory slice."""
    memory = load_memory(workspace)
    card = (memory.get("projects") or {}).get(project, {})

    lines = []
    if card:
        lines.append("长期研究记忆：")
        latest = _clean_memory_text(card.get("latest_result", ""), 120)
        if latest:
            lines.append(f"- 最近有效结果：{latest}")
        for item in (card.get("bottlenecks") or [])[:1]:
            item = _clean_memory_text(item, 120)
            if item:
                lines.append(f"- 当前瓶颈：{item}")
        for item in (card.get("method_boundaries") or [])[:1]:
            item = _clean_memory_text(item, 120)
            if item:
                lines.append(f"- 方法边界：{item}")
        for item in (card.get("next_bets") or [])[:1]:
            item = _clean_memory_text(item, 120)
            if item:
                lines.append(f"- 候选突破口：{item}")

    # Project execution prompts should only receive concrete lessons from the
    # current project. Cross-project concrete cases easily pollute the task
    # context (for example, a nutrition lesson leaking into an age-prediction
    # project). Transferable experience is injected separately as abstract
    # habits by research_guardrails.
    related_lessons = [x for x in (memory.get("lessons") or []) if isinstance(x, dict) and x.get("project") == project][-3:][::-1]
    if related_lessons:
        if not lines:
            lines.append("长期研究记忆：")
        for lesson in related_lessons[:2]:
            label = lesson.get("type", "lesson")
            content = _clean_memory_text(lesson.get("content", ""), 130)
            if content:
                lines.append(f"- {label}：{content}")

    ideas = _related_items(memory.get("ideas") or [], project, 2)
    for idea in ideas[:1]:
        if idea.get("status") == "open":
            if not lines:
                lines.append("长期研究记忆：")
            content = _clean_memory_text(idea.get("content", ""), 130)
            if content:
                lines.append(f"- 用户/老师信号：{content}")

    text = "\n".join(lines).strip()
    if text == "长期研究记忆：":
        return ""
    return _clip_block(text, limit_chars) if text else ""


def consolidate_research_memory(workspace: str, project: str = ""):
    """Compact repeated memory rows and refresh the user-facing summary."""
    memory = load_memory(workspace)
    save_memory(workspace, memory)


def ensure_habits(workspace: str) -> dict[str, Any]:
    """Create/read the researcher habit policy."""
    path = _system_path(workspace, "habits", "habits.json")
    defaults = {
        "version": 1,
        "short_loop_interval_minutes": 30,
        "reflection_every_project_rounds": 8,
        "daily_reflection_interval_hours": 24,
        "cross_project_interval_hours": 24,
        "memory_consolidation_interval_hours": 6,
        "habits": [
            "每轮只做一个最小动作",
            "关键数字必须有 evidence 文件",
            "任何最佳结果/突破/完成结论先过 evidence check；证据不足只能写待复核，不能继续堆调参或包装汇报",
            "遇到新项目时由 LLM 判断是否需要先查文献/资料/公开数据路线；不机械搜索，也不跳过必要背景核验",
            "失败要记录条件和适用边界",
            "用户/老师灵感进入 idea_inbox",
            "用户分享内容先分项目指令/项目参考/普通学习/访问受限",
            "访问受限平台不绕过限制、不编造正文，转向截图/正文请求或公开替代来源",
            "数据/API/账号/预算缺失时记录 blocker，同时继续无阻塞分支",
            "完成项进入 done，不空转",
            "重复动作先去重再换突破口",
            "每个 event 结束后先对齐根目标：逐项核对用户要求的交付物、已完成内容、缺口和阻塞；不能把子步骤完成当成根目标完成",
            "科研相关分析完成后，如果已有可整理内容且用户没有反对，倾向继续调用 pdf_report，把阶段结果整理成 PDF 再结束",
            "生成图表/绘图时，PDF/报告正文按用户语言书写；只有嵌入图片内部的标题、节点、坐标轴、图例和注释默认使用英文，避免图片中文字因字体缺失不可见",
            "报告/演示/说明如果基于前序图片、图表、架构图或可视化产物，PDF 必须嵌入真实图片文件，不能只写文件名、路径或文字说明",
            "pdf_report event 已负责生成真实 PDF；除非 PDF 生成失败，不要再把 Markdown 转 PDF 当成下一步 artifact_build",
        ],
    }
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                merged = {**defaults, **data}
                existing = [str(x) for x in (data.get("habits") or [])]
                merged["habits"] = existing[:]
                for item in defaults["habits"]:
                    if item not in merged["habits"]:
                        merged["habits"].append(item)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(merged, f, ensure_ascii=False, indent=2)
                return merged
        except Exception:
            pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(defaults, f, ensure_ascii=False, indent=2)
    return defaults


def _last_run_state(workspace: str) -> dict[str, Any]:
    path = _state_path(workspace, "research_habits_state.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_last_run_state(workspace: str, state: dict[str, Any]):
    path = _state_path(workspace, "research_habits_state.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def should_run_periodic(workspace: str, key: str, interval_hours: float) -> bool:
    state = _last_run_state(workspace)
    last = state.get(key)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
        delta = datetime.now() - last_dt
        return delta.total_seconds() >= interval_hours * 3600
    except Exception:
        return True


def mark_periodic_run(workspace: str, key: str):
    state = _last_run_state(workspace)
    state[key] = _now()
    _save_last_run_state(workspace, state)


def build_reflection_context(workspace: str, limit_chars: int = 6000) -> str:
    memory = load_memory(workspace)
    lines = ["# 独立反思上下文"]
    for project, card in (memory.get("projects") or {}).items():
        lines.append(f"## {project}")
        if card.get("latest_result"):
            lines.append(f"- latest: {_clip(card['latest_result'], 180)}")
        for item in (card.get("bottlenecks") or [])[:3]:
            lines.append(f"- bottleneck: {_clip(item, 180)}")
        for item in (card.get("method_boundaries") or [])[:3]:
            lines.append(f"- boundary: {_clip(item, 180)}")
        for item in (card.get("next_bets") or [])[:2]:
            lines.append(f"- next_bet: {_clip(item, 180)}")
    lessons = memory.get("lessons") or []
    if lessons:
        lines.append("## recent lessons")
        for lesson in _compact_lessons_for_prompt(lessons, limit=6):
            lines.append(f"- [{lesson.get('project','')}] {lesson.get('type','')}: {_clip(lesson.get('content',''), 180)}")
    ideas = [x for x in (memory.get("ideas") or []) if str(x.get("status", "")).lower() in OPEN_IDEA_STATUSES]
    if ideas:
        lines.append("## open ideas")
        for idea in _compact_ideas_for_prompt(ideas, limit=4):
            lines.append(f"- [{idea.get('project','')}] {_clip(idea.get('content',''), 220)}")
    return _clip_block("\n".join(lines), limit_chars)


def write_reflection_artifacts(workspace: str, content: str, kind: str = "daily_reflection") -> str:
    date = datetime.now().strftime("%Y%m%d")
    path = _system_path(workspace, "reflections", f"{kind}_{date}.md")
    with open(path, "a", encoding="utf-8") as f:
        if os.path.getsize(path) == 0:
            f.write(f"# {kind} {date}\n\n")
        f.write(content.strip() + "\n\n")
    return path


def append_strategy_memory(workspace: str, content: str):
    path = _system_path(workspace, "strategy", "strategy_memory.md")
    needs_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", encoding="utf-8") as f:
        if needs_header:
            f.write("# Partner 策略记忆\n\n")
        f.write(f"## {datetime.now().isoformat(timespec='seconds')}\n{content.strip()}\n\n")


def build_cross_project_context(workspace: str, limit_chars: int = 6000) -> str:
    memory = load_memory(workspace)
    lines = ["# 跨项目迁移上下文"]
    for lesson in _compact_lessons_for_prompt(memory.get("lessons") or [], limit=12):
        content = lesson.get("content", "")
        if lesson.get("transferable"):
            lines.append(f"- [{lesson.get('project','')}] {lesson.get('type','')}: {_clip(content, 220)}")
    if len(lines) == 1:
        for lesson in _compact_lessons_for_prompt(memory.get("lessons") or [], limit=8):
            lines.append(f"- [{lesson.get('project','')}] {lesson.get('type','')}: {_clip(lesson.get('content',''), 180)}")
    return _clip_block("\n".join(lines), limit_chars)


def _prompt_dedupe_key(text: str) -> str:
    text = _collapse_repeated_tokens(text or "").lower()
    text = re.sub(r"step\s*\d+", "step_n", text, flags=re.I)
    text = re.sub(r"第[一二三四五六七八九十百\d]+轮", "第n轮", text)
    text = re.sub(r"round\s*\d+", "round_n", text, flags=re.I)
    text = re.sub(r"reflection_note(?:_final\d*)?\.md", "reflection_note.md", text, flags=re.I)
    text = re.sub(r"\d+(?:\.\d+)?", "n", text)
    text = re.sub(r"\s+", "", text)
    return text[:180]


def _compact_lessons_for_prompt(lessons: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    out = []
    seen = set()
    for lesson in reversed(lessons or []):
        if not isinstance(lesson, dict):
            continue
        content = _clip(str(lesson.get("content", "")), 320)
        if not content:
            continue
        key = (lesson.get("project", ""), lesson.get("type", ""), _prompt_dedupe_key(content))
        if key in seen:
            continue
        seen.add(key)
        row = dict(lesson)
        row["content"] = content
        out.append(row)
        if len(out) >= limit:
            break
    return list(reversed(out))


def _compact_ideas_for_prompt(ideas: list[dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    out = []
    seen = set()
    for idea in reversed(ideas or []):
        if not isinstance(idea, dict):
            continue
        content = _clip(str(idea.get("content") or idea.get("idea") or ""), 260)
        if not content:
            continue
        key = (idea.get("project", ""), _prompt_dedupe_key(content))
        if key in seen:
            continue
        seen.add(key)
        row = dict(idea)
        row["content"] = content
        out.append(row)
        if len(out) >= limit:
            break
    return list(reversed(out))


def maybe_reflection_objective(workspace: str, project: str, step: int) -> tuple[str, str]:
    """Occasionally schedule researcher-style reflection instead of another routine step."""
    try:
        from .stage_report import maybe_stage_report_objective

        report_objective, report_path = maybe_stage_report_objective(workspace, project, step)
        if report_objective:
            return report_objective, report_path
    except Exception:
        pass

    if step <= 0 or step % 8 != 0:
        return "", ""
    from .project_state import get_project_dir

    project_dir = get_project_dir(workspace, project)
    path = os.path.join(project_dir, "reflection_note.md")
    return (
        "做一次短反思而不是展开新任务：基于最近状态和长期研究记忆，写清一个失败经验、一个仍值得迁移的方法、"
        "一个下一轮最小突破口。不要问用户，不要泛泛总结。",
        path,
    )


def _related_items(items: list[dict[str, Any]], project: str, limit: int) -> list[dict[str, Any]]:
    exact = [x for x in items if x.get("project") == project]
    cross = [x for x in items if x.get("project") and x.get("project") != project and x.get("transferable")]
    generic = [x for x in items if not x.get("project")]
    return (exact + cross + generic)[-limit:][::-1]


def _dedupe_recent(items: list[str], limit: int) -> list[str]:
    seen = set()
    out = []
    for item in items:
        key = _clip(item, 120)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
        if len(out) >= limit:
            break
    return out


def _looks_like_bottleneck(text: str) -> bool:
    return False


def _looks_like_method_boundary(text: str) -> bool:
    return False


def _lesson_from_round(project: str, done: str, findings: list[str], next_action: str,
                       state_delta: str, raw_response: str) -> dict[str, Any] | None:
    return None


def _append_method_memory(workspace: str, lesson: dict[str, Any]):
    content = lesson.get("content", "")
    row = {
        "time": lesson.get("ts") or _now(),
        "method": _infer_method_name(content),
        "project": lesson.get("project", ""),
        "result": _lesson_result(lesson.get("type", ""), content),
        "conditions": _clip(content, 220),
        "failed_because": _clip(content, 220) if lesson.get("type") in ("pitfall", "method_boundary", "user_method_boundary") else "",
        "may_work_when": "其他项目条件不同、数据规模/标签/约束更匹配时可重新评估" if lesson.get("transferable") else "",
        "related_projects": [],
        "evidence": [],
        "confidence": "medium",
    }
    _append_jsonl(_system_path(workspace, "synapses", "method_memory.jsonl"), row)
    _update_cross_project_lessons(workspace, row)


def _update_cross_project_lessons(workspace: str, row: dict[str, Any]):
    path = _system_path(workspace, "synapses", "cross_project_lessons.md")
    line = (
        f"- [{row.get('time', '')}] {row.get('method') or '未命名方法'} "
        f"在 {row.get('project') or '未指定项目'} 中为 {row.get('result')}；"
        f"边界：{row.get('conditions', '')}"
    )
    if row.get("may_work_when"):
        line += f"；可能适用：{row['may_work_when']}"
    needs_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", encoding="utf-8") as f:
        if needs_header:
            f.write("# 跨项目经验\n\n")
        f.write(line + "\n")


def _infer_method_name(text: str) -> str:
    return "未命名方法"


def _lesson_result(kind: str, content: str) -> str:
    if kind == "next_bet":
        return "unknown"
    return "partial"


def _idea_source(kind: str) -> str:
    return {
        "teacher_advice": "teacher",
        "user_idea": "user",
        "correction_signal": "user",
        "risk_signal": "user",
    }.get(kind, kind or "user")


def _write_user_summary(workspace: str, memory: dict[str, Any]):
    user_dir = os.path.join(workspace, "user")
    os.makedirs(user_dir, exist_ok=True)
    path = os.path.join(user_dir, "research_memory_summary.md")
    lines = ["# Partner 长期研究记忆", ""]
    lines.append("这份文件是给用户看的摘要；运行时使用的是 `state/research_memory.json`。")
    lines.append("")

    projects = memory.get("projects") or {}
    if projects:
        lines.append("## 项目卡片")
        for name, card in sorted(projects.items(), key=lambda item: item[1].get("updated_at", ""), reverse=True)[:6]:
            lines.append(f"### {name}")
            if card.get("latest_result"):
                lines.append(f"- 最近有效结果：{card['latest_result']}")
            for item in (card.get("bottlenecks") or [])[:2]:
                lines.append(f"- 当前瓶颈：{item}")
            for item in (card.get("method_boundaries") or [])[:2]:
                lines.append(f"- 方法边界：{item}")
            for item in (card.get("next_bets") or [])[:2]:
                lines.append(f"- 候选突破口：{item}")
            lines.append("")

    lessons = memory.get("lessons") or []
    if lessons:
        lines.append("## 最近经验")
        for lesson in lessons[-8:][::-1]:
            lines.append(f"- [{lesson.get('project', '通用')}] {lesson.get('type', 'lesson')}：{lesson.get('content', '')}")
        lines.append("")

    ideas = [x for x in (memory.get("ideas") or []) if x.get("status") == "open"]
    if ideas:
        lines.append("## 未消化的用户/老师信号")
        for idea in ideas[-8:][::-1]:
            lines.append(f"- [{idea.get('project', '未指定')}] {idea.get('kind', 'idea')}：{idea.get('content', '')}")
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")


def _write_growth_summary(workspace: str, memory: dict[str, Any]):
    user_dir = os.path.join(workspace, "user")
    os.makedirs(user_dir, exist_ok=True)
    path = os.path.join(user_dir, "partner_growth.md")
    rows = memory.get("growth_events") or []
    lines = [
        "# Partner 成长记录",
        "",
        "这份文件只记录会改变 Partner 后续行为的经验，不记录普通项目流水账。",
        "",
    ]
    if not rows:
        lines.extend([
            "## 当前状态",
            "还没有形成明确成长事件。用户纠偏、风险提醒、失败复盘或质量审计会写到这里。",
            "",
        ])
    else:
        by_project: dict[str, list[dict[str, Any]]] = {}
        for row in rows[-40:]:
            by_project.setdefault(row.get("project") or "未指定项目", []).append(row)
        for project, project_rows in sorted(
            by_project.items(),
            key=lambda item: item[1][-1].get("time", ""),
            reverse=True,
        ):
            lines.append(f"## {project}")
            for row in project_rows[-6:][::-1]:
                lines.append(f"### {row.get('time', '')} · {row.get('category', 'learning')}")
                if row.get("trigger"):
                    lines.append(f"- 触发：{row['trigger']}")
                if row.get("learned"):
                    lines.append(f"- 学到：{row['learned']}")
                if row.get("behavior_change"):
                    lines.append(f"- 以后会：{row['behavior_change']}")
                if row.get("evidence"):
                    lines.append(f"- 证据：{row['evidence']}")
                lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")

"""User-facing showcase builder.

Partner's runtime writes many small records while it works.  That is useful for
recovery, but it is a poor demo surface.  This module creates a compact,
evidence-oriented folder under ``user/showcase/`` so a user can inspect what the
Partner actually did, how it changed its mind, and which claims are backed by
files.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


MAX_SOURCE_CHARS = 12_000
MAX_DIALOGUE_LINES = 80
MAX_EVIDENCE_ITEMS = 12


@dataclass
class SourceDoc:
    label: str
    path: Path
    text: str


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", name or "project")
    return name.strip("_") or "project"


def _read_text(path: Path, limit: int = MAX_SOURCE_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    return text[:limit]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _first_existing(paths: Iterable[Path]) -> Path | None:
    for path in paths:
        if path.exists() and path.is_file():
            return path
    return None


def _active_project(workspace: Path) -> str:
    candidates = [
        workspace / "20_records" / "active_project.txt",
        workspace / "state" / "active_project.txt",
    ]
    path = _first_existing(candidates)
    if path:
        text = _read_text(path, 300).strip()
        if text:
            return text.splitlines()[0].strip()
    plan_path = workspace / "state" / "active_plan.json"
    try:
        plan = json.loads(_read_text(plan_path, 3000))
        title = str(plan.get("title") or plan.get("goal") or "").strip()
        if title:
            return title[:80]
    except Exception:
        pass
    return "current_project"


def _project_dirs(workspace: Path, project: str) -> list[Path]:
    slug = _safe_name(project)
    candidates = [
        workspace / "20_records" / "projects" / project,
        workspace / "20_records" / "projects" / slug,
        workspace / "projects" / project,
        workspace / "projects" / slug,
        workspace / "user" / "projects" / project,
        workspace / "user" / "projects" / slug,
        workspace / "user" / "current_project",
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if path.exists() and key not in seen:
            out.append(path)
            seen.add(key)
    return out


def _collect_sources(workspace: Path, project: str) -> list[SourceDoc]:
    filenames = [
        "project_brief.md",
        "summary.md",
        "state.md",
        "exploration_log.md",
        "research_journey.md",
        "reflection_log.md",
        "growth_journal.md",
        "insight_log.md",
        "habit_applications.md",
        "breakthrough_queue.md",
        "breakthrough_execution.md",
        "progress_quality_audit.md",
        "trace_detail.md",
        "latest_stage_report.md",
    ]
    sources: list[SourceDoc] = []
    seen: set[Path] = set()
    for root in _project_dirs(workspace, project):
        for filename in filenames:
            path = root / filename
            if path.exists() and path.is_file() and path.resolve() not in seen:
                text = _read_text(path)
                if text.strip():
                    sources.append(SourceDoc(filename, path, text))
                    seen.add(path.resolve())

    extra_roots = [
        workspace / "user" / "partner_mind",
        workspace / "user" / "reports" / _safe_name(project),
        workspace / "state",
    ]
    for root in extra_roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("*.md"))[:12]:
            if path.resolve() in seen:
                continue
            text = _read_text(path)
            if text.strip():
                sources.append(SourceDoc(path.name, path, text))
                seen.add(path.resolve())
    return sources


def _collect_dialogue(workspace: Path) -> list[str]:
    roots = [workspace / "dialogue", workspace / "logs"]
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(sorted(root.rglob("*.log"), key=lambda p: p.stat().st_mtime))
            files.extend(sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime))
    lines: list[str] = []
    for path in files[-8:]:
        text = _read_text(path, 50_000)
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if "思考中" in line:
                continue
            if re.search(r"(ZLL|partner\d+|用户|user|assistant|qq)", line, re.I):
                lines.append(line[:500])
    return lines[-MAX_DIALOGUE_LINES:]


def _sentences(text: str) -> list[str]:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    chunks = re.split(r"(?<=[。！？.!?])\s+|\n+", text)
    out = []
    for chunk in chunks:
        cleaned = re.sub(r"\s+", " ", chunk).strip(" -*#\t")
        if len(cleaned) >= 12:
            out.append(cleaned[:260])
    return out


def _find_evidence(sources: list[SourceDoc], keywords: list[str], limit: int = 6) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for doc in sources:
        for sent in _sentences(doc.text):
            if any(k.lower() in sent.lower() for k in keywords):
                item = f"- {sent}（证据：`{doc.path.name}`）"
                if item not in seen:
                    out.append(item)
                    seen.add(item)
            if len(out) >= limit:
                return out
    return out


def _claim_level(text: str) -> str:
    lower = (text or "").lower()
    if re.search(r"manual simulation|simulation|synthetic|mock|dry-run|模拟|合成|代理分数", lower):
        return "SIMULATION"
    if re.search(r"real api|真实\s*api|真实实验|真实数据|real-world", lower):
        return "REAL_OR_REAL_REQUIRED"
    if re.search(r"blocked|blocker|无法|缺少|需要 api|需要.*预算|不可达|受限", lower):
        return "BLOCKED"
    if re.search(r"hypothesis|inferred|推断|假设|预计|可能", lower):
        return "INFERRED"
    if re.search(r"pass|passed|通过|verified|审计|测试|运行|执行|输出", lower):
        return "VERIFIED_LOCAL"
    return "UNKNOWN"


def _level_badge(level: str) -> str:
    return {
        "REAL_OR_REAL_REQUIRED": "REAL/NEEDS_REAL",
        "SIMULATION": "SIMULATION",
        "BLOCKED": "BLOCKED",
        "INFERRED": "INFERRED",
        "VERIFIED_LOCAL": "LOCAL_VERIFIED",
        "UNKNOWN": "UNKNOWN",
    }.get(level, level)


def _distilled_evidence(sources: list[SourceDoc]) -> str:
    """Create a small, readable evidence table instead of exposing all logs."""
    candidates: list[tuple[int, str, SourceDoc, str]] = []
    priorities = [
        (6, ["核心结论", "关键结论", "结论", "breakthrough", "突破"]),
        (5, ["修正", "不可信", "不能", "边界", "风险", "泄露", "bias"]),
        (5, ["运行", "执行", "测试", "pass", "passed", "输出", "reproducible"]),
        (4, ["学到", "习惯", "反思", "以后", "行为改变"]),
        (3, ["下一步", "blocked", "blocker", "需要 api", "预算"]),
    ]
    seen: set[str] = set()
    for doc in sources:
        for sent in _sentences(doc.text):
            score = 0
            for weight, keys in priorities:
                if any(k.lower() in sent.lower() for k in keys):
                    score += weight
            if score <= 0:
                continue
            sig = re.sub(r"\d+", "<n>", sent.lower())[:140]
            if sig in seen:
                continue
            seen.add(sig)
            candidates.append((score, sent, doc, _claim_level(sent + "\n" + doc.text[:800])))
    candidates.sort(key=lambda x: (-x[0], x[2].path.name, x[1]))
    rows = [
        "# Distilled Evidence",
        "",
        "只保留最适合给用户/老师看的核心证据；完整来源见 `source_index.md`。",
        "",
        "| 证据等级 | 核心证据 | 来源 | 能证明什么 | 不能证明什么 |",
        "|---|---|---|---|---|",
    ]
    for _, sent, doc, level in candidates[:MAX_EVIDENCE_ITEMS]:
        proof = "局部代码/文档/审计链路存在" if level == "VERIFIED_LOCAL" else "阶段性判断或限制被记录"
        if level == "SIMULATION":
            proof = "simulation 阶段的假设或离线结果"
        elif level == "BLOCKED":
            proof = "真实推进所需外部资源或访问条件"
        elif level == "INFERRED":
            proof = "基于已有材料的推断"
        cannot = "不能当作真实 API 实证" if level in {"SIMULATION", "INFERRED"} else "不能证明整体项目已经完成"
        rows.append(
            f"| {_level_badge(level)} | {sent[:180]} | `{doc.path.name}` | {proof} | {cannot} |"
        )
    if len(rows) <= 5:
        rows.append("| UNKNOWN | 当前没有足够精选证据 | - | - | 需要继续运行或人工补充证据 |")
    return "\n".join(rows) + "\n"


def _run_quick_python_check(path: Path, cwd: Path) -> tuple[str, str]:
    """Best-effort safe syntax check for generated Python files."""
    try:
        result = subprocess.run(
            ["python3", "-m", "py_compile", str(path)],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        status = "syntax_ok" if result.returncode == 0 else "syntax_error"
        detail = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or [""]
        return status, detail[0][:180]
    except Exception as exc:
        return "check_failed", str(exc)[:180]


def _execution_evidence(workspace: Path, project: str) -> str:
    roots = _project_dirs(workspace, project)
    code_files: list[Path] = []
    result_files: list[Path] = []
    for root in roots:
        code_files.extend(sorted(root.rglob("*.py"))[:120])
        code_files.extend(sorted(root.rglob("*.sh"))[:40])
        for pattern in ("*.json", "*.jsonl", "*.csv", "*.tsv", "*.out", "*.log"):
            result_files.extend(sorted(root.rglob(pattern))[:80])
    code_files = sorted({p.resolve(): p for p in code_files}.values(), key=lambda p: str(p))[:160]
    result_files = sorted({p.resolve(): p for p in result_files}.values(), key=lambda p: str(p))[:120]

    command_hits: list[str] = []
    pass_hits: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.md"))[:120]:
            text = _read_text(path, 5000)
            for line in text.splitlines():
                if re.search(r"\bpython3?\s+[\w./-]+\.py|bash\s+[\w./-]+\.sh", line):
                    command_hits.append(f"`{path.name}`: {line.strip()[:180]}")
                if re.search(r"\bPASS\b|\bpassed\b|全部通过|测试通过|returncode\s*[:=]\s*0", line, re.I):
                    pass_hits.append(f"`{path.name}`: {line.strip()[:180]}")
    checks: list[str] = []
    for path in code_files[:20]:
        if path.suffix == ".py":
            status, detail = _run_quick_python_check(path, path.parent)
            checks.append(f"- `{path.relative_to(path.parents[0]) if False else path.name}`：{status}" + (f"；{detail}" if detail else ""))

    rows = [
        "# Execution Evidence",
        "",
        "这份文件回答：Partner 是否写了代码、是否留下执行/输出证据。语法检查由 showcase 构建时本地快速执行；不代表真实实验已经跑完。",
        "",
        "## Code Files",
        f"- 发现代码文件：{len(code_files)} 个（仅列前 60 个）。",
    ]
    rows.extend(f"- `{p}`" for p in code_files[:60])
    rows.extend([
        "",
        "## Output / Result Files",
        f"- 发现结果/日志类文件：{len(result_files)} 个（仅列前 60 个）。",
    ])
    rows.extend(f"- `{p}`" for p in result_files[:60])
    rows.extend([
        "",
        "## Commands Mentioned In Records",
    ])
    rows.extend(f"- {x}" for x in command_hits[:40]) if command_hits else rows.append("- 未找到明确命令记录。")
    rows.extend([
        "",
        "## PASS / Execution Result Mentions",
    ])
    rows.extend(f"- {x}" for x in pass_hits[:40]) if pass_hits else rows.append("- 未找到明确 PASS/执行结果记录。")
    rows.extend([
        "",
        "## Quick Syntax Check",
    ])
    rows.extend(checks or ["- 没有可检查的 Python 文件。"])
    rows.extend([
        "",
        "## Verdict",
        "",
        "- 如果只有代码文件，没有输出文件/命令/PASS 记录：只能说明写了代码，不能说明执行过。",
        "- 如果有 `reproducible_output/`、结果 JSON、PASS 记录：可以说明至少有离线/mock/simulation 执行证据。",
        "- 如果没有真实 API trace、真实模型响应、真实账单/请求日志：不能说明真实 API 实验已经完成。",
    ])
    return "\n".join(rows) + "\n"


def _ability_matrix(sources: list[SourceDoc]) -> str:
    abilities = [
        ("逻辑推理", ["因为", "所以", "推断", "结论", "原因", "假设", "机制"]),
        ("迭代学习", ["下一轮", "修正", "复盘", "迭代", "重新", "对照", "失败"]),
        ("项目推进", ["完成", "验证", "实验", "运行", "产出", "生成", "下载", "实现"]),
        ("经验学习", ["学到", "经验", "习惯", "以后", "边界", "审计", "泄露", "幻觉"]),
        ("自我进化", ["成长", "反思", "自进化", "行为改变", "mind", "海马", "突触"]),
        ("外部内容学习", ["公众号", "小红书", "B站", "视频", "论文", "文献", "网页", "链接"]),
        ("用户协作", ["用户", "老师", "纠正", "提醒", "分享", "反馈", "要求"]),
    ]
    rows = [
        "| 能力 | 当前证据 | 展示判断 |",
        "|---|---|---|",
    ]
    for ability, keys in abilities:
        ev = _find_evidence(sources, keys, limit=2)
        if ev:
            evidence = "<br>".join(x.removeprefix("- ") for x in ev)
            judgment = "已有可展示证据"
        else:
            evidence = "未在当前 workspace 中找到明确证据"
            judgment = "需要后续 demo 补强"
        rows.append(f"| {ability} | {evidence} | {judgment} |")
    return "\n".join(rows)


def _timeline(sources: list[SourceDoc]) -> str:
    items: list[tuple[str, str, str]] = []
    for doc in sources:
        for line in doc.text.splitlines():
            m = re.search(r"(20\d{2}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2})?)", line)
            if m:
                cleaned = re.sub(r"\s+", " ", line).strip(" -*#")
                if len(cleaned) > 12:
                    items.append((m.group(1), doc.path.name, cleaned[:240]))
    if not items:
        for doc in sources[:8]:
            try:
                ts = datetime.fromtimestamp(doc.path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            except Exception:
                ts = ""
            first = next(iter(_sentences(doc.text)), "")
            if first:
                items.append((ts, doc.path.name, first))
    rows = ["# Demo Timeline\n"]
    for ts, name, text in items[-30:]:
        rows.append(f"- **{ts}** `{name}`：{text}")
    return "\n".join(rows)


def _story(project: str, sources: list[SourceDoc]) -> str:
    conclusions = _find_evidence(sources, ["结论", "发现", "验证", "完成", "突破"], 5)
    risks = _find_evidence(sources, ["风险", "泄露", "过拟合", "幻觉", "不可复现", "失败"], 5)
    growth = _find_evidence(sources, ["学到", "习惯", "反思", "行为改变", "以后"], 5)
    nexts = _find_evidence(sources, ["下一步", "继续", "计划", "后续"], 5)

    def section(title: str, lines: list[str], fallback: str) -> str:
        return f"## {title}\n\n" + ("\n".join(lines) if lines else fallback) + "\n"

    return "\n".join(
        [
            f"# {project} Demo Story",
            "",
            "这份材料只整理当前 workspace 中已有证据，不重新调用 LLM 生成新结论。",
            "",
            section("1. 这轮真正做成了什么", conclusions, "当前还缺少明确的成果证据，需要继续跑出更硬的实验、报告或可验证产物。"),
            section("2. 它如何发现风险或修正方向", risks, "当前还缺少明确的风险审计记录，demo 中需要补强这一点。"),
            section("3. 它学到了什么习惯", growth, "当前还缺少可读的成长记录，需要让后续运行把经验写成用户可见的成长日志。"),
            section("4. 下一步能自然接着做什么", nexts, "当前还缺少具体下一步，应要求 Partner 自己生成可执行小任务。"),
        ]
    )


def _qq_conversation(lines: list[str]) -> str:
    body = "\n".join(f"- {line}" for line in lines) if lines else "当前没有找到可展示的 QQ 对话片段。"
    return "# QQ Conversation Excerpts\n\n" + body + "\n"


def _limitations(sources: list[SourceDoc]) -> str:
    gaps = _find_evidence(
        sources,
        ["无法", "没有", "缺少", "受限", "API", "登录", "blocked", "unknown", "失败"],
        12,
    )
    if not gaps:
        gaps = ["- 当前 showcase 没有发现显式限制记录；这不等于没有限制，只说明运行日志还没有把限制写清楚。"]
    return "# Limitations And Risks\n\n" + "\n".join(gaps) + "\n"


def _reasoning_chain(sources: list[SourceDoc]) -> str:
    hypotheses = _find_evidence(sources, ["假设", "判断", "认为", "可能", "hypothesis"], 6)
    actions = _find_evidence(sources, ["实验", "验证", "运行", "实现", "构造", "审计", "对照"], 6)
    results = _find_evidence(sources, ["结果", "发现", "准确率", "MAE", "passed", "failed", "提升", "下降"], 6)
    corrections = _find_evidence(sources, ["修正", "泄露", "不可靠", "推翻", "风险", "失败", "边界"], 6)
    habits = _find_evidence(sources, ["习惯", "以后", "行为改变", "学到", "沉淀", "复用"], 6)

    def section(title: str, rows: list[str], fallback: str) -> str:
        return f"## {title}\n\n" + ("\n".join(rows) if rows else fallback) + "\n"

    return "\n".join(
        [
            "# Reasoning Chain",
            "",
            "这份文件把 demo 证据整理成“假设 -> 行动 -> 结果 -> 修正 -> 习惯”的链条。",
            "",
            section("1. 初始判断 / 假设", hypotheses, "当前证据中还没有清晰的初始假设记录。"),
            section("2. 自主行动 / 验证", actions, "当前证据中还没有清晰的执行验证记录。"),
            section("3. 结果 / 观察", results, "当前证据中还没有清晰的结果记录。"),
            section("4. 风险 / 修正", corrections, "当前证据中还没有清晰的修正记录。"),
            section("5. 沉淀成习惯", habits, "当前证据中还没有清晰的习惯沉淀记录。"),
        ]
    )


def _before_after(project: str, sources: list[SourceDoc]) -> str:
    start = _find_evidence(sources, ["目标", "开始", "初始", "问题", "用户要求", "当前项目"], 5)
    current = _find_evidence(sources, ["当前", "完成", "结果", "最新", "结论", "下一步"], 8)
    risks = _find_evidence(sources, ["限制", "风险", "blocked", "缺少", "无法", "未完成"], 5)
    return "\n".join(
        [
            f"# Before / After: {project}",
            "",
            "## Before",
            "",
            "\n".join(start) if start else "当前 workspace 还没有足够的初始状态证据。",
            "",
            "## After",
            "",
            "\n".join(current) if current else "当前 workspace 还没有足够的阶段结果证据。",
            "",
            "## Still Missing",
            "",
            "\n".join(risks) if risks else "当前没有显式记录未完成项；后续应继续补充限制和风险。",
            "",
        ]
    )


def _experience_cards(sources: list[SourceDoc]) -> str:
    candidates = _find_evidence(
        sources,
        ["用户", "纠正", "学到", "习惯", "以后", "行为改变", "风险", "泄露", "API", "捷径", "模拟"],
        16,
    )
    if not candidates:
        return "# Experience Cards\n\n当前没有找到足够清晰的经验卡片证据。\n"
    blocks = ["# Experience Cards\n", "每张卡只记录能迁移到后续项目的习惯，不把普通文件操作当成长。\n"]
    for idx, item in enumerate(candidates[:10], start=1):
        text = item.removeprefix("- ").strip()
        level = _level_badge(_claim_level(text))
        suggested_change = "下次遇到类似信号时先审计证据等级，再决定是否汇报或继续扩展。"
        if "api" in text.lower() or "预算" in text or "blocked" in text.lower():
            suggested_change = "先明确告诉用户需要什么资源；用户不回复时只做无 API 的准备、审计或对照，不声称完成。"
        elif "simulation" in text.lower() or "模拟" in text:
            suggested_change = "所有 simulation 结果必须降级为假设，并写清楚真实实验还缺什么。"
        elif "泄露" in text or "不可信" in text or "风险" in text:
            suggested_change = "异常好或关键结论先做泄露/偏差/可复现审计，再更新最佳结论。"
        blocks.extend(
            [
                f"## Card {idx}",
                "",
                f"- 证据等级：{level}",
                f"- 触发线索：{text}",
                f"- 行为改变：{suggested_change}",
                "- 复用状态：后续轮次需要在 `research_journey.md` 或 `partner_evolution.md` 中记录是否复用成功。",
                "",
            ]
        )
    return "\n".join(blocks)


def _key_artifacts(copied: list[str], sources: list[SourceDoc]) -> str:
    rows = ["# Key Artifacts\n"]
    if copied:
        rows.append("## Copied User-Facing Artifacts\n")
        rows.extend(f"- `artifacts/{name}`" for name in copied)
    else:
        rows.append("## Copied User-Facing Artifacts\n\n- 暂无 PPT/PDF/报告类产物。")
    rows.append("\n## Important Source Records\n")
    for doc in sources[:20]:
        rows.append(f"- `{doc.path}`")
    return "\n".join(rows) + "\n"


def _reproduce(workspace: Path, project: str) -> str:
    return "\n".join(
        [
            "# Reproduce This Showcase",
            "",
            "```bash",
            f"partner showcase build --workspace {workspace} --project \"{project}\"",
            "```",
            "",
            "如果要重新跑 demo，建议先备份并清空对应实例 workspace，只保留 `00_config/qq_config.json` 和必要的 `partner_config.json`。",
            "",
        ]
    )


def _copy_key_artifacts(workspace: Path, project: str, out_dir: Path) -> list[str]:
    copied: list[str] = []
    artifact_dir = out_dir / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    patterns = ["*.pdf", "*.pptx", "*.docx", "*report*.md", "*stage_report*.md", "*audit*.md"]
    for root in _project_dirs(workspace, project) + [workspace / "user" / "reports" / _safe_name(project)]:
        if not root.exists():
            continue
        for pattern in patterns:
            for path in sorted(root.rglob(pattern))[:20]:
                if not path.is_file():
                    continue
                dest = artifact_dir / path.name
                if dest.exists():
                    dest = artifact_dir / f"{_safe_name(path.parent.name)}_{path.name}"
                try:
                    shutil.copy2(path, dest)
                    copied.append(dest.name)
                except Exception:
                    pass
    return copied[:40]


def build_showcase(workspace: str, project: str | None = None, output: str | None = None) -> Path:
    """Build a compact user-facing demo folder and return its path."""
    ws = Path(workspace).expanduser().resolve()
    project_name = (project or _active_project(ws)).strip() or "current_project"
    out_dir = Path(output).expanduser().resolve() if output else ws / "user" / "showcase" / _safe_name(project_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    sources = _collect_sources(ws, project_name)
    dialogue = _collect_dialogue(ws)
    copied = _copy_key_artifacts(ws, project_name, out_dir)

    _write(out_dir / "story.md", _story(project_name, sources))
    _write(out_dir / "distilled_evidence.md", _distilled_evidence(sources))
    _write(out_dir / "execution_evidence.md", _execution_evidence(ws, project_name))
    _write(out_dir / "timeline.md", _timeline(sources))
    _write(out_dir / "qq_conversation.md", _qq_conversation(dialogue))
    _write(out_dir / "ability_evidence_matrix.md", "# Ability Evidence Matrix\n\n" + _ability_matrix(sources) + "\n")
    _write(out_dir / "reasoning_chain.md", _reasoning_chain(sources))
    _write(out_dir / "before_after.md", _before_after(project_name, sources))
    _write(out_dir / "experience_cards.md", _experience_cards(sources))
    _write(out_dir / "key_artifacts.md", _key_artifacts(copied, sources))
    _write(out_dir / "limitations.md", _limitations(sources))
    _write(out_dir / "reproduce.md", _reproduce(ws, project_name))
    _write(
        out_dir / "source_index.md",
        "# Source Index\n\n"
        + "\n".join(f"- `{doc.path}` ({len(doc.text)} chars)" for doc in sources)
        + ("\n\n## Copied Artifacts\n\n" + "\n".join(f"- `artifacts/{name}`" for name in copied) if copied else "\n\n## Copied Artifacts\n\n- 暂无。")
        + "\n",
    )
    _write(
        out_dir / "README.md",
        "\n".join(
            [
                f"# {project_name} Showcase",
                "",
                "这是 Partner 自动整理的 demo 入口，面向用户、老师或评审阅读。",
                "",
                "建议阅读顺序：",
                "",
                "1. `story.md`：一页看懂这轮做了什么、学到了什么、还有什么风险。",
                "2. `distilled_evidence.md`：查看 8-12 条精选证据及 REAL/SIMULATION/INFERRED/BLOCKED 标签。",
                "3. `execution_evidence.md`：查看它是否写了代码、是否有执行/输出证据。",
                "4. `reasoning_chain.md`：查看假设、行动、结果、修正、习惯的链条。",
                "5. `before_after.md`：查看开始前和当前状态的对照。",
                "6. `ability_evidence_matrix.md`：逐项查看逻辑推理、迭代学习、项目推进、自我进化等能力证据。",
                "7. `experience_cards.md`：查看用户纠偏、失败经验和习惯形成。",
                "8. `timeline.md`：查看推进轨迹。",
                "9. `qq_conversation.md`：查看用户交互片段。",
                "10. `key_artifacts.md` / `artifacts/`：查看可展示报告、PPT、PDF 或审计文件。",
                "11. `limitations.md`：查看当前 demo 还没证明什么。",
                "",
                f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            ]
        ),
    )
    return out_dir

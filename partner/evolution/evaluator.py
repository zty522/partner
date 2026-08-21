"""Quality Evaluator — 给任务产出打可量化质量分（C1：自进化的北极星）。

评分维度（满分 100）：
  - 文件产出 (40)：有产出文件
  - 非空     (20)：文件大小 > 0
  - 非模板   (20)：内容不含占位符/空泛模式
  - 实质内容 (20)：文本达到最低字符阈值

记录：{workspace_root}/state/logs/quality_scores.jsonl（每轮一条）
分数可被自愈引擎 / research_loop 消费：高分 → 迭代继续；低分 → 触发修复。
"""
import json
import logging
import os
import re
from datetime import datetime

logger = logging.getLogger(__name__)

_PLACEHOLDER_PATTERNS = re.compile(
    r"\{\{.*?\}\}|TODO|FIXME|待补充|待完善|待写|占位|lorem ipsum|placeholder|^\s*\.\.\.\s*$",
    re.I | re.M,
)
_MIN_TEXT_CHARS = 100


def evaluate_outputs(files, expected_types=None) -> dict:
    """对产出文件列表打分。files: 绝对路径列表。

    Returns: {"score": 0-100, "reasons": [...], "detail": [...]}
    """
    score = 0
    reasons: list[str] = []
    detail: list[str] = []
    files = [f for f in (files or []) if f]

    if not files:
        return {"score": 0, "reasons": ["无产出文件"], "detail": []}
    score += 40
    reasons.append(f"有产出文件 ({len(files)} 个)")

    non_empty = 0
    substantive = 0
    template_hit = False
    for f in files:
        try:
            size = os.path.getsize(f)
        except OSError:
            size = 0
        if size > 0:
            non_empty += 1
        else:
            detail.append(f"{os.path.basename(f)}: 空文件")
        if 0 < size < 2 * 1024 * 1024:
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    text = fh.read(_MIN_TEXT_CHARS * 4)
                if len(text.strip()) >= _MIN_TEXT_CHARS:
                    substantive += 1
                else:
                    detail.append(f"{os.path.basename(f)}: 内容过短(<{_MIN_TEXT_CHARS}字)")
                if _PLACEHOLDER_PATTERNS.search(text):
                    template_hit = True
                    detail.append(f"{os.path.basename(f)}: 含模板占位特征")
            except Exception:
                pass

    if non_empty:
        score += 20
        reasons.append(f"{non_empty}/{len(files)} 非空")
    if substantive:
        score += 20
        reasons.append(f"{substantive}/{len(files)} 有实质内容")
    if template_hit:
        reasons.append("检出模板占位特征（扣 20）")
    else:
        score += 20
        reasons.append("未检出模板占位")

    return {"score": min(score, 100), "reasons": reasons, "detail": detail}


def record_quality_score(
    instance_id: str,
    workspace: str,
    *,
    round_num: int = 0,
    score: int = 0,
    reasons: list[str] | None = None,
    files: list[str] | None = None,
    task_title: str = "",
) -> str:
    """记录一轮产出的质量分到 workspace state/logs/quality_scores.jsonl。失败返回空串。"""
    try:
        from ..api_log import workspace_root_from_pointer

        root = workspace_root_from_pointer()
        if not root:
            return ""
        log_dir = os.path.join(root, "state", "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "quality_scores.jsonl")
        row = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "instance": instance_id,
            "round": round_num,
            "score": score,
            "reasons": reasons or [],
            "files": [os.path.basename(f) for f in (files or [])],
            "task_title": (task_title or "")[:120],
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path
    except Exception as exc:
        logger.debug("record_quality_score failed: %s", exc)
        return ""


# ── C2: 失败反思库（Reflexion 式失败经验）──────────────────────────

_FAILURE_LOG = "failure_reflections.jsonl"


def record_failure(
    instance_id: str,
    workspace: str,
    *,
    task_title: str = "",
    failure_type: str = "",
    reason: str = "",
    round_num: int = 0,
    score: int = 0,
) -> str:
    """记录一次失败反思（自动，无需人工）。写入 workspace state/logs/failure_reflections.jsonl。"""
    try:
        from ..api_log import workspace_root_from_pointer

        root = workspace_root_from_pointer()
        if not root:
            return ""
        log_dir = os.path.join(root, "state", "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, _FAILURE_LOG)
        row = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "instance": instance_id,
            "round": round_num,
            "failure_type": failure_type,
            "reason": reason,
            "score": score,
            "task_title": (task_title or "")[:120],
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path
    except Exception as exc:
        logger.debug("record_failure failed: %s", exc)
        return ""


def load_recent_failures(workspace: str = "", instance_id: str = "", limit: int = 3) -> list[dict]:
    """读取最近的失败反思（供任务生成时注入，避免重蹈覆辙）。"""
    try:
        from ..api_log import workspace_root_from_pointer

        root = workspace_root_from_pointer()
        if not root:
            return []
        path = os.path.join(root, "state", "logs", _FAILURE_LOG)
        if not os.path.exists(path):
            return []
        rows = []
        for line in open(path, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if instance_id and row.get("instance") != instance_id:
                continue
            rows.append(row)
        return rows[-limit:]
    except Exception as exc:
        logger.debug("load_recent_failures failed: %s", exc)
        return []


# ── C4: 成功经验沉淀（Voyager 式技能卡片）──────────────────────────

_SUCCESS_LOG = "skill_cards.jsonl"


def record_success(
    instance_id: str,
    workspace: str,
    *,
    task_title: str = "",
    summary: str = "",
    files: list[str] | None = None,
    round_num: int = 0,
    score: int = 0,
) -> str:
    """把一轮成功的任务（任务→方法→产出）沉淀为技能卡片，跨实例可复用。

    写入 workspace share/mind/skill_cards.jsonl（跨实例共享）。
    """
    try:
        from ..api_log import workspace_root_from_pointer

        root = workspace_root_from_pointer()
        if not root:
            return ""
        mind_dir = os.path.join(root, "share", "mind")
        os.makedirs(mind_dir, exist_ok=True)
        path = os.path.join(mind_dir, _SUCCESS_LOG)
        row = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "instance": instance_id,
            "round": round_num,
            "task_title": (task_title or "")[:120],
            "summary": (summary or "")[:600],
            "files": [os.path.basename(f) for f in (files or [])],
            "score": score,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path
    except Exception as exc:
        logger.debug("record_success failed: %s", exc)
        return ""


def load_recent_successes(workspace: str = "", instance_id: str = "", limit: int = 2) -> list[dict]:
    """读取最近的技能卡片（供任务生成时注入成功经验）。"""
    try:
        from ..api_log import workspace_root_from_pointer

        root = workspace_root_from_pointer()
        if not root:
            return []
        path = os.path.join(root, "share", "mind", _SUCCESS_LOG)
        if not os.path.exists(path):
            return []
        rows = []
        for line in open(path, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if instance_id and row.get("instance") != instance_id:
                continue
            rows.append(row)
        return rows[-limit:]
    except Exception as exc:
        logger.debug("load_recent_successes failed: %s", exc)
        return []

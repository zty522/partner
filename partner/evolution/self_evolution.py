"""Self-Evolution Engine — learns from external knowledge to improve Partner.

Two modes:
1. ingest(url|file|text) — feed external content, LLM extracts improvement ideas
2. reflect() — analyze Partner's own run logs for systemic issues

Both produce EvolutionInsight objects that can be auto-applied or reviewed.
Completely separate from experiences/growth/habits — those are statistical/tracking,
this is architectural self-improvement.
"""

from __future__ import annotations

import json, logging, os, re, sqlite3, time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── Data types ──

@dataclass
class EvolutionInsight:
    """One improvement idea extracted from external knowledge or reflection."""
    source: str  # "url", "file", "reflection", "article"
    source_ref: str  # URL or file path or "run_logs"
    insight: str  # what was learned
    suggestion: str  # what to do about it
    target_module: str = ""  # which Partner module this affects
    priority: str = "medium"  # high/medium/low
    confidence: float = 0.7
    auto_apply: bool = False  # can this be applied automatically?
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


# ── Prompt templates ──

INGEST_PROMPT = """你是一个 AI 自进化分析引擎。分析以下外部内容，提取对 Partner（一个 AI 研究助手系统）有价值的改进思路。

**核心原则**: 即使内容不是直接讲 AI 系统设计的，也要思考它对 AI 系统的启示。比如：一篇生物学文章可能启示更好的数据处理流程，一篇产品设计文章可能启示更好的用户交互。

## 外部内容
{content}

## 分析要求
请务必从以下角度思考，每个角度至少尝试生成1条洞察：
1. **架构模式**: 值得借鉴的系统架构、模块设计、数据流、并行策略
2. **Agent 设计**: Agent 交互方式、任务分解、多Agent协作、工具调用
3. **Prompt 工程**: 更好的 prompt 模板、few-shot 策略、输出约束
4. **工作流**: 执行流程优化、错误处理、重试策略、超时管理
5. **能力缺口**: Partner 缺少什么能力，如何补充

每条洞察必须具体、可执行。不要泛泛而谈。

输出 JSON 数组（只输出 JSON，不要其他内容）：
[{{"insight": "具体发现（必须引用原文具体内容）", "suggestion": "具体可执行的改进建议", "target_module": "影响的Partner模块名称", "priority": "high/medium/low", "confidence": 0.7}}]

即使内容看起来与 AI 无关，也要努力找启示。不要轻易返回空数组。"""


REFLECT_PROMPT = """你是 Partner 的自进化分析引擎。分析 Partner 近期的执行记录，找出系统性问题。

## 最近失败 ({fail_count} 条)
{failures}

## 最近成功 ({success_count} 条)
{successes}

## 高频失败模式
{error_patterns}

## 分析要求
基于以上数据，输出 JSON 数组：
[{{"insight": "系统性问题", "suggestion": "改进建议", "target_module": "影响模块", "priority": "high/medium/low"}}]

只输出 JSON。"""


# ── Content fetching ──

def _fetch_content(source: str) -> str | None:
    """Fetch content from a URL, file path, or raw text. Returns None if failed."""
    # Raw text (no URL/file indicators)
    if not source.startswith(("http://", "https://", "/", "./", "~/")) and "\n" in source:
        return source

    # URL
    if source.startswith(("http://", "https://")):
        try:
            import urllib.request
            req = urllib.request.Request(source, headers={"User-Agent": "Partner/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            # Try to extract readable text from HTML
            if "<html" in raw.lower()[:500]:
                return _extract_text_from_html(raw)
            return raw[:20000]
        except Exception as e:
            logger.warning("Failed to fetch URL %s: %s", source, e)
            return None

    # File path
    expanded = os.path.expanduser(source)
    if os.path.isfile(expanded):
        try:
            with open(expanded, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            # Handle PDF
            if expanded.endswith(".pdf"):
                return _extract_text_from_pdf(expanded)
            return content[:20000]
        except Exception as e:
            logger.warning("Failed to read file %s: %s", expanded, e)
            return None

    return None


def _extract_text_from_html(html: str) -> str:
    """Basic HTML text extraction without external deps."""
    # Remove scripts and styles
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.I)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.I)
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:20000]


def _extract_text_from_pdf(path: str) -> str:
    """Extract text from a PDF file."""
    try:
        import fitz
        doc = fitz.open(path)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text[:20000]
    except ImportError:
        return f"[PDF text extraction unavailable — install pymupdf: pip install pymupdf]\nFile: {path}"


# ── Core engine ──

class SelfEvolutionEngine:
    """LLM-driven self-evolution: ingest external knowledge → generate insights."""

    def __init__(self, db_path: str = "", adapter=None):
        self._db_path = db_path or os.path.expanduser(
            "~/.partner/evolution.db"
        )
        self._adapter = adapter

    def _get_adapter(self):
        if self._adapter:
            return self._adapter
        try:
            from partner.adapters.adapter import HermesAdapter
            return HermesAdapter(workspace_path="")
        except Exception:
            return None

    def ingest(self, source: str, source_label: str = "") -> list[EvolutionInsight]:
        """Feed external content (URL, file path, or raw text) for analysis.

        Args:
            source: URL, file path, or raw text content
            source_label: Human-readable label for the source (e.g. article title)

        Returns:
            List of EvolutionInsight objects extracted from the content.
        """
        content = _fetch_content(source)
        if not content:
            logger.warning("Failed to fetch content from: %s", source[:100])
            return []

        adapter = self._get_adapter()
        if not adapter:
            logger.warning("No adapter available for self-evolution")
            return []

        prompt = INGEST_PROMPT.format(content=content[:15000])
        try:
            response = adapter.chat(prompt, purpose="self_evolve_ingest")
            if not response:
                return []
            insights = self._parse_insights(response, source_label or source[:100])
            self._store_insights(insights)
            return insights
        except Exception as e:
            logger.warning("Self-evolution ingest failed: %s", e)
            return []

    def reflect(self, days: int = 3) -> list[EvolutionInsight]:
        """Analyze Partner's own run logs for systemic improvement ideas."""
        adapter = self._get_adapter()
        if not adapter:
            return []

        # Gather data from learning DB
        from partner.utils.workspace import get_learning_db_path
        db = sqlite3.connect(get_learning_db_path())

        failures = db.execute(
            "SELECT user_message, task_summary FROM experiences "
            "WHERE success=0 AND created_at > datetime('now', ?) "
            "ORDER BY created_at DESC LIMIT 20", (f"-{days} days",)
        ).fetchall()

        successes = db.execute(
            "SELECT user_message, task_summary FROM experiences "
            "WHERE success=1 AND created_at > datetime('now', ?) "
            "ORDER BY created_at DESC LIMIT 10", (f"-{days} days",)
        ).fetchall()

        error_patterns = db.execute(
            "SELECT task_summary, COUNT(*) as cnt FROM experiences "
            "WHERE success=0 AND task_summary != '' "
            "AND created_at > datetime('now', ?) "
            "GROUP BY task_summary ORDER BY cnt DESC LIMIT 10", (f"-{days} days",)
        ).fetchall()
        db.close()

        if len(failures) < 3:
            return []

        prompt = REFLECT_PROMPT.format(
            fail_count=len(failures),
            failures="\n".join(f"- {(f[1] or f[0])[:120]}" for f in failures[:15]),
            success_count=len(successes),
            successes="\n".join(f"- {(s[1] or s[0])[:120]}" for s in successes[:5]),
            error_patterns="\n".join(
                f"- {e[1]}次: {(e[0] or '')[:150]}" for e in error_patterns[:8]
            ),
        )

        try:
            response = adapter.chat(prompt, purpose="self_evolve_reflect")
            if not response:
                return []
            insights = self._parse_insights(response, "run_logs")
            self._store_insights(insights)
            return insights
        except Exception as e:
            logger.warning("Self-evolution reflect failed: %s", e)
            return []

    def _parse_insights(self, response: str, source: str) -> list[EvolutionInsight]:
        """Parse LLM response into EvolutionInsight objects."""
        try:
            data = json.loads(response.strip())
        except json.JSONDecodeError:
            m = re.search(r"\[.*\]", response, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group())
                except json.JSONDecodeError:
                    return []
            else:
                return []

        if not isinstance(data, list):
            return []

        insights = []
        for item in data[:10]:
            if not item.get("insight") or not item.get("suggestion"):
                continue
            insights.append(EvolutionInsight(
                source="ingest" if source != "run_logs" else "reflection",
                source_ref=source[:200],
                insight=item["insight"],
                suggestion=item["suggestion"],
                target_module=item.get("target_module", ""),
                priority=item.get("priority", "medium"),
                confidence=float(item.get("confidence", 0.7)),
                auto_apply=item.get("priority") == "low",
            ))
        return insights

    def _store_insights(self, insights: list[EvolutionInsight]) -> None:
        """Store insights to DB."""
        if not insights:
            return
        try:
            from partner.utils.workspace import get_learning_db_path
            db = sqlite3.connect(get_learning_db_path())
            for ins in insights:
                db.execute(
                    """INSERT INTO evolution_rules
                       (rule_type, rule_text, confidence, category, created_at)
                       VALUES (?, ?, ?, ?, datetime('now'))""",
                    (
                        "self_evolution",
                        f"[{ins.source}] {ins.insight} → {ins.suggestion}",
                        ins.confidence,
                        "architecture_insight",
                    ),
                )
            db.commit()
            db.close()
            logger.info("Stored %d self-evolution insights", len(insights))
        except Exception as e:
            logger.warning("Failed to store insights: %s", e)

    def list_insights(self, limit: int = 20) -> list[dict]:
        """List recent insights from DB."""
        try:
            from partner.utils.workspace import get_learning_db_path
            db = sqlite3.connect(get_learning_db_path())
            rows = db.execute(
                "SELECT id, rule_text, confidence, created_at FROM evolution_rules "
                "WHERE rule_type='self_evolution' ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            db.close()
            return [{"id": r[0], "text": r[1], "confidence": r[2], "created": r[3]} for r in rows]
        except Exception:
            return []


# ── Convenience functions ──

def learn_from(source: str, label: str = "") -> list[dict]:
    """One-call: feed content → get insights. For use from Partner's CLI or API.

    Usage:
        insights = learn_from("https://example.com/article")
        insights = learn_from("/path/to/paper.pdf")
        insights = learn_from("raw text content here...", label="manual input")
    """
    engine = SelfEvolutionEngine()
    results = engine.ingest(source, label)
    return [
        {
            "insight": r.insight,
            "suggestion": r.suggestion,
            "target": r.target_module,
            "priority": r.priority,
        }
        for r in results
    ]


# ── CLI test ──

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        source = sys.argv[1]
        label = sys.argv[2] if len(sys.argv) > 2 else ""
        print(f"Learning from: {label or source[:80]}...")
        results = learn_from(source, label)
        for r in results:
            print(f"\n  [{r['priority']}] {r['insight'][:120]}")
            print(f"  → {r['suggestion'][:120]}")
        print(f"\n{len(results)} insights generated")
    else:
        # Test with a small internal reflection
        engine = SelfEvolutionEngine()
        insights = engine.reflect(days=7)
        for ins in insights:
            print(f"[{ins.priority}] {ins.insight[:100]}")
            print(f"  → {ins.suggestion[:100]}")

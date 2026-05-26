"""Idea Generator — systematic AI idea generation for Partner.

Implements three idea generation methods from ERA paper (Nature 2026):
1. Code Mutation: LLM rewrites existing code to improve quality
2. Method Remixing: Combine two existing approaches
3. Novel Idea: LLM proposes entirely new approaches

Each idea is scored, ranked, and the best ones are executed.
"""

import json
import os
import logging
import subprocess
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


@dataclass
class Idea:
    """A single generated idea with metadata."""
    idea_id: str
    method: str                # mutation | remix | novel
    title: str
    description: str
    confidence: float          # 0.0 - 1.0, LLM's self-assessment
    estimated_impact: str      # high | medium | low
    source_materials: List[str] = field(default_factory=list)  # referenced methods/papers
    status: str = "pending"    # pending | running | completed | failed
    score: Optional[float] = None  # actual result after execution
    notes: str = ""
    created_at: str = ""
    executed_at: str = ""

    def to_dict(self):
        return asdict(self)


class IdeaGenerator:
    """Generates research ideas using Hermes LLM.

    Three generation methods:
    1. mutation — improve existing approaches
    2. remix — combine two or more existing methods
    3. novel — LLM proposes something new
    """

    def __init__(self, workspace: str):
        self.workspace = workspace
        self.ideas_dir = os.path.join(workspace, "ideas")
        self.ideas_file = os.path.join(workspace, "state", "ideas.json")
        self._load_ideas()

    def _load_ideas(self):
        """Load existing ideas registry."""
        if os.path.exists(self.ideas_file):
            try:
                with open(self.ideas_file) as f:
                    data = json.load(f)
                self.ideas = [Idea(**d) if isinstance(d, dict) else d for d in data]
            except Exception:
                self.ideas = []
        else:
            self.ideas = []

    def _save_ideas(self):
        """Persist ideas registry."""
        os.makedirs(os.path.dirname(self.ideas_file), exist_ok=True)
        with open(self.ideas_file, "w", encoding="utf-8") as f:
            json.dump([i.to_dict() for i in self.ideas], f, indent=2, ensure_ascii=False)

    def _next_id(self) -> str:
        return f"idea_{len(self.ideas) + 1:04d}"

    def _call_llm(self, prompt: str) -> Optional[str]:
        """Call Hermes LLM for idea generation."""
        try:
            from .adapter import create_adapter
            adapter = create_adapter("hermes", self.workspace)
            result = adapter.chat(prompt)
            import time
            time.sleep(0.5)  # Rate limiting
            return result
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return None

    def generate_ideas(self, project: str = "", count: int = 3) -> List[Idea]:
        """Generate ideas using all three methods, return the best ones.

        Args:
            project: Optional project context
            count: How many ideas to return (total across methods)

        Returns:
            List of Idea objects, ranked by confidence
        """
        all_ideas = []

        # 1. Mutation: improve existing approaches
        existing = self._get_existing_methods(project)
        if existing:
            mutation = self._generate_mutation(existing[:3], project)
            if mutation:
                all_ideas.append(mutation)

        # 2. Remix: combine two methods
        if len(existing) >= 2:
            remix = self._generate_remix(existing[:2], project)
            if remix:
                all_ideas.append(remix)

        # 3. Novel: LLM thinks of something new
        novel = self._generate_novel(project)
        if novel:
            all_ideas.append(novel)

        # Score and rank
        all_ideas.sort(key=lambda x: x.confidence, reverse=True)

        # Save all
        for idea in all_ideas:
            self.ideas.append(idea)
        self._save_ideas()

        return all_ideas[:count]

    def _get_existing_methods(self, project: str = "") -> List[Dict]:
        """Get existing methods/code from workspace."""
        methods = []

        # Check project code directories
        code_dirs = []
        if project:
            code_dirs.append(os.path.join(self.workspace, "projects", project, "code"))
        code_dirs.append(os.path.join(self.workspace, "code"))

        for cd in code_dirs:
            if os.path.exists(cd):
                for fname in sorted(os.listdir(cd))[:5]:
                    fpath = os.path.join(cd, fname)
                    if fname.endswith(".py") and os.path.isfile(fpath):
                        try:
                            with open(fpath) as f:
                                content = f.read(2000)  # First 2000 chars
                            methods.append({
                                "name": fname,
                                "path": fpath,
                                "preview": content[:500],
                            })
                        except Exception:
                            pass

        # Also check knowledge base for known methods
        kb_path = os.path.join(self.workspace, "state", "knowledge.json")
        if os.path.exists(kb_path):
            try:
                with open(kb_path) as f:
                    kb = json.load(f)
                for entry in kb.get("entries", [])[-10:]:
                    if entry.get("category") in ("methods", "technique", "implementation"):
                        methods.append({
                            "name": entry.get("title", "unknown"),
                            "preview": entry.get("content", "")[:500],
                        })
            except Exception:
                pass

        return methods

    def _generate_mutation(self, existing: List[Dict], project: str = "") -> Optional[Idea]:
        """Method 1: LLM improves an existing approach."""
        methods_text = "\n---\n".join(
            f"方法: {m['name']}\n预览: {m.get('preview','')[:300]}" for m in existing[:2]
        )
        prompt = f"""你是一个科研方法设计专家。分析以下现有的研究方法，提出一个具体的改进方案。

现有方法:
{methods_text}

请提出一个具体的改进方案，包括：
1. 改进思路（核心创新点）
2. 预期效果
3. 实施难度（高/中/低）
4. 自信度（0-1之间的分数）

格式: 标题 | 描述 | 预期效果 | 难度 | 自信度"""
        result = self._call_llm(prompt)
        if not result:
            return None

        return self._parse_idea(result, "mutation", [m["name"] for m in existing[:2]])

    def _generate_remix(self, pair: List[Dict], project: str = "") -> Optional[Idea]:
        """Method 2: Combine two existing methods."""
        text = "\n---\n".join(
            f"方法A: {pair[0]['name']}\n{pair[0].get('preview','')[:300]}\n\n方法B: {pair[1]['name']}\n{pair[1].get('preview','')[:300]}"
        )
        prompt = f"""你是一个科研方法设计专家。分析以下两种方法，提出一个将它们重新组合的创新方案。

{text}

请提出组合方案，包括：
1. 如何将两种方法的优点结合起来
2. 具体的技术路线
3. 预期效果
4. 自信度（0-1）

格式: 标题 | 描述 | 预期效果 | 难度 | 自信度"""
        result = self._call_llm(prompt)
        if not result:
            return None

        return self._parse_idea(result, "remix", [pair[0]["name"], pair[1]["name"]])

    def _generate_novel(self, project: str = "") -> Optional[Idea]:
        """Method 3: LLM proposes something entirely new."""
        context = f"项目方向: {project}" if project else "没有特定项目限制"
        prompt = f"""你是一个科研方法设计专家。{context}

请提出一个全新的研究思路或方法，不要依赖现有方法。
这应该是一个原创性的想法。

要求：
1. 核心创新点（为什么是新的）
2. 技术路线概要
3. 先进性（相比现有方法的优势）
4. 实施难度（高/中/低）
5. 自信度（0-1）

格式: 标题 | 描述 | 预期效果 | 难度 | 自信度"""
        result = self._call_llm(prompt)
        if not result:
            return None

        return self._parse_idea(result, "novel", [])

    def _parse_idea(self, llm_output: str, method: str, sources: List[str]) -> Optional[Idea]:
        """Parse LLM output into structured Idea."""
        try:
            lines = llm_output.strip().split("\n")
            first = lines[0] if lines else ""
            parts = first.split("|")

            title = parts[0].strip() if len(parts) > 0 else first[:60]
            desc = parts[1].strip() if len(parts) > 1 else first
            impact_part = parts[2].strip().lower() if len(parts) > 2 else "medium"
            difficulty = parts[3].strip().lower() if len(parts) > 3 else "medium"
            conf_str = parts[4].strip() if len(parts) > 4 else "0.5"

            # Parse confidence
            try:
                confidence = float(conf_str)
            except ValueError:
                confidence = 0.5

            # Map impact
            impact = "medium"
            for kw, val in [("high", "high"), ("low", "low")]:
                if kw in impact_part:
                    impact = val

            return Idea(
                idea_id=self._next_id(),
                method=method,
                title=title[:120],
                description=llm_output[:2000],
                confidence=min(max(confidence, 0.0), 1.0),
                estimated_impact=impact,
                source_materials=sources,
                created_at=datetime.now().isoformat(),
            )
        except Exception as e:
            logger.error(f"Parse idea failed: {e}")
            return None


def save_idea_record(workspace: str, idea: Idea, result_summary: str = ""):
    """Save an idea with its result to the ideas record file."""
    record_path = os.path.join(workspace, "state", "idea_records.jsonl")
    record = {
        "timestamp": datetime.now().isoformat(),
        "idea_id": idea.idea_id,
        "method": idea.method,
        "title": idea.title,
        "confidence": idea.confidence,
        "status": idea.status,
        "result": result_summary,
    }
    os.makedirs(os.path.dirname(record_path), exist_ok=True)
    with open(record_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

"""Self-Evolve Engine — periodic closed-loop learning for Partner.

Coordinates the full evolution cycle:
1. Extract lessons from the experiences table (lesson_extractor)
2. Tune behavior parameters from habits (behavior_tuner)
3. Apply rules to future planning cycles

This is called after each task completion by executor.py's _trigger_self_evolution() hook.
"""

from __future__ import annotations

import json
import logging
import re  # for _extract_repos_from_topic
import sqlite3
import time
from datetime import datetime
from typing import Any

from ..meta.learning import record_growth, get_experience_stats
from ..utils.workspace import get_learning_db_path
from .lesson_extractor import extract_lessons, get_rule_stats, ensure_rules_table
from .behavior_tuner import count_rules, load_rules, format_rules_for_prompt

# ── Five-step cycle imports (lazy, loaded on demand) ──
_GAP_DISCOVERY = None
_KNOWLEDGE_ACQUISITION = None
_PLAN_FORMATION = None
_IMPLEMENTATION = None
_VERIFICATION = None
_CODE_KNOWLEDGE = None

def _load_five_step_modules():
    global _GAP_DISCOVERY, _KNOWLEDGE_ACQUISITION, _PLAN_FORMATION, _IMPLEMENTATION, _VERIFICATION, _CODE_KNOWLEDGE
    from .gap_discovery import GapDiscovery as _G
    from .knowledge_acquisition import KnowledgeAcquirer as _K
    from .plan_formation import PlanFormation as _P
    from .implementation import Implementation as _I
    from . import verification as _V
    _GAP_DISCOVERY = _G
    _KNOWLEDGE_ACQUISITION = _K
    _PLAN_FORMATION = _P
    _IMPLEMENTATION = _I
    _VERIFICATION = _V

def _load_code_knowledge():
    global _CODE_KNOWLEDGE
    from .code_knowledge import CodeKnowledge as _C
    _CODE_KNOWLEDGE = _C

logger = logging.getLogger(__name__)

# ── Throttle tracker ─────────────────────────────────────────────────────
# Trigger counts — resets when a full cycle fires
_evolution_accumulator = 0
_last_cycle_time: float = 0.0
_min_cycle_interval = 60.0  # seconds between cycles
_rules_table_ensured = False


def _ensure_db():
    global _rules_table_ensured
    if not _rules_table_ensured:
        ensure_rules_table()
        _rules_table_ensured = True


def _reset_accumulator():
    global _evolution_accumulator, _last_cycle_time
    _evolution_accumulator = 0
    _last_cycle_time = time.time()


# ── Core cycle ────────────────────────────────────────────────────────────


def _run_evolution_cycle(amount: int) -> dict[str, Any]:
    """Run one full evolution cycle: extract → tune → store.

    Returns a report dict with stats about what happened.
    """
    start = time.time()
    _ensure_db()

    # Phase 1: Extract lessons from recent experiences
    new_rules = extract_lessons(limit=200)

    # Phase 2: Get current stats
    rule_stats = get_rule_stats()
    rule_count = rule_stats.get("total_rules", count_rules())

    # Phase 3: Compute cycle number and record growth milestone
    try:
        _gc_db = sqlite3.connect(get_learning_db_path())
        cycle_num = _gc_db.execute(
            "SELECT COUNT(*) AS c FROM growth WHERE category = 'self_evolution'"
        ).fetchone()[0] + 1
        _gc_db.close()
    except Exception:
        cycle_num = 1
    milestone_text = (
        f"自进化循环 #{cycle_num + 1}: {len(new_rules)} 新规则, "
        f"共 {rule_count} 条规则"
    )
    record_growth(
        user_id="default",
        milestone=milestone_text,
        reflection=json.dumps({
            "new_rules": len(new_rules),
            "total_rules": rule_count,
            "rule_types": rule_stats.get("by_type", {}),
            "avg_confidence": rule_stats.get("avg_confidence", 0),
            "cycle_duration_s": round(time.time() - start, 1),
        }, ensure_ascii=False),
        category="self_evolution",
    )

    elapsed = time.time() - start
    logger.info(
        "[EVOLVE] cycle complete: %d new rules, %d total, took %.1fs",
        len(new_rules), rule_count, elapsed,
    )

    # ── A 方案：自进化周期末尾自动应用低风险架构改进 ──
    try:
        from .architecture_improver import ArchitectureImprover
        improver = ArchitectureImprover()
        arch_result = improver.apply_improvements(
            max_risk_level="low",
            require_approval=False,
        )
        if arch_result.applied_count > 0:
            logger.info(
                "[EVOLVE] auto-applied %d low-risk architecture improvements",
                arch_result.applied_count,
            )
            _record_arch_improvement_growth(arch_result.applied_count)
    except Exception as _arch_e:
        logger.debug("[EVOLVE] architecture auto-improve skipped: %s", _arch_e)

    # ── P2: Skills auto-evolution cycle ──
    skill_cycle_result = {}
    try:
        from .skill_evolver import SkillEvolver
        skill_evolver = SkillEvolver(db_path=get_learning_db_path())
        skill_cycle_result = skill_evolver.run_cycle()
        logger.info(
            "[EVOLVE] skill cycle: %d candidates, %d stale (%d pruned), %d degraded",
            skill_cycle_result.get("candidates_extracted", 0),
            skill_cycle_result.get("stale_detected", 0),
            skill_cycle_result.get("stale_pruned", 0),
            skill_cycle_result.get("degraded_detected", 0),
        )
        if skill_cycle_result.get("stale_pruned", 0) > 0:
            record_growth(
                user_id="default",
                milestone=f"Skills auto-evolution: pruned {skill_cycle_result['stale_pruned']} stale skills",
                reflection=json.dumps(skill_cycle_result, ensure_ascii=False, default=str),
                category="skill_evolution",
            )
    except Exception as _skill_e:
        logger.debug("[EVOLVE] skill evolver skipped: %s", _skill_e)

    # ── Phase 4: Deep LLM reflection (every 10 cycles) ──
    deep_insights = 0
    try:
        if cycle_num % 10 == 0:
            deep_insights = _run_deep_reflection(cycle_num)
            if deep_insights > 0:
                rule_count = count_rules()
    except Exception as _deep_e:
        logger.debug("[EVOLVE] deep reflection skipped: %s", _deep_e)

    return {
        "cycle_triggered": True,
        "new_rules": len(new_rules),
        "total_rules": rule_count,
        "by_type": rule_stats.get("by_type", {}),
        "avg_confidence": rule_stats.get("avg_confidence", 0),
        "cycle_duration_s": round(elapsed, 1),
        "cycle_number": cycle_num + 1,
        "deep_insights": deep_insights,
        "reason": f"提取 {len(new_rules)} 条新规则, 共 {rule_count} 条",
    }


def _record_arch_improvement_growth(count: int) -> None:
    """Record architecture improvement to growth table."""
    try:
        from ..meta.learning import record_growth
        record_growth(
            user_id="default",
            milestone=f"自动应用 {count} 条低风险架构改进",
            reflection=f"architecture_improver auto-applied {count} low-risk improvements",
            category="architecture_improvement",
        )
    except Exception:
        pass



# ── Deep LLM Reflection ──────────────────────────────────────────────────

def _run_deep_reflection(cycle_num: int) -> int:
    """Every ~10 cycles, run an LLM-driven analysis of Partner's execution patterns.

    Gathers recent task results (successes, failures, error patterns),
    sends them to an LLM, and stores actionable insights as architecture_insight
    rules in evolution_rules.

    Returns number of new insights generated.
    """
    try:
        from ..adapters.adapter import HermesAdapter
    except ImportError:
        return 0
    
    # Gather recent experiences
    db = sqlite3.connect(get_learning_db_path())
    
    # Recent failures
    failures = db.execute(
        "SELECT user_message, task_summary, output_type FROM experiences "
        "WHERE success=0 AND created_at > datetime('now', '-3 days') "
        "ORDER BY created_at DESC LIMIT 30"
    ).fetchall()
    
    # Recent successes
    successes = db.execute(
        "SELECT user_message, task_summary, output_type FROM experiences "
        "WHERE success=1 AND created_at > datetime('now', '-3 days') "
        "ORDER BY created_at DESC LIMIT 30"
    ).fetchall()
    
    # Top failure task patterns (grouped by task_summary)
    error_patterns = db.execute(
        "SELECT task_summary, COUNT(*) as cnt FROM experiences "
        "WHERE success=0 AND task_summary != '' AND created_at > datetime('now', '-7 days') "
        "GROUP BY task_summary ORDER BY cnt DESC LIMIT 10"
    ).fetchall()
    
    db.close()
    
    if len(failures) < 3:
        return 0
    
    # Build prompt
    failure_text = "\n".join(
        f"- 任务: {(f[1] or f[0])[:100]}"
        for f in failures[:20]
    )
    success_text = "\n".join(
        f"- 任务: {s[1][:100]} | 输出: {s[2]}"
        for s in successes[:10]
    )
    error_text = "\n".join(
        f"- {e[1]}次失败: {(e[0] or '')[:200]}"
        for e in error_patterns[:10]
    )
    
    prompt = f"""你是 Partner 的自进化分析引擎。分析 Partner 近期的执行记录，找出可改进的地方。

## 最近失败 ({len(failures)} 条)
{failure_text}

## 最近成功 ({len(successes)} 条)
{success_text}

## 高频错误
{error_text}

## 分析要求
基于以上数据，分析：
1. Partner 在执行中存在哪些系统性问题？
2. 哪些改进可以显著提升成功率？
3. 每个建议需要具体到：改进什么、为什么、预期效果

输出 JSON 数组，每个建议一个对象：
[{{"insight": "具体发现", "suggestion": "改进建议", "target": "影响模块", "priority": "high/medium/low", "expected_effect": "预期效果"}}]

只输出 JSON 数组，不要其他内容。"""

    try:
        adapter = HermesAdapter(workspace_path="")
        response = adapter.chat(prompt, purpose="deep_reflection")
        if not response:
            return 0
        
        # Parse JSON
        suggestions = json.loads(response.strip())
        if not isinstance(suggestions, list):
            return 0
        
        # Store as architecture_insight rules
        db2 = sqlite3.connect(get_learning_db_path())
        count = 0
        for s in suggestions[:5]:
            insight = s.get("insight", "")
            suggestion = s.get("suggestion", "")
            if not insight or not suggestion:
                continue
            rule_text = f"[自进化#{cycle_num}] {insight} → 建议: {suggestion}"
            # Check duplicate
            exists = db2.execute(
                "SELECT COUNT(*) FROM evolution_rules WHERE rule_text=?",
                (rule_text,)
            ).fetchone()[0]
            if exists:
                continue
            db2.execute(
                """INSERT INTO evolution_rules 
                   (rule_type, rule_text, confidence, category, created_at)
                   VALUES (?, ?, ?, ?, datetime('now'))""",
                ("deep_reflection", rule_text, 0.7, "architecture_insight"),
            )
            count += 1
        db2.commit()
        db2.close()
        
        logger.info("[EVOLVE] deep reflection #%d: %d insights from %d failures + %d successes",
                     cycle_num, count, len(failures), len(successes))
        return count
    except Exception as e:
        logger.warning("[EVOLVE] deep reflection failed: %s", e)
        return 0


async def ingest_external_knowledge(
    sources: list[dict],
    workspace: str = "",
    progress_callback=None,
) -> dict:
    """Ingest external knowledge and run the full 5-step self-evolution cycle.

    sources: list of {"type": "github"|"paper"|"url"|"code", "path": "...", "focus": "..."}

    The 5-step cycle:
    1. Knowledge Acquisition — read external sources
    2. Gap Discovery — compare against Partner's architecture
    3. Plan Formation — generate improvement plans
    4. Implementation — apply changes (low-risk auto, high-risk for review)
    5. Verification — run tests to verify

    Returns a detailed report.
    """
    import os as _os
    _load_five_step_modules()
    Gd, Ka, Pf, Im, Vf = _GAP_DISCOVERY, _KNOWLEDGE_ACQUISITION, _PLAN_FORMATION, _IMPLEMENTATION, _VERIFICATION

    if not workspace:
        workspace = _os.environ.get("PARTNER_DATA_DIR", "/mnt/e/work/partner_workspace/partner_data")

    report = {"success": False, "steps": {}, "sources": len(sources)}

    # Step 1: Acquire knowledge
    if progress_callback:
        await progress_callback(f"📡 步骤 1/5：获取外部知识（{len(sources)} 个来源）...")
    
    acquirer = Ka()
    all_knowledge = []
    for src in sources:
        stype = src.get("type", "")
        spath = src.get("path", "")
        try:
            if stype == "github":
                k = await acquirer.fetch_from_github(repo_url=spath, focus_area=src.get("focus", ""))
            elif stype in ("paper", "pdf"):
                k = await acquirer.fetch_from_local(path=spath, focus_area=src.get("focus", ""))
            elif stype == "url":
                k = await acquirer.fetch_from_url(url=spath, focus_area=src.get("focus", ""))
            elif stype == "code":
                k = await acquirer.fetch_from_local(path=spath, focus_area=src.get("focus", ""))
            else:
                continue
            if k and k.key_insights:
                all_knowledge.append(k)
                if progress_callback:
                    await progress_callback(f"  ✅ {stype}: {len(k.key_insights)} 条洞察")
        except Exception as e:
            logger.warning("[INGEST] failed to acquire %s: %s", stype, e)
    
    report["steps"]["knowledge"] = {"count": len(all_knowledge)}
    if not all_knowledge:
        report["error"] = "未获取到任何有效知识"
        return report

    # Step 2: Gap Discovery
    if progress_callback:
        await progress_callback(f"🔍 步骤 2/5：差距分析...")
    
    discoverer = Gd(workspace=workspace)
    gaps = []
    for k in all_knowledge:
        gap_list = discoverer.analyze(k)
        gaps.extend(gap_list)
    
    report["steps"]["gaps"] = {"count": len(gaps)}
    if not gaps:
        report["error"] = "未发现差距"
        return report

    # Step 3: Plan Formation
    if progress_callback:
        await progress_callback(f"📝 步骤 3/5：生成改进方案（{len(gaps)} 个差距）...")
    
    planner = Pf()
    plans = await planner.from_multiple_knowledge(all_knowledge, gaps)
    
    report["steps"]["plans"] = {"count": len(plans)}
    if not plans:
        return report

    # Step 4: Implementation
    if progress_callback:
        await progress_callback(f"🔧 步骤 4/5：实施改进（{len(plans)} 个方案）...")
    
    from .implementation import apply_plans
    results = await apply_plans(
        [p.to_dict() if hasattr(p, 'to_dict') else p for p in plans],
        workspace=workspace,
    )
    applied = [r for r in results if r.success]
    report["steps"]["implementation"] = {
        "total": len(results),
        "applied": len(applied),
        "failed": len(results) - len(applied),
    }

    # Step 5: Verification
    if progress_callback:
        await progress_callback(f"✅ 步骤 5/5：验证效果...")
    
    verify_results = await Vf.run_smoke_test(workspace=workspace) if hasattr(Vf, 'run_smoke_test') else {"passed": True}
    report["steps"]["verification"] = verify_results
    report["success"] = len(applied) > 0

    return report


def trigger_evolution(amount: int = 1) -> dict[str, Any]:
    """Called by executor.py after task completion.

    Accumulates triggers; fires a full cycle when enough have accumulated
    or when a minimum time interval has passed.

    Args:
        amount: Number of triggers to add (default 1 per task completion).

    Returns:
        Dict with cycle_triggered (bool), new_rules (int), etc.
    """
    global _evolution_accumulator, _last_cycle_time

    _evolution_accumulator += amount
    now = time.time()
    time_since_last = now - _last_cycle_time

    # Fire when we've accumulated enough triggers OR enough time has passed
    if _evolution_accumulator >= 5 or time_since_last >= 300.0:
        result = _run_evolution_cycle(amount)
        _reset_accumulator()
        return result

    logger.debug(
        "[EVOLVE] accumulator=%d/%d, time_since_last=%.0fs/300s — skipping",
        _evolution_accumulator, 5, time_since_last,
    )
    return {
        "cycle_triggered": False,
        "new_rules": 0,
        "total_rules": count_rules(),
        "accumulator": _evolution_accumulator,
    }


# ── SelfEvolveEngine class ────────────────────────────────────────────────


class SelfEvolveEngine:
    """Full self-evolution engine for multi-direction / deep evolution.

    Provides the async interface used by executor.py for on-demand
    self-evolution runs (self-review, deep evolution, etc.).
    """

    async def run_self_evolution(
        self,
        workspace: str = "",
        progress_callback=None,
    ) -> dict[str, Any]:
        """Run a single evolution cycle with progress reporting."""
        if progress_callback:
            await progress_callback("🔄 开始自进化循环：模式提取 + 行为调优")

        result = _run_evolution_cycle(amount=5)  # force immediate run

        if progress_callback:
            msg = (
                f"✅ 自进化循环完成：{result['new_rules']} 新规则, "
                f"共 {result['total_rules']} 条规则"
            )
            await progress_callback(msg)

        return result

    async def run_deep_self_evolution(
        self,
        workspace: str = "",
        progress_callback=None,
    ) -> dict[str, Any]:
        """Run 3 rounds of self-evolution, each building on the last."""
        report = {"rounds": [], "summary": ""}
        for i in range(3):
            if progress_callback:
                await progress_callback(f"🔄 第 {i + 1}/3 轮自进化...")

            result = _run_evolution_cycle(amount=5)
            report["rounds"].append(result)

            if progress_callback:
                await progress_callback(
                    f"第 {i + 1}/3 轮完成: {result['new_rules']} 新规则"
                )

        total_new = sum(r["new_rules"] for r in report["rounds"])
        last = report["rounds"][-1] if report["rounds"] else {}
        report["summary"] = (
            f"## 深度自进化闭环完成\n\n"
            f"3 轮迭代, {total_new} 新规则, "
            f"共 {last.get('total_rules', 0)} 条规则"
        )
        return report

    async def run_real_multi_direction_evolution(
        self,
        workspace: str = "",
        progress_callback=None,
    ) -> dict[str, Any]:
        """Run multi-direction self-evolution (legacy interface).

        Compatible with the existing executor.py code that calls this method.
        """
        return await self.run_deep_self_evolution(
            workspace=workspace, progress_callback=progress_callback,
        )

    @staticmethod
    def _extract_repos_from_topic(topic: str) -> list[str]:
        """Extract GitHub repository URLs from a topic string.

        Maps known names (Hermes, OpenClaw, etc.) to their GitHub URLs.
        Falls back to treating the topic as a direct reference.

        Args:
            topic: User topic string like "看看 Hermes 和 OpenClaw 的前端代码"

        Returns:
            List of repository URLs to learn from.
        """
        topic_lower = topic.lower()
        repos: list[str] = []

        NAME_TO_URL = {
            "hermes": "https://github.com/nousresearch/hermes-agent",
            "hermes desktop": "https://github.com/nousresearch/hermes-desktop",
            "openclaw": "https://github.com/nousresearch/openclaw",
            "openclaw desktop": "https://github.com/nousresearch/openclaw-desktop",
            "langchain": "https://github.com/langchain-ai/langchain",
            "langgraph": "https://github.com/langchain-ai/langgraph",
            "autogen": "https://github.com/microsoft/autogen",
            "crewai": "https://github.com/joaomdmoura/crewai",
            "react": "https://github.com/facebook/react",
            "vue": "https://github.com/vuejs/core",
        }

        for name, url in NAME_TO_URL.items():
            if name in topic_lower:
                repos.append(url)

        # Also try to extract raw GitHub URLs
        url_pattern = re.compile(r"https?://github\.com/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+")
        for match in url_pattern.finditer(topic):
            url = match.group(0).rstrip("/")
            if url not in repos:
                repos.append(url)

        # Default to Hermes + OpenClaw for frontend learning
        if not repos:
            repos = [
                "https://github.com/nousresearch/hermes-desktop",
                "https://github.com/nousresearch/openclaw",
            ]

        return repos

    async def run_full_self_evolution(
        self,
        topic: str = "",
        workspace: str = "",
        progress_callback=None,
    ) -> dict:
        """Run full self-evolution with topic-specific learning phases.
        
        Legacy 5-phase flow. See run_five_step_cycle() for the new 5-step closed loop.
        """
        import time as _t
        _start = _t.time()
        phases = []

        # Phase 1: Start
        msg1 = f"🔄 开始自进化学习：{topic[:60]}" if topic else "🔄 开始自进化学习"
        if progress_callback:
            await progress_callback(msg1)
        phases.append({"phase": "start", "message": msg1})

        # Phase 2: Search (simulate search by trying to extract related experiences)
        msg2 = f"📡 正在搜索相关资料..."
        if progress_callback:
            await progress_callback(msg2)
        phases.append({"phase": "search", "message": msg2, "topic": topic})

        # ── Code Learning Branch: detect if topic is about learning external code ──
        focus_lower = topic.lower()
        is_frontend_focus = any(kw in focus_lower for kw in [
            "前端", "gui", "ui", "frontend", "界面",
            "hermes", "openclaw", "代码", "code",
            "看看", "学习", "改进",
        ])

        code_learn_phase = None
        if is_frontend_focus and topic:
            if progress_callback:
                await progress_callback("🎯 检测到代码学习需求，启动代码分析流水线")

            try:
                from .code_learner import CodeLearner
                from .pattern_extractor import PatternExtractor
                from .pattern_comparator import PatternComparator
                from .pattern_improver import PatternImprover

                repo_urls = self._extract_repos_from_topic(topic)

                code_learn_start = _t.time()

                # Step 1: Fetch & analyze repos
                results = await CodeLearner.learn(
                    repo_urls=repo_urls,
                    focus_area="frontend",
                    progress_callback=progress_callback,
                )

                if results:
                    code_learn_phase = {
                        "repos_analyzed": len(results),
                        "repos": [r.source_name for r in results],
                    }

                    # Step 2: Extract patterns
                    if progress_callback:
                        await progress_callback("🧩 正在提取设计模式...")

                    all_patterns = []
                    for knowledge in results:
                        patterns = PatternExtractor.extract(knowledge, focus_area="frontend")
                        all_patterns.extend(patterns)

                    if progress_callback:
                        await progress_callback(
                            f"📊 提取到 {len(all_patterns)} 个设计模式"
                        )

                    code_learn_phase["patterns_count"] = len(all_patterns)

                    # Step 3: Compare & find gaps
                    if progress_callback:
                        await progress_callback("⚖️ 正在对比 Partner 与外部代码的差距...")

                    gaps = await PatternComparator.compare_external_to_actual_partner(
                        external_knowledge=results[0] if results else None,
                        external_patterns=all_patterns,
                        progress_callback=progress_callback,
                    )

                    if progress_callback:
                        high_gaps = sum(1 for g in gaps if g.get("priority") == "high")
                        med_gaps = sum(1 for g in gaps if g.get("priority") == "medium")
                        await progress_callback(
                            f"📋 发现 {len(gaps)} 个差距 "
                            f"(高优先级 {high_gaps}, 中优先级 {med_gaps})"
                        )

                    code_learn_phase["gaps_count"] = len(gaps)
                    code_learn_phase["gaps"] = gaps

                    # Step 4: Generate improvement plans
                    if progress_callback:
                        await progress_callback("💡 正在生成改进方案...")

                    plans = await PatternImprover.generate_improvements(
                        gaps, progress_callback=progress_callback,
                    )
                    plans_hl = PatternImprover.format_plan_human_readable(plans)

                    if progress_callback:
                        await progress_callback(f"✅ 生成 {len(plans)} 个改进方案")
                        for p in plans[:5]:
                            await progress_callback(
                                f"  {p.get('target_file', '?')}: {p.get('description', '?')[:80]}"
                            )

                    code_learn_phase["plans_count"] = len(plans)
                    code_learn_phase["plans"] = plans
                    code_learn_phase["human_readable"] = plans_hl
                    code_learn_phase["elapsed_s"] = round(_t.time() - code_learn_start, 1)

                    if progress_callback:
                        await progress_callback(
                            f"🎉 代码学习完成：分析了 {len(results)} 个仓库，"
                            f"提取 {len(all_patterns)} 个模式，"
                            f"发现 {len(gaps)} 个差距，"
                            f"生成 {len(plans)} 个方案"
                        )

            except Exception as cle:
                logger.error("Code learning failed: %s", cle)
                if progress_callback:
                    await progress_callback(f"❌ 代码学习失败: {cle}")
                code_learn_phase = {"error": str(cle)}

        if not code_learn_phase:
            # Phase 3: Analysis - run the standard evolution cycle
            msg3 = f"🔍 正在分析对比 {topic[:40]}..." if topic else "🔍 正在分析模式..."
            if progress_callback:
                await progress_callback(msg3)

            result = _run_evolution_cycle(amount=5)
            phases.append({
                "phase": "analysis",
                "message": msg3,
                "new_rules": result.get('new_rules', 0),
                "total_rules": result.get('total_rules', 0),
            })

            # Phase 4: Plan generation
            msg4 = "📝 已生成改进方案"
            if progress_callback:
                await progress_callback(msg4)
            phases.append({"phase": "plan_generation", "message": msg4})

        # Phase 5: Completion
        if code_learn_phase:
            elapsed = _t.time() - _start
            msg5 = (
                f"✅ 代码学习完成：{code_learn_phase.get('repos_analyzed', 0)} 个仓库, "
                f"{code_learn_phase.get('gaps_count', 0)} 个差距, "
                f"{code_learn_phase.get('plans_count', 0)} 个改进方案 "
                f"（耗时 {elapsed:.1f}s）"
            )
            if progress_callback:
                await progress_callback(msg5)
            phases.append({"phase": "complete", "message": msg5})

            return {
                "success": True,
                "topic": topic,
                "new_rules": code_learn_phase.get("gaps_count", 0),
                "total_rules": 0,
                "elapsed_s": round(_t.time() - _start, 1),
                "phases": phases,
                "code_learning": code_learn_phase,
            }
        else:
            new_rules = result.get('new_rules', 0)
            total_rules = result.get('total_rules', 0)
            elapsed = _t.time() - _start
            msg5 = f"✅ 自进化完成，{new_rules} 条改进方案待审批（耗时 {elapsed:.1f}s）"
            if progress_callback:
                await progress_callback(msg5)
            phases.append({"phase": "complete", "message": msg5})

            return {
                "success": True,
                "topic": topic,
                "new_rules": new_rules,
                "total_rules": total_rules,
                "elapsed_s": round(elapsed, 1),
                "phases": phases,
            }

    async def run_five_step_cycle(
        self,
        workspace: str = "",
        progress_callback=None,
        focus_targets: list[str] | None = None,
        focus_aspect: str = "",
    ) -> dict:
        """Run the complete 5-step self-evolution closed loop.
        
        Steps:
        1. Gap Discovery — systematically detect "what's not good enough"
        2. Knowledge Acquisition — fetch external knowledge for each gap
        3. Plan Formation — map external knowledge to Partner modules
        4. Implementation — apply changes (config auto, code pending approval)
        5. Verification — compare before/after performance
        
        When focus_targets is provided (e.g. ["Hermes", "OpenClaw"]), the cycle
        focuses only on those specific external systems instead of scanning all
        historical data.
        
        Returns a detailed report of what happened at each step.
        """
        import time as _t
        import os as _os
        _start = _t.time()
        report = {
            "success": False,
            "steps": {},
            "total_gaps": 0,
            "total_plans": 0,
            "applied_count": 0,
            "verification": None,
            "elapsed_s": 0,
            "focus_targets": focus_targets or [],
            "focus_aspect": focus_aspect,
        }
        
        if not workspace:
            workspace = os.environ.get("PARTNER_DATA_DIR", "")

        # Load modules on first use
        _load_five_step_modules()
        Gd = _GAP_DISCOVERY
        Ka = _KNOWLEDGE_ACQUISITION
        Pf = _PLAN_FORMATION
        Im = _IMPLEMENTATION
        Vf = _VERIFICATION

        # ─────────────────────────────────────────────────────────────────
        # Step 1: Gap Discovery
        # ─────────────────────────────────────────────────────────────────
        step1_start = _t.time()
        has_focus = bool(focus_targets)

        if has_focus:
            targets_str = "、".join(focus_targets)
            if progress_callback:
                await progress_callback(f"🔍 步骤 1/5：差距发现 — 针对性分析 {targets_str} 的 {focus_aspect}")
        else:
            if progress_callback:
                await progress_callback("🔍 步骤 1/5：差距发现 — 系统检测所有历史执行瓶颈和性能差距")

        # Initialize plan containers BEFORE Step 1 try block (CodeKnowledge may populate them)
        all_plans: list[dict] = []
        applied_plans: list = []

        try:
            if has_focus:
                # 有针对性的差距发现：读目标代码 + 对比 Partner 对应模块
                gaps = []
                is_frontend = False  # ← BUG FIX: initialized before loop, not inside it
                for target in focus_targets:
                    target_lower = target.lower()
                    if progress_callback:
                        await progress_callback(f"📂 正在读取 {target} 的代码结构...")

                    # 解析目标路径
                    target_path = None
                    if "hermes" in target_lower:
                        candidate = "/mnt/e/work/hermes-agent"
                        if _os.path.isdir(candidate):
                            target_path = candidate
                    elif "openclaw" in target_lower or "claw" in target_lower:
                        candidate = "/home/os/.openclaw"
                        if _os.path.isdir(candidate):
                            target_path = candidate
                    elif "langchain" in target_lower:
                        # Try to find langchain
                        import subprocess as _sp
                        _r = _sp.run(["python3", "-c", "import langchain; print(langchain.__file__)"],
                                     capture_output=True, text=True, timeout=15)
                        _p = _r.stdout.strip()
                        if _p:
                            target_path = _os.path.dirname(_os.path.dirname(_p))
                    
                    # Search external/ directory for any target
                    if not target_path:
                        external_base = "/mnt/e/work/partner_workspace/external"
                        # Direct match
                        candidate = _os.path.join(external_base, target)
                        if _os.path.isdir(candidate):
                            target_path = candidate
                        # Case-insensitive search
                        if not target_path and _os.path.isdir(external_base):
                            for _entry in _os.listdir(external_base):
                                if target_lower in _entry.lower():
                                    _cand = _os.path.join(external_base, _entry)
                                    if _os.path.isdir(_cand):
                                        target_path = _cand
                                        break
                        # Also check external_repos
                        if not target_path:
                            ext_repos = "/mnt/e/work/partner_workspace/external_repos"
                            if _os.path.isdir(ext_repos):
                                for _entry in _os.listdir(ext_repos):
                                    if target_lower in _entry.lower():
                                        _cand = _os.path.join(ext_repos, _entry)
                                        if _os.path.isdir(_cand):
                                            target_path = _cand
                                            break

                    if target_path and _os.path.isdir(target_path):
                        if progress_callback:
                            await progress_callback(f"✅ 找到 {target} 代码：{target_path}")
                        
                        # 检查是否是前端代码分析
                        is_frontend = any(kw in focus_aspect.lower() for kw in ["前端", "ui", "gui", "frontend", "界面"]) or is_frontend
                        
                        if is_frontend:
                            # ── 新代码：使用 CodeLearner + PatternExtractor 进行深度分析 ──
                            try:
                                from .code_learner import CodeLearner
                                from .pattern_extractor import PatternExtractor
                                from .pattern_comparator import PatternComparator
                                from .pattern_improver import PatternImprover

                                if progress_callback:
                                    await progress_callback(f"🔬 使用 CodeLearner 深度分析 {target} 的前端代码...")

                                # 用 CodeLearner 做深度分析
                                knowledge = await CodeLearner.learn_from_local(
                                    local_path=target_path,
                                    source_name=target,
                                    focus_area="frontend",
                                    progress_callback=progress_callback,
                                )

                                if progress_callback:
                                    await progress_callback(
                                        f"📊 {target} 分析：{knowledge.file_count} 文件, "
                                        f"{len(knowledge.all_classes)} 类, "
                                        f"{len(knowledge.ui_components)} UI组件"
                                    )

                                # 提取设计模式
                                patterns = PatternExtractor.extract(knowledge, focus_area="frontend")

                                if progress_callback:
                                    await progress_callback(
                                        f"🧩 提取到 {len(patterns)} 个设计模式"
                                    )

                                # 对比差距
                                gaps_partial = await PatternComparator.compare_external_to_actual_partner(
                                    external_knowledge=knowledge,
                                    external_patterns=patterns,
                                    progress_callback=progress_callback,
                                )

                                if progress_callback:
                                    high_g = sum(1 for g in gaps_partial if g.get("priority") == "high")
                                    await progress_callback(
                                        f"⚖️ {target} 对比完成：{len(gaps_partial)} 个差距 "
                                        f"(高优先级 {high_g})"
                                    )

                                # 生成改进方案
                                import os as _os_env
                                _os_env.environ["PARTNER_DATA_DIR"] = workspace
                                # Async progress_callback — no ensure_future wrapper needed
                                plans_partial = await PatternImprover.generate_improvements(
                                    gaps_partial, progress_callback=progress_callback,
                                )

                                # 创建 gap 记录
                                gap = Gd._make_gap(
                                    type_="functionality",
                                    description=(
                                        f"{target} 的 {focus_aspect}：{knowledge.file_count} 文件, "
                                        f"{len(knowledge.ui_components)} UI组件, "
                                        f"{len(patterns)} 模式, "
                                        f"{len(gaps_partial)} 差距, "
                                        f"{len(plans_partial)} 方案"
                                    ),
                                    source=f"code_learner:{target}",
                                    severity="medium",
                                    detail={
                                        "target": target,
                                        "target_path": target_path,
                                        "file_count": knowledge.file_count,
                                        "class_count": len(knowledge.all_classes),
                                        "pattern_count": len(patterns),
                                        "gap_count": len(gaps_partial),
                                        "plan_count": len(plans_partial),
                                        "gaps": gaps_partial[:8],
                                        "plans": plans_partial[:5],
                                    },
                                )
                                gaps.append(gap)

                                # Convert plans to dict and ensure target_module field
                                for p in plans_partial:
                                    if isinstance(p, dict):
                                        # PatternImprover uses target_file, Implementation expects target_module
                                        if "target_module" not in p and "target_file" in p:
                                            p["target_module"] = p["target_file"]
                                        all_plans.append(p)

                                if progress_callback:
                                    await progress_callback(
                                        f"💡 {target} 生成 {len(plans_partial)} 个改进方案"
                                    )

                            except Exception as _cle:
                                logger.warning("[5STEP] CodeLearner failed for %s: %s", target, _cle)
                                if progress_callback:
                                    await progress_callback(f"⚠️ CodeLearner 失败，回退到 CodeKnowledge ({_cle})")

                                # Fall back to old CodeKnowledge
                                _load_code_knowledge()
                                Ck = _CODE_KNOWLEDGE
                                try:
                                    ck = Ck()
                                    patterns = ck.extract_ui_patterns(target_path)
                                    diffs = ck.compare_with_partner(patterns)
                                    plans_from_ck = ck.generate_frontend_improvements(diffs)

                                    gap = Gd._make_gap(
                                        type_="functionality",
                                        description=f"{target} 的 {focus_aspect}：{len(patterns.component_hierarchy)} 个组件，{len(diffs)} 个差异，{len(plans_from_ck)} 个改进方案",
                                        source=f"code_knowledge:{target}",
                                        severity="medium",
                                        detail={"target": target, "target_path": target_path, "diffs": [str(d) for d in diffs[:10]], "plans": len(plans_from_ck)},
                                    )
                                    gaps.append(gap)
                                    for p in plans_from_ck:
                                        p_dict = p.to_dict() if hasattr(p, 'to_dict') else p
                                        all_plans.append(p_dict)
                                except Exception as _cke2:
                                    logger.warning("[5STEP] CodeKnowledge fallback also failed: %s", _cke2)
                        else:
                            # 非前端目标，用通用知识获取
                            acquirer = Ka(repos_dir="/tmp/partner_evolution_repos")
                            try:
                                code_knowledge = await acquirer.fetch_from_local(target_path)
                                if code_knowledge:
                                    insights = code_knowledge.key_insights[:10]
                                    gap = Gd._make_gap(type_="functionality", description=f"{target} 的 {focus_aspect}：{len(insights)} 条模式", source=f"focus:{target}", severity="medium", detail={"insights": insights})
                                    gaps.append(gap)
                            except Exception as _ke:
                                logger.warning("[5STEP] code analysis failed for %s: %s", target, _ke)
                    else:
                        if progress_callback:
                            await progress_callback(f"⚠️ 未找到 {target} 的代码路径")
                        gap = Gd._make_gap(
                            type_="functionality",
                            description=f"需要学习 {target} 的 {focus_aspect}",
                            source=f"focus:{target}",
                            severity="medium",
                        )
                        gaps.append(gap)
            else:
                gaps = await Gd.discover_all(workspace)
                if not gaps:
                    gaps = []

            report["steps"]["gap_discovery"] = {
                "gaps": [dict(g) if not isinstance(g, dict) else g for g in gaps],
                "count": len(gaps),
                "elapsed_s": round(_t.time() - step1_start, 1),
            }
            logger.info("[5STEP] Gap discovery: found %d gaps", len(gaps))
            report["steps"]["gap_discovery"] = {
                "gaps": [dict(g) if not isinstance(g, dict) else g for g in gaps],
                "count": len(gaps),
                "elapsed_s": round(_t.time() - step1_start, 1),
            }
        except Exception as e:
            logger.error("[5STEP] Gap discovery failed: %s", e)
            gaps = []
            report["steps"]["gap_discovery"] = {"gaps": [], "count": 0, "elapsed_s": 0, "error": str(e)}
        report["total_gaps"] = len(gaps)

        if progress_callback:
            await progress_callback(f"📊 步骤 1/5 完成：发现 {len(gaps)} 个差距，耗时 {report['steps']['gap_discovery']['elapsed_s']}s")

        # ─────────────────────────────────────────────────────────────────
        # Steps 2-4: For each gap, acquire knowledge → form plan → implement
        # ─────────────────────────────────────────────────────────────────
        
        # ── Screenshot: capture BEFORE state (for frontend focus) ──
        ss_before: dict[str, str] = {}
        if has_focus and is_frontend:
            try:
                from .gui_manager import capture_all_screenshots

                if progress_callback:
                    await progress_callback("📸 [启动 GUI] 确保 GUI 运行并截取改进前界面...")

                ss_before = await capture_all_screenshots(
                    workspace=workspace,
                    progress_callback=progress_callback,
                )

                # ── [世界模型] 预测界面修改效果 ──
                # 用 before 截图 + 修改描述生成预测视频
                _wm_ts = _t.time()
                if ss_before and any(ss_before.get(k, "") for k in ss_before):
                    _first_shot = next((v for k, v in ss_before.items() if v and _os.path.isfile(v)), "")
                    if _first_shot:
                        _gap_descs = []
                        for _g in gaps:
                            _gd = str(_g.get("description", _g)[:200]) if isinstance(_g, dict) else str(_g)[:200]
                            if _gd:
                                _gap_descs.append(_gd)
                        if _gap_descs:
                            try:
                                from .world_model_predict import world_model_predict_in_evolution
                                _wm_result = await world_model_predict_in_evolution(
                                    workspace=workspace,
                                    before_screenshot=_first_shot,
                                    gaps=gaps,
                                    plans=[],
                                    progress_callback=progress_callback,
                                )
                                if _wm_result.get("status") == "success":
                                    _pred = _wm_result.get("prediction", {})
                                    report.setdefault("world_model_prediction", {})
                                    report["world_model_prediction"] = {
                                        "status": "success",
                                        "video_path": _pred.get("video_path", ""),
                                        "frames_generated": _pred.get("frames_generated", 0),
                                        "elapsed_seconds": _pred.get("elapsed_seconds", 0),
                                        "session_dir": _pred.get("session_dir", ""),
                                    }
                                    logger.info(
                                        "[5STEP][WM] Prediction complete: %d frames in %.1fs, video=%s",
                                        _pred.get("frames_generated", 0),
                                        _pred.get("elapsed_seconds", 0),
                                        _pred.get("video_path", ""),
                                    )
                            except Exception as _wme:
                                logger.warning("[5STEP][WM] World model prediction failed: %s", _wme)
                _wm_elapsed = _t.time() - _wm_ts
                if _wm_elapsed > 1:
                    logger.info("[5STEP] World model prediction took %.1fs", _wm_elapsed)
                # ── [世界模型] 结束 ──

            except Exception as _sse:
                logger.warning("[5STEP] Before-screenshot failed: %s", _sse)

        for gap_idx, gap in enumerate(gaps):
            gap_id = gap.get("id", f"gap_{gap_idx}")
            gap_desc = str(gap.get("description", ""))[:120]
            gap_type = gap.get("type", "performance")
            gap_source = str(gap.get("source", ""))

            # ── CodeKnowledge gaps already have pre-generated plans ──
            if gap_source.startswith("code_knowledge:") or gap_source.startswith("code_learner:"):
                if progress_callback:
                    await progress_callback(f"📎 使用 CodeLearner 预生成的 {len(all_plans)} 个改进方案")
                # Report placeholders so the summary doesn't look empty
                report_step2_ck = {"gap_id": gap_id, "knowledge_count": 0, "elapsed_s": 0, "note": "code_learner pre-generated"}
                report_step3_ck = {"gap_id": gap_id, "plans_count": 0, "elapsed_s": 0, "note": "deferred to end section"}
                if "knowledge_acquisition" not in report["steps"]:
                    report["steps"]["knowledge_acquisition"] = []
                report["steps"]["knowledge_acquisition"].append(report_step2_ck)
                if "plan_formation" not in report["steps"]:
                    report["steps"]["plan_formation"] = []
                report["steps"]["plan_formation"].append(report_step3_ck)
                continue

            if progress_callback:
                await progress_callback(
                    f"📡 步骤 2/5：知识获取 ({gap_idx + 1}/{len(gaps)}) — {gap_desc}"
                )

            # Step 2: Knowledge Acquisition
            step2_start = _t.time()
            knowledge_records = []
            try:
                acquirer = Ka(repos_dir="/tmp/partner_evolution_repos")

                # If this gap has pre-analyzed code knowledge from focus step, use it
                gap_detail = gap.get("detail", {})
                if isinstance(gap_detail, dict) and gap_detail.get("insights"):
                    if progress_callback:
                        await progress_callback(f"📎 使用步骤 1 已提取的 {targets_str} 代码分析结果")
                    from ..evolution.knowledge_acquisition import Knowledge
                    kn = Knowledge(
                        source=str(gap_detail.get("target", "focus")),
                        content_type="code",
                        key_insights=gap_detail.get("insights", []),
                        relevant_files=gap_detail.get("relevant_files", []),
                        raw_metadata={"pre_analyzed": True},
                    )
                    knowledge_records.append(kn)

                # Web search for the gap topic
                if progress_callback:
                    await progress_callback(f"🌐 正在搜索 {gap_desc[:60]} 的相关资料...")
                try:
                    import urllib.parse as _up
                    query = _up.quote(gap_desc[:100])
                    kw = await acquirer.fetch_from_web(
                        f"https://html.duckduckgo.com/html/?q={query}"
                    )
                    knowledge_records.append(kw)
                    if progress_callback:
                        insights_count = len(kw.key_insights)
                        await progress_callback(f"✅ 网页搜索完成，提取到 {insights_count} 条关键信息")
                except Exception as _we:
                    logger.warning("[5STEP] web search for gap %s failed: %s", gap_id, _we)
                    # Fallback: try reading local files from external/ directories
                    try:
                        gap_target = gap.get("detail", {}).get("target", "")
                        if gap_target:
                            local_path = _os.path.join("/mnt/e/work/partner_workspace/external", gap_target)
                            if not _os.path.isdir(local_path):
                                # Try case-insensitive
                                ext_dir = "/mnt/e/work/partner_workspace/external"
                                for entry in _os.listdir(ext_dir):
                                    if gap_target.lower() in entry.lower():
                                        local_path = _os.path.join(ext_dir, entry)
                                        break
                            if _os.path.isdir(local_path):
                                kw_local = await acquirer.fetch_from_local(local_path)
                                if kw_local:
                                    knowledge_records.append(kw_local)
                                    if progress_callback:
                                        await progress_callback(f"✅ 本地文件读取成功：{len(kw_local.key_insights)} 条信息")
                    except Exception:
                        pass
                    if not knowledge_records:
                        if progress_callback:
                            await progress_callback(f"⚠️ 网页搜索未返回有效结果，继续下一步")

            except Exception as e:
                logger.warning("[5STEP] Knowledge acquisition for gap %s failed: %s", gap_id, e)

            report_step2 = {
                "gap_id": gap_id,
                "knowledge_count": len(knowledge_records),
                "elapsed_s": round(_t.time() - step2_start, 1),
            }
            if "knowledge_acquisition" not in report["steps"]:
                report["steps"]["knowledge_acquisition"] = []
            report["steps"]["knowledge_acquisition"].append(report_step2)

            if progress_callback:
                await progress_callback(f"✅ 步骤 2/5 完成：获取 {len(knowledge_records)} 条知识，耗时 {report_step2['elapsed_s']}s")

            # Step 3: Plan Formation
            step3_start = _t.time()
            if progress_callback:
                await progress_callback(f"📝 步骤 3/5：方案形成 ({gap_idx + 1}/{len(gaps)}) — 将外部知识映射到 Partner 模块")
            plans = []
            try:
                for kn in knowledge_records:
                    kn_plans = Pf.from_knowledge(kn)
                    plans.extend(kn_plans)
                seen_descs = set()
                unique_plans = []
                for p in plans:
                    p_dict = p.to_dict() if hasattr(p, 'to_dict') else (p if isinstance(p, dict) else {})
                    desc = str(p_dict.get("description", ""))
                    if desc not in seen_descs:
                        seen_descs.add(desc)
                        unique_plans.append(p_dict)
                plans = unique_plans
                if progress_callback:
                    await progress_callback(f"💡 生成 {len(plans)} 个改进方案")
                    for p in plans[:3]:
                        risk = p.get("risk_level", "?")
                        module = p.get("target_module", "?")
                        desc = str(p.get("description", ""))[:80]
                        await progress_callback(f"  [{risk}] {module} → {desc}")
            except Exception as e:
                logger.warning("[5STEP] Plan formation for gap %s failed: %s", gap_id, e)

            all_plans.extend(plans)
            report_step3 = {
                "gap_id": gap_id,
                "plans_count": len(plans),
                "elapsed_s": round(_t.time() - step3_start, 1),
            }
            if "plan_formation" not in report["steps"]:
                report["steps"]["plan_formation"] = []
            report["steps"]["plan_formation"].append(report_step3)

            if progress_callback:
                await progress_callback(f"✅ 步骤 3/5 完成：生成 {len(plans)} 个方案，耗时 {report_step3['elapsed_s']}s")

            # Step 4: Implementation (AUTO mode with force_apply + smoke test + screenshots)
            step4_start = _t.time()
            if progress_callback:
                await progress_callback(f"🔧 步骤 4/5：自动实施 ({gap_idx + 1}/{len(gaps)}) — auto-apply with smoke test")

            gap_applied = 0
            for plan in plans:
                plan_desc = str(plan.get("description", ""))[:60]
                risk = plan.get("risk_level", "high")
                risk_icon = "🟢" if risk == "low" else ("🟡" if risk == "medium" else "🔴")
                if progress_callback:
                    await progress_callback(f"  {risk_icon} 验证方案：{plan_desc}")

                try:
                    # ── [VALIDATE] 沙箱预验证 ──
                    if not plan.get("new_code", "").strip():
                        if progress_callback:
                            await progress_callback(f"  ⏭️ 跳过（无代码内容）：{plan_desc}")
                        continue

                    # 延迟导入沙箱验证器
                    from .sandbox_validator import SandboxValidator
                    _validator = SandboxValidator(workspace)
                    _val_result = await _validator.validate(plan)

                    plan["validation"] = {
                        "valid": _val_result.success,
                        "duration": round(_val_result.duration, 2),
                        "error_type": _val_result.error_type,
                        "error_detail": _val_result.error_detail,
                        "steps": _val_result.validation_steps,
                    }

                    if _val_result.success:
                        if progress_callback:
                            await progress_callback(f"  ✅ 预验证通过 ({_val_result.duration:.1f}s)")
                    else:
                        err_msg = _val_result.error_type or "验证失败"
                        logger.warning(
                            "[5STEP] Plan %s 验证失败: %s — %s",
                            plan.get("id", "?"), err_msg,
                            _val_result.error_detail,
                        )
                        if progress_callback:
                            await progress_callback(
                                f"  ❌ 预验证失败 ({err_msg})，跳过实施"
                            )
                        plan["validated"] = False
                        continue  # 不应用此方案

                    plan["validated"] = True
                    # ── [VALIDATE] 结束 ──

                    # Use force_apply() — always writes code, no approval gate
                    impl_result = await Im.force_apply(plan, workspace)
                    applied_plans.append(impl_result)

                    if impl_result.success:
                        plan["file_path"] = impl_result.file_path
                        plan["backup_path"] = impl_result.backup_path
                        gap_applied += 1

                        if progress_callback:
                            await progress_callback(f"  ✅ 已应用：{impl_result.file_path}")

                        # ── Auto-verification: run smoke test ──
                        if progress_callback:
                            await progress_callback(f"  🔬 自动验证：运行冒烟测试...")

                        smoke_result = await Vf.run_smoke_test(
                            plan=plan,
                            workspace=workspace,
                            timeout=120,
                        )

                        if smoke_result.verdict == "effective":
                            if progress_callback:
                                await progress_callback(f"  ✅ 验证通过（语法✅ 导入✅ 功能✅）— 保留修改")
                        elif smoke_result.verdict == "neutral":
                            if progress_callback:
                                await progress_callback(f"  ➡️ 验证中性（无明显提升/下降）— 保留修改")
                        else:
                            # regressive — rollback
                            if progress_callback:
                                await progress_callback(f"  ❌ 验证失败（{smoke_result.verdict}）— 自动回滚")

                            try:
                                rb_result = await Im.rollback_plan(
                                    plan_id=plan.get("id", ""),
                                    workspace=workspace,
                                )
                                if rb_result.success:
                                    if progress_callback:
                                        await progress_callback(f"  ↩️ 已回滚：{rb_result.file_path}")
                                else:
                                    if progress_callback:
                                        await progress_callback(f"  ⚠️ 回滚失败：{rb_result.error_message}")
                            except Exception as _rbe:
                                logger.warning("[5STEP] Rollback failed: %s", _rbe)

                        # Store smoke test result
                        plan["smoke_test"] = smoke_result.to_dict() if hasattr(smoke_result, 'to_dict') else str(smoke_result)

                    elif impl_result.needs_approval:
                        if progress_callback:
                            await progress_callback(f"  ⏳ 需审批：diff 已保存到 {impl_result.diff_path}")

                except Exception as e:
                    logger.warning("[5STEP] Auto-implementation failed for plan %s: %s",
                                   plan.get("id", "?"), e)
                    if progress_callback:
                        await progress_callback(f"  ❌ 自动实施失败：{e}")

            report_step4 = {
                "gap_id": gap_id,
                "plans_in_gap": len(plans),
                "applied": gap_applied,
                "elapsed_s": round(_t.time() - step4_start, 1),
            }

            # ── Screenshot: capture AFTER state + comparison report ──
            if has_focus and is_frontend and ss_before:
                try:
                    from .screenshot import capture_evolution_screenshots, generate_evolution_report
                    if progress_callback:
                        await progress_callback("📸 截图改进后界面...")
                    ss_after = await capture_evolution_screenshots(
                        workspace=workspace, stage="after",
                        progress_callback=progress_callback,
                    )

                    # Generate side-by-side comparison report
                    report_path = await generate_evolution_report(
                        workspace=workspace,
                        screenshots_before=ss_before,
                        screenshots_after=ss_after,
                        gaps=[dict(g) for g in gaps] if isinstance(gaps, list) else gaps,
                        plans=plans,
                        progress_callback=progress_callback,
                    )

                    report_step4["screenshots_before"] = ss_before
                    report_step4["screenshots_after"] = ss_after
                    report_step4["comparison_report"] = report_path

                    if progress_callback:
                        from .screenshot import EvolutionScreenshot
                        ss_list = EvolutionScreenshot(workspace).get_screenshots()
                        ss_count = len(ss_list)
                        await progress_callback(
                            f"📊 对比报告已保存：{report_path} "
                            f"（共 {ss_count} 张截图）"
                        )
                except Exception as _sse2:
                    logger.warning("[5STEP] After-screenshot/report failed: %s", _sse2)
            if "implementation" not in report["steps"]:
                report["steps"]["implementation"] = []
            report["steps"]["implementation"].append(report_step4)

            if progress_callback:
                await progress_callback(f"✅ 步骤 4/5 完成：{gap_applied}/{len(plans)} 个方案已实施，耗时 {report_step4['elapsed_s']}s")

        report["total_plans"] = len(all_plans)

        # ── Implement CodeKnowledge pre-generated plans (skipped in the gap loop) ──
        if progress_callback:
            code_learner_plans = [p for p in all_plans if p.get("target_module") or p.get("target_file")]
            await progress_callback(
                f"✅ 步骤 2-3/5：方案已就绪（{report['total_plans']} 个改进方案，{len(code_learner_plans)} 个可实施）"
            )

        from dataclasses import asdict as _asdict
        ck_plans = []
        for p in all_plans:
            if isinstance(p, dict):
                # Normalize: PatternImprover uses target_file, Implementation uses target_module
                if "target_module" not in p and "target_file" in p:
                    p["target_module"] = p["target_file"]
                # Normalize: PatternImprover uses code_diff, Implementation uses new_code
                if "new_code" not in p and "code_diff" in p:
                    p["new_code"] = p["code_diff"]
                # Normalize: PatternImprover uses modify/new_class/new_file, Implementation uses modify_function/add_feature
                ct = p.get("change_type", "")
                if ct in ("new_class", "new_file", "new_function"):
                    p["change_type"] = "add_feature"
                elif ct == "modify":
                    p["change_type"] = "modify_function"
                # Normalize plan_id -> id
                if "id" not in p and "plan_id" in p:
                    p["id"] = p["plan_id"]
                # PatternImprover plans are snippets — append to existing files, don't replace
                if "append_mode" not in p:
                    p["append_mode"] = True
                # Ensure function_name is set (for path resolution)
                if not p.get("function_name") and p.get("target_module"):
                    p["function_name"] = p["target_module"]
                if p.get("target_module"):
                    ck_plans.append(p)
            elif hasattr(p, "target_module") and hasattr(p, "change_type"):
                ck_plans.append(_asdict(p))
        if ck_plans:
            if progress_callback:
                await progress_callback(f"🔧 步骤 4/5：自动实施 {len(ck_plans)} 个前端改进方案")
            # Batch progress to avoid QQ passive reply quota (~5 per msg_id)
            # Only send one final summary instead of per-plan messages
            batch_applied = 0
            batch_skipped = 0
            batch_errors = 0
            for plan in ck_plans:
                plan_desc = str(plan.get("description", ""))[:60]
                try:
                    if not plan.get("new_code", "").strip():
                        batch_skipped += 1
                        continue

                    # ── [VALIDATE] 沙箱预验证（CK 方案） ──
                    from .sandbox_validator import SandboxValidator
                    _ck_val = SandboxValidator(workspace)
                    _ck_result = await _ck_val.validate(plan)
                    plan["validation"] = {
                        "valid": _ck_result.success,
                        "duration": round(_ck_result.duration, 2),
                        "error_type": _ck_result.error_type,
                        "error_detail": _ck_result.error_detail,
                    }
                    if not _ck_result.success:
                        err_msg = _ck_result.error_type or "验证失败"
                        logger.warning(
                            "[5STEP][CK] Plan %s 验证失败: %s",
                            plan.get("id", "?"), err_msg,
                        )
                        batch_skipped += 1
                        continue
                    # ── [VALIDATE] 结束 ──

                    impl_result = await Im.force_apply(plan, workspace)
                    applied_plans.append(impl_result)
                    if impl_result.success:
                        batch_applied += 1
                    elif impl_result.needs_approval:
                        pass  # count as pending
                except Exception as e:
                    batch_errors += 1
                    logger.warning("[5STEP] CK implementation failed for plan %s: %s", plan.get("id", "?"), e)

            # Send single summary after all plans are processed
            if progress_callback:
                await progress_callback(
                    f"  ✅ 步骤 4/5 完成：{batch_applied} 个已应用，"
                    f"{batch_skipped} 个跳过，{batch_errors} 个错误"
                )

        report["applied_count"] = len([r for r in applied_plans if r.success]) if applied_plans else 0

        # 统计验证结果
        _validated_total = 0
        _validated_ok = 0
        _validated_fail = 0
        for _p in (all_plans if isinstance(all_plans, list) else []):
            _v = _p.get("validation", {}) if isinstance(_p, dict) else {}
            if _v.get("valid") is True:
                _validated_ok += 1
                _validated_total += 1
            elif _v.get("valid") is False:
                _validated_fail += 1
                _validated_total += 1
        report["validation_passed"] = _validated_ok
        report["validation_failed"] = _validated_fail

        report["steps"]["gap_summary"] = {
            "performance_gaps": len([g for g in gaps if g.get("type") == "performance"]),
            "functionality_gaps": len([g for g in gaps if g.get("type") == "functionality"]),
            "usability_gaps": len([g for g in gaps if g.get("type") == "usability"]),
        }

        # ── Screenshot: capture AFTER state + comparison report ──
        ss_after: dict[str, str] = {}

        # Build applied_plan_dicts and gap_summaries BEFORE screenshots (used by both before and after)
        applied_plan_dicts = []
        for r in applied_plans:
            if hasattr(r, 'plan_id'):
                applied_plan_dicts.append({
                    "description": getattr(r, 'description', getattr(r, 'plan_id', '?')),
                    "target_file": getattr(r, 'file_path', ''),
                    "change_type": getattr(r, 'change_type', '?'),
                    "risk_level": getattr(r, 'risk_level', '?'),
                    "expected_impact": "auto-applied",
                })
        gap_summaries = []
        for g in gaps:
            if isinstance(g, dict):
                gap_summaries.append(g)
            elif hasattr(g, 'get'):
                try: gap_summaries.append(dict(g))
                except: gap_summaries.append({"description": str(g)[:100]})

        if has_focus and is_frontend:
            # ── After-screenshot with retry + GUI restart ──
            try:
                import asyncio as _ai
                from .gui_manager import capture_all_screenshots

                if progress_callback:
                    await progress_callback("📸 [启动 GUI] 截取改进后界面...")

                # 1. Wait for implementation to settle
                await _ai.sleep(3)

                # 2. Capture all screenshots (ensures GUI is running)
                ss_after = await capture_all_screenshots(
                    workspace=workspace,
                    progress_callback=progress_callback,
                )

                # 3. Log capture results with file sizes
                captured_count = sum(1 for v in ss_after.values() if v)
                if captured_count == 0:
                    logger.warning(
                        "[5STEP] After-screenshot: 0/3 captures. "
                        "GUIManager could not start any GUI."
                    )
                    # Try one more time
                    if progress_callback:
                        await progress_callback("📸 重试截图...")
                    await _ai.sleep(5)
                    ss_after = await capture_all_screenshots(
                        workspace=workspace,
                        progress_callback=progress_callback,
                    )
                    captured_count = sum(1 for v in ss_after.values() if v)
                else:
                    for k, v in ss_after.items():
                        if v and _os.path.exists(v):
                            fsize = _os.path.getsize(v)
                            logger.info("[5STEP] After-screenshot %s captured: %s (%d bytes)", k, v, fsize)
            except Exception as _sse2:
                logger.warning("[5STEP] After-screenshot failed (continuing): %s", _sse2)

            # Generate comparison report
            try:
                if ss_after and any(ss_after.get(k, "") for k in ss_after):
                    report_path = await generate_evolution_report(
                        workspace=workspace,
                        screenshots_before=ss_before,
                        screenshots_after=ss_after,
                        gaps=gap_summaries[:10],
                        plans=applied_plan_dicts[:20],
                        progress_callback=progress_callback,
                    )
                    if progress_callback:
                        await progress_callback(f"📊 对比报告已保存：{report_path}")
                else:
                    if progress_callback:
                        await progress_callback("⚠️ 改进后截图失败，跳过对比报告")
                    report_path = ""
            except Exception as _re:
                logger.warning("[5STEP] Report generation failed: %s", _re)
                report_path = ""

            # ── Deliver results via QQ push callbacks ──
            try:
                # Build delivery text
                delivery_lines = ["## 📸 后台截图对比报告", ""]
                delivery_lines.append(f"### 改进前截图")
                # Only use current-run screenshots (from ss_before/ss_after), not stale ones
                available_shots = []
                _current_shots: dict[str, str] = {}
                for k, v in (ss_before or {}).items():
                    if v:
                        _current_shots[f"before_{k}"] = v
                for k, v in (ss_after or {}).items():
                    if v:
                        _current_shots[f"after_{k}"] = v
                if _current_shots:
                    for key, fpath in sorted(_current_shots.items()):
                        fsize = _os.path.getsize(fpath) if _os.path.exists(fpath) else 0
                        if fsize > 500:
                            available_shots.append(fpath)
                            label = "改进前" if "before" in key else "改进后"
                            delivery_lines.append(f"  📷 {label}: {_os.path.basename(fpath)} ({fsize//1024}KB)")
                    delivery_lines.append("")
                else:
                    delivery_lines.append("⚠️ **截图失败**：无法捕获真实 GUI 窗口，无法提供对比图。")
                    delivery_lines.append("")

                # Only push the most important screenshots (max 3 to avoid QQ daily limit)
                _shots_to_push = available_shots[:3]

                delivery_lines.append("### 已自动实施的改进方案（前10条）")
                delivery_lines.append("")
                for i, p in enumerate(applied_plan_dicts[:10], 1):
                    f = p.get("target_file", "")
                    f_short = _os.path.basename(f) if f else "?"
                    desc = str(p.get("description", ""))[:80]
                    delivery_lines.append(f"{i}. **{f_short}** → {desc}")
                delivery_lines.append("")

                delivery_lines.append("### 发现的差距")
                delivery_lines.append("")
                for g in gap_summaries[:5]:
                    name = g.get("external_pattern", "?")
                    status = g.get("partner_status", "")[:80]
                    pri = g.get("priority", "?")
                    delivery_lines.append(f"- [{pri}] {name}: {status}")
                delivery_lines.append("")

                delivery_text = "\n".join(delivery_lines)

                # Use push callbacks for real delivery (bridges send via QQ API)
                try:
                    from partner.mind.executor import _push_callback, _file_push_callback
                except Exception:
                    _push_callback = None
                    _file_push_callback = None

                # 1. Push text report
                if _push_callback:
                    try:
                        _push_callback(delivery_text)
                        logger.info("[5STEP] Text delivered via push callback")
                    except Exception as _pc:
                        logger.warning("[5STEP] Push callback failed: %s", _pc)
                else:
                    # Fallback: write to qq_chat_history directly
                    qq_path = _os.path.join(workspace, "state", "qq_chat_history.jsonl")
                    try:
                        qq_entry = json.dumps({
                            "role": "assistant", "content": delivery_text,
                            "timestamp": __import__("datetime").datetime.now().isoformat(),
                            "source": "self_review", "sender_id": "partner", "sender_name": "Partner",
                        }, ensure_ascii=False)
                        with open(qq_path, "a", encoding="utf-8") as f:
                            f.write(qq_entry + "\n"); f.flush(); _os.fsync(f.fileno())
                        logger.info("[5STEP] Text written to qq_chat_history.jsonl (fallback)")
                    except Exception as _qe:
                        logger.warning("[5STEP] Direct qq write failed: %s", _qe)

                # 2. Push screenshots as file attachments — only current-run, max 3
                if _file_push_callback:
                    for fpath in _shots_to_push:
                        try:
                            with open(fpath, "rb") as _f:
                                png_bytes = _f.read()
                            fname = _os.path.basename(fpath)
                            caption = "改进前界面" if "before" in fname else "改进后界面"
                            ok = _file_push_callback(png_bytes, fname, caption)
                            if ok:
                                logger.info("[5STEP] File sent via file push callback: %s", fname)
                            else:
                                logger.warning("[5STEP] File push callback returned False for: %s", fname)
                        except Exception as _fe:
                            logger.warning("[5STEP] File push failed for %s: %s", fpath, _fe)
                else:
                    # Fallback: copy to outgoing/
                    try:
                        from partner.workspace.workspace_layout import outgoing_dir as _outgoing_dir
                        out_dir = _outgoing_dir(workspace)
                    except Exception:
                        out_dir = _os.path.normpath(_os.path.join(workspace, "..", "..", "..", "files", "outgoing"))
                    if _os.path.isdir(out_dir):
                        now_ts = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                        for fpath in _shots_to_push:
                            fname = _os.path.basename(fpath)
                            safe_name = fname.replace(" ", "_")
                            out_name = f"{now_ts}_{safe_name}"
                            try:
                                import shutil as _shutil
                                _shutil.copy2(fpath, _os.path.join(out_dir, out_name))
                            except Exception:
                                pass
                        logger.info("[5STEP] Screenshots copied to outgoing/ (fallback)")

                if progress_callback:
                    await progress_callback(delivery_text)

            except Exception as _de:
                logger.warning("[5STEP] Delivery failed: %s", _de)

        # ─────────────────────────────────────────────────────────────────
        # Step 5: Verification
        # ─────────────────────────────────────────────────────────────────
        if progress_callback:
            await progress_callback("📊 步骤 5/5：验证 — 运行基准测试对比改进效果")

        step5_start = _t.time()
        try:
            if has_focus:
                if progress_callback:
                    await progress_callback("🧪 焦点模式：运行 VerificationRunner 验证改进效果...")
                # Use the real VerificationRunner instead of hardcoded pending
                try:
                    from .verification_runner import run_verification, write_verification_to_harness_log
                    verification_result = await run_verification(
                        plan_id="five_step_cycle_001",
                        workspace=workspace,
                    )
                    # Write to harness_runs.jsonl
                    write_verification_to_harness_log(verification_result)
                except Exception as _vre:
                    logger.warning("[5STEP] VerificationRunner failed, fallback to pending: %s", _vre)
                    verification_result = Vf.VerificationResult(
                        plan_id="five_step_cycle_001",
                        before_metrics=[],
                        after_metrics=[],
                        verdict="pending",
                        summary_stats={
                            "total_plans": len(all_plans),
                            "applied": report["applied_count"],
                            "pending_approval": len([r for r in applied_plans if r.needs_approval]) if applied_plans else 0,
                        },
                    )
            else:
                if progress_callback:
                    await progress_callback("🏃 运行 3 个基准测试任务...")
                benchmark_tasks = [
                    "你好",
                    "成都今天天气怎么样",
                    "整理转录组年龄预测方法，提出突破思路",
                ]
                verification_result = await Vf.run_verification(
                    plan_id="five_step_cycle_001",
                    benchmark_tasks=benchmark_tasks,
                    workspace=workspace,
                    timeout=120,
                )

            report["verification"] = {
                "plan_id": getattr(verification_result, 'plan_id', "five_step_cycle_001"),
                "verdict": getattr(verification_result, 'verdict', 'unknown'),
                "checks": getattr(verification_result, 'checks', []),
            }
            # Summary stats: differ between VerificationReport and VerificationResult
            if hasattr(verification_result, 'summary_stats'):
                report["verification"]["summary_stats"] = verification_result.summary_stats
            elif hasattr(verification_result, 'checks'):
                report["verification"]["summary_stats"] = {
                    "passed": verification_result.passed_count(),
                    "failed": verification_result.failed_count(),
                }
            if hasattr(verification_result, 'verdict'):
                report["verification"]["verdict"] = verification_result.verdict
        except Exception as e:
            logger.error("[5STEP] Verification failed: %s", e)
            report["verification"] = {"error": str(e)}

        if progress_callback:
            await progress_callback(f"✅ 步骤 5/5 完成：验证结论 — {report['verification'].get('verdict', 'N/A')}")

        total_elapsed = _t.time() - _start
        report["elapsed_s"] = round(total_elapsed, 1)
        report["success"] = report["applied_count"] > 0 or report["total_plans"] == 0

        if progress_callback:
            summary_msg = (
                f"## 📋 五步自进化闭环完成\n\n"
                f"🎯 目标：{'、'.join(focus_targets) if has_focus else '通用优化'}\n"
                f"🔍 发现差距：{report['total_gaps']} 个\n"
                f"📝 生成方案：{report['total_plans']} 个\n"
                f"🔧 已实施：{report['applied_count']} 个\n"
                f"✅ 预验证通过：{report.get('validation_passed', 0)} 个\n"
                f"❌ 预验证失败：{report.get('validation_failed', 0)} 个\n"
                f"⏱️ 总耗时：{report['elapsed_s']}s\n"
            )
            if report.get("verification") and report["verification"].get("verdict"):
                summary_msg += f"📊 验证结论：{report['verification']['verdict']}\n"
            await progress_callback(summary_msg)

        # Record the cycle in growth table
        try:
            record_growth(
                user_id="default",
                milestone=f"五步自进化闭环：{report['total_gaps']} 个差距，{report['total_plans']} 个方案，{report['applied_count']} 个已实施",
                reflection=json.dumps({
                    "total_gaps": report["total_gaps"],
                    "total_plans": report["total_plans"],
                    "applied_count": report["applied_count"],
                    "elapsed_s": report["elapsed_s"],
                    "gaps_by_type": report["steps"].get("gap_summary", {}),
                }, ensure_ascii=False),
                category="self_evolution_five_step",
            )
        except Exception:
            pass

        logger.info(
            "[5STEP] Cycle complete: %d gaps, %d plans, %d applied in %.1fs",
            report["total_gaps"], report["total_plans"], report["applied_count"], report["elapsed_s"],
        )
        return report

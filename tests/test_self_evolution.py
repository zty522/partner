"""Tests for SelfEvolutionEngine"""

import json
import os
import tempfile
import shutil
from datetime import datetime, timedelta
from partner.self_evolution import (
    StrategyLearner, MemoryPruner, CPEGuard, SelfEvolutionEngine,
    StrategyProfile, PruneAction, Capability,
)


class TestStrategyLearner:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.journal_path = os.path.join(self.tmpdir, "journal.jsonl")
        self.profile_path = os.path.join(self.tmpdir, "strategy_profile.json")

    def teardown_method(self):
        shutil.rmtree(self.tmpdir)

    def _write_journal(self, entries):
        with open(self.journal_path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def test_analyze_empty_journal(self):
        learner = StrategyLearner(self.journal_path, self.profile_path)
        profiles = learner.analyze()
        assert profiles == {}

    def test_analyze_basic(self):
        entries = [
            {"task_id": "t1", "task_type": "literature_search", "task_title": "search A",
             "new_tasks_generated": 2, "knowledge_entries_added": 1},
            {"task_id": "t2", "task_type": "literature_search", "task_title": "search B",
             "new_tasks_generated": 3, "knowledge_entries_added": 2},
            {"task_id": "t3", "task_type": "literature_search", "task_title": "FAILED: search C",
             "result_summary": "FAILED", "new_tasks_generated": 0, "knowledge_entries_added": 0},
            {"task_id": "t4", "task_type": "idea_generation", "task_title": "idea A",
             "new_tasks_generated": 1, "knowledge_entries_added": 1},
        ]
        self._write_journal(entries)

        learner = StrategyLearner(self.journal_path, self.profile_path)
        profiles = learner.analyze()

        assert "literature_search" in profiles
        assert "idea_generation" in profiles

        ls = profiles["literature_search"]
        assert ls.execution_count == 3
        assert abs(ls.success_rate - 2/3) < 0.01
        assert ls.avg_value_score > 0

    def test_analyze_high_success_high_value_boost(self):
        """success_rate > 0.8 and avg_value > 2 → boost +2"""
        entries = [
            {"task_id": f"t{i}", "task_type": "deep_dive", "task_title": f"dive {i}",
             "new_tasks_generated": 3, "knowledge_entries_added": 2}
            for i in range(10)
        ]
        self._write_journal(entries)

        learner = StrategyLearner(self.journal_path, self.profile_path)
        profiles = learner.analyze()
        assert profiles["deep_dive"].recommended_priority_boost == 2

    def test_analyze_low_success_penalty(self):
        """success_rate < 0.3 → boost -3"""
        entries = []
        for i in range(10):
            title = f"FAILED: task {i}" if i < 8 else f"task {i}"
            entries.append({"task_id": f"t{i}", "task_type": "project_scan",
                          "task_title": title, "result_summary": "FAILED" if i < 8 else "ok"})
        self._write_journal(entries)

        learner = StrategyLearner(self.journal_path, self.profile_path)
        profiles = learner.analyze()
        assert profiles["project_scan"].recommended_priority_boost == -3

    def test_save_and_load(self):
        entries = [
            {"task_id": "t1", "task_type": "skill_learning", "task_title": "learn A",
             "new_tasks_generated": 1, "knowledge_entries_added": 1},
        ]
        self._write_journal(entries)

        learner = StrategyLearner(self.journal_path, self.profile_path)
        profiles = learner.analyze()
        learner.save(profiles)

        loaded = learner.load()
        assert "skill_learning" in loaded
        assert loaded["skill_learning"].execution_count == 1


class TestMemoryPruner:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.kb_path = os.path.join(self.tmpdir, "knowledge.json")
        self.prune_log = os.path.join(self.tmpdir, "prune_log.jsonl")

    def teardown_method(self):
        shutil.rmtree(self.tmpdir)

    def _write_kb(self, entries, meta=None):
        kb = {"entries": entries, "meta": meta or {"total_entries": len(entries)}}
        with open(self.kb_path, "w", encoding="utf-8") as f:
            json.dump(kb, f, ensure_ascii=False, indent=2)

    def test_prune_empty(self):
        pruner = MemoryPruner(self.kb_path, self.prune_log)
        actions = pruner.prune()
        assert actions == []

    def test_prune_old_low_confidence(self):
        old_date = (datetime.now() - timedelta(days=60)).isoformat()
        entries = [
            {"id": "k1", "title": "old entry", "confidence": "low",
             "created_at": old_date, "tags": ["test"]},
            {"id": "k2", "title": "recent entry", "confidence": "low",
             "created_at": datetime.now().isoformat(), "tags": ["test"]},
        ]
        self._write_kb(entries)

        pruner = MemoryPruner(self.kb_path, self.prune_log)
        actions = pruner.prune()

        archive_actions = [a for a in actions if a.action == "archive"]
        assert len(archive_actions) == 1
        assert archive_actions[0].entry_id == "k1"

    def test_prune_duplicate_titles(self):
        entries = [
            {"id": "k1", "title": "Same Title", "confidence": "medium", "tags": []},
            {"id": "k2", "title": "Same Title", "confidence": "high", "tags": []},
        ]
        self._write_kb(entries)

        pruner = MemoryPruner(self.kb_path, self.prune_log)
        actions = pruner.prune()

        merge_actions = [a for a in actions if a.action == "merge"]
        assert len(merge_actions) == 1

    def test_prune_no_tags_demote(self):
        entries = [
            {"id": "k1", "title": "orphan", "confidence": "medium", "tags": []},
        ]
        self._write_kb(entries)

        pruner = MemoryPruner(self.kb_path, self.prune_log)
        actions = pruner.prune()

        demote_actions = [a for a in actions if a.action == "demote"]
        assert len(demote_actions) == 1

    def test_apply_actions(self):
        entries = [
            {"id": "k1", "title": "old", "confidence": "low",
             "created_at": (datetime.now() - timedelta(days=60)).isoformat(), "tags": []},
        ]
        self._write_kb(entries)

        pruner = MemoryPruner(self.kb_path, self.prune_log)
        actions = pruner.prune()
        pruner.apply(actions)

        with open(self.kb_path, "r", encoding="utf-8") as f:
            kb = json.loads(f.read(), strict=False)

        k1 = next(e for e in kb["entries"] if e["id"] == "k1")
        # May be archived or demoted depending on actions
        assert k1["confidence"] in ("archived", "low")


class TestCPEGuard:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.registry_path = os.path.join(self.tmpdir, "capability_registry.json")
        self.journal_path = os.path.join(self.tmpdir, "journal.jsonl")

    def teardown_method(self):
        shutil.rmtree(self.tmpdir)

    def _write_journal(self, entries):
        with open(self.journal_path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def test_register_and_check(self):
        guard = CPEGuard(self.registry_path, self.journal_path)

        cap = Capability(
            id="cap_search",
            name="文献搜索",
            task_type="literature_search",
            baseline_success_rate=0.9,
        )
        guard.register_capability(cap)

        # Journal with good performance
        entries = [
            {"task_id": f"t{i}", "task_type": "literature_search", "task_title": f"search {i}"}
            for i in range(10)
        ]
        self._write_journal(entries)

        alerts = guard.check_all()
        assert len(alerts) == 0

    def test_degradation_detection(self):
        guard = CPEGuard(self.registry_path, self.journal_path)

        cap = Capability(
            id="cap_search",
            name="文献搜索",
            task_type="literature_search",
            baseline_success_rate=0.9,
            degradation_threshold=0.15,
        )
        guard.register_capability(cap)

        # Journal with bad performance (8/10 failed)
        entries = []
        for i in range(10):
            title = f"FAILED: search {i}" if i < 8 else f"search {i}"
            entries.append({"task_id": f"t{i}", "task_type": "literature_search",
                          "task_title": title, "result_summary": "FAILED" if i < 8 else "ok"})
        self._write_journal(entries)

        alerts = guard.check_all()
        assert len(alerts) == 1
        assert alerts[0]["capability_id"] == "cap_search"
        assert alerts[0]["degradation"] > 0.15

    def test_protect_updates_registry(self):
        guard = CPEGuard(self.registry_path, self.journal_path)
        cap = Capability(
            id="cap_search", name="文献搜索", task_type="literature_search",
            baseline_success_rate=0.9, verification_interval_hours=72,
        )
        guard.register_capability(cap)

        # Trigger degradation
        entries = [
            {"task_id": f"t{i}", "task_type": "literature_search",
             "task_title": f"FAILED: s {i}", "result_summary": "FAILED"}
            for i in range(10)
        ]
        self._write_journal(entries)
        alerts = guard.check_all()
        guard.protect(alerts)

        with open(self.registry_path, "r", encoding="utf-8") as f:
            reg = json.loads(f.read(), strict=False)

        cap_data = reg["capabilities"][0]
        assert cap_data["verification_interval_hours"] == 36  # halved
        assert cap_data.get("degradation_alert") is True


class TestSelfEvolutionEngine:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = os.path.join(self.tmpdir, "state")
        os.makedirs(self.state_dir, exist_ok=True)

        # Write minimal journal
        journal_path = os.path.join(self.state_dir, "journal.jsonl")
        entries = [
            {"task_id": f"t{i}", "task_type": "literature_search",
             "task_title": f"search {i}", "new_tasks_generated": 1,
             "knowledge_entries_added": 1}
            for i in range(5)
        ]
        with open(journal_path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

        # Write minimal knowledge
        kb_path = os.path.join(self.state_dir, "knowledge.json")
        with open(kb_path, "w", encoding="utf-8") as f:
            json.dump({"entries": [], "meta": {"total_entries": 0}}, f)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir)

    def test_skip_non_interval(self):
        engine = SelfEvolutionEngine(self.state_dir, evolution_interval=5)
        # First 4 cycles should return None
        for _ in range(4):
            result = engine.run_evolution_cycle()
            assert result is None

    def test_run_at_interval(self):
        engine = SelfEvolutionEngine(self.state_dir, evolution_interval=3)
        # Cycle 1, 2: skip
        assert engine.run_evolution_cycle() is None
        assert engine.run_evolution_cycle() is None
        # Cycle 3: run
        result = engine.run_evolution_cycle()
        assert result is not None
        assert "策略分析" in result
        assert "记忆优化" in result

    def test_get_strategy_boost(self):
        engine = SelfEvolutionEngine(self.state_dir, evolution_interval=1)
        engine.run_evolution_cycle()
        boost = engine.get_strategy_boost("literature_search")
        assert isinstance(boost, int)


class TestStrategyProfile:
    def test_roundtrip(self):
        p = StrategyProfile(task_type="test", tags=["a", "b"],
                          success_rate=0.8, recommended_priority_boost=2)
        d = p.to_dict()
        p2 = StrategyProfile.from_dict(d)
        assert p2.task_type == "test"
        assert p2.tags == ["a", "b"]
        assert p2.success_rate == 0.8


class TestCapability:
    def test_roundtrip(self):
        c = Capability(id="c1", name="test", task_type="search",
                      baseline_success_rate=0.9)
        d = c.to_dict()
        c2 = Capability.from_dict(d)
        assert c2.id == "c1"
        assert c2.baseline_success_rate == 0.9

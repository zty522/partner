"""self_review 能力匹配回归 —— 缺口误报修复不复发"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from partner.evolution.self_review import SelfReview, CapabilityInventory


class TestCapTokens:
    def setup_method(self):
        self.sr = SelfReview(workspace="/tmp")

    def test_composite_word_split(self):
        assert self.sr._cap_tokens("blast_search") == {"blast", "search"}

    def test_single_token(self):
        assert self.sr._cap_tokens("blast") == {"blast"}

    def test_underscore_vs_space_normalized(self):
        # 分隔符差异不应导致误判：single cell（空格）vs single_cell（下划线）
        assert self.sr._cap_tokens("single cell") == self.sr._cap_tokens("single_cell") == {"single", "cell"}

    def test_case_insensitive(self):
        assert self.sr._cap_tokens("BLAST") == {"blast"}

    def test_intersection_matching(self):
        # blast → blast_search 词交集非空 → 覆盖
        assert bool(self.sr._cap_tokens("blast") & self.sr._cap_tokens("blast_search"))
        assert bool(self.sr._cap_tokens("protein") & self.sr._cap_tokens("protein_structure"))
        assert bool(self.sr._cap_tokens("molecular") & self.sr._cap_tokens("molecular_generation"))


def _inventory(agents=None):
    return CapabilityInventory(
        agents=agents or [
            {"name": "bioinformatics", "capabilities": ["blast_search", "sequence_analysis"], "health_status": "ok"},
            {"name": "bionemo", "capabilities": ["protein_structure"], "health_status": "ok"},
            {"name": "cytobridge", "capabilities": ["single_cell", "trajectory_inference"], "health_status": "ok"},
            {"name": "pocketflow", "capabilities": ["molecular_generation"], "health_status": "ok"},
        ],
        skill_count=9,
        event_types=["execute_code", "read_file"],
        experience_stats={},
        weaknesses=[],
    )


class TestIdentifyGapsRegression:
    """回归：曾经误报的缺口（BLAST/AlphaFold/Scanpy 等）不得再出现。"""

    def setup_method(self):
        self.sr = SelfReview(workspace="/tmp")
        self.inv = _inventory()

    def _gap_names(self):
        return [g.name for g in self.sr.identify_gaps(self.inv)]

    def test_blast_not_reported(self):
        names = self._gap_names()
        assert not any("blast" in n.lower() for n in names), names

    def test_alphafold_not_reported(self):
        names = self._gap_names()
        assert not any("alphafold" in n.lower() for n in names), names

    def test_scanpy_not_reported(self):
        names = self._gap_names()
        assert not any("scanpy" in n.lower() for n in names), names

    def test_diffdock_not_reported(self):
        # molecular 能力覆盖 diffdock 的 molecular 词
        names = self._gap_names()
        assert not any("diffdock" in n.lower() for n in names), names

    def test_no_single_cell_weakness(self):
        names = self._gap_names()
        assert not any("单细胞" in n for n in names), names

    def test_real_gap_still_reported(self):
        # 变体调用（variant calling）无人覆盖 → GATK 应仍报缺口
        names = self._gap_names()
        assert any("GATK" in n for n in names), names

    def test_low_success_rate_gap(self):
        inv = _inventory()
        inv.experience_stats = {"by_type": {"docking": {"total": 10, "success_rate": 0.2}}}
        names = [g.name for g in self.sr.identify_gaps(inv)]
        assert any("成功率不足" in n for n in names), names


class TestDeriveWeaknessesRegression:
    """weaknesses 衍生缺口：分隔符匹配回归（single cell vs single_cell）。"""

    def setup_method(self):
        self.sr = SelfReview(workspace="/tmp")

    def test_single_cell_agent_no_weakness(self):
        agents = [{"name": "cytobridge", "capabilities": ["single_cell", "trajectory_inference"]}]
        ws = self.sr._derive_weaknesses({}, agents)
        assert not any("单细胞" in w for w in ws), ws

    def test_genome_annotation_weakness_when_missing(self):
        agents = [{"name": "cytobridge", "capabilities": ["single_cell"]}]
        ws = self.sr._derive_weaknesses({}, agents)
        assert any("基因组注释" in w for w in ws), ws

    def test_sequence_alignment_covered_by_substring(self):
        # sequence_translate 含 sequence → 序列比对类不误报
        agents = [{"name": "bioinformatics", "capabilities": ["sequence_translate"]}]
        ws = self.sr._derive_weaknesses({}, agents)
        assert not any("序列比对" in w for w in ws), ws

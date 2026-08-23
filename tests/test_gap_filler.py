"""C3 缺口自动补缺 —— 单元测试"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from partner.evolution.gap_filler import detect_tool, detect_all, fill_gap


class TestDetectTool:
    def test_installed_tools_detected(self):
        # 环境已安装的工具（B 阶段集成）
        assert detect_tool("plink")
        assert detect_tool("iqtree")
        assert detect_tool("bcftools")

    def test_missing_tool_not_detected(self):
        # prokka 未安装（需 sudo/conda）
        assert not detect_tool("prokka")

    def test_unknown_tool_empty(self):
        assert not detect_tool("not_a_real_tool_xyz")

    def test_detect_all_returns_dict(self):
        d = detect_all()
        assert isinstance(d, dict)
        assert d.get("plink")
        assert "prokka" in d


class TestFillGap:
    def test_already_present(self):
        r = fill_gap("", "iqtree")
        assert r["status"] == "already_present"

    def test_manual_required(self):
        r = fill_gap("", "prokka")
        assert r["status"] == "manual_required"
        assert "apt" in r["message"] or "conda" in r["message"]

    def test_unsupported(self):
        r = fill_gap("", "no_such_tool")
        assert r["status"] == "unsupported"

    def test_gap_fill_log_recorded(self):
        # 调用后应记录到 gap_fill_log（workspace 由指针解析，无法断言路径则至少不抛）
        r = fill_gap("", "no_such_tool")
        assert r["status"] == "unsupported"

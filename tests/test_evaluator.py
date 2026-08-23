"""C1 质量评估器 / C2 失败反思库 / C4 技能卡片 —— 单元测试"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from partner.evolution.evaluator import (
    evaluate_outputs,
    record_failure,
    load_recent_failures,
    record_success,
    load_recent_successes,
)


def _write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class TestEvaluateOutputs:
    def test_no_files_zero(self):
        r = evaluate_outputs([])
        assert r["score"] == 0
        assert any("无产出" in x for x in r["reasons"])

    def test_good_file_full_score(self, tmp_path):
        p = _write(str(tmp_path / "report.md"), "# 标题\n\n" + "内容" * 100)
        r = evaluate_outputs([p])
        assert r["score"] == 100, r

    def test_empty_file_deducted(self, tmp_path):
        p = str(tmp_path / "empty.md")
        open(p, "w").close()
        r = evaluate_outputs([p])
        assert r["score"] == 60, r  # 有文件40 + 非空0 + 非模板20 + 实质0... 校验: 40+0+20+0=60

    def test_template_placeholder_deducted(self, tmp_path):
        # 内容足够长（≥100 字）但含模板占位 → 只扣模板分 20 → 80
        p = _write(str(tmp_path / "tpl.md"), "# 目标\nTODO\n待补充内容\n" + "填充内容。" * 40)
        r = evaluate_outputs([p])
        assert r["score"] == 80, r  # 40+20+0+20=80（模板扣 20）

    def test_mixed_files(self, tmp_path):
        good = _write(str(tmp_path / "a.md"), "x" * 300)
        empty = str(tmp_path / "b.md")
        open(empty, "w").close()
        r = evaluate_outputs([good, empty])
        assert 60 <= r["score"] <= 100


class TestFailureReflections:
    def test_record_and_load(self, tmp_path):
        ws = str(tmp_path)
        path = record_failure("03", ws, task_title="测试任务", failure_type="no_output",
                              reason="连续2轮无产出", round_num=1)
        assert path and os.path.exists(path)
        rows = load_recent_failures(ws, instance_id="03", limit=5)
        assert rows and rows[0]["failure_type"] == "no_output"
        assert rows[0]["instance"] == "03"

    def test_filter_by_instance(self, tmp_path):
        ws = str(tmp_path)
        record_failure("01", ws, task_title="t1", failure_type="low_quality", reason="r")
        record_failure("02", ws, task_title="t2", failure_type="no_output", reason="r")
        rows = load_recent_failures(ws, instance_id="02", limit=10)
        assert len(rows) == 1 and rows[0]["instance"] == "02"

    def test_limit(self, tmp_path):
        ws = str(tmp_path)
        for i in range(5):
            record_failure("03", ws, task_title=f"t{i}", failure_type="x", reason="r")
        rows = load_recent_failures(ws, instance_id="03", limit=2)
        assert len(rows) == 2


class TestSkillCards:
    def test_record_and_load(self, tmp_path, monkeypatch):
        # C4 固定写根级指针路径：monkeypatch 指针指向 tmp
        import partner.api_log as api_log_mod
        monkeypatch.setattr(api_log_mod, "workspace_root_from_pointer", lambda: str(tmp_path))
        record_success("03", str(tmp_path), task_title="分子生成", files=["a.py"], summary="VAE 训练完成")
        rows = load_recent_successes(str(tmp_path), instance_id="03", limit=5)
        assert rows and rows[0]["task_title"] == "分子生成"
        assert rows[0]["files"] == ["a.py"]
        # 确认写入位置是 share/mind（根级共享语义）
        assert os.path.exists(str(tmp_path / "share" / "mind" / "skill_cards.jsonl"))

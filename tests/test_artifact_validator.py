"""产物验收 —— 单元测试"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from partner.harness_core.artifact_validator import ArtifactValidator


def _task(workdir, artifacts):
    return SimpleNamespace(
        working_dir=workdir,
        expected_artifacts=artifacts,
        append_log=lambda *a, **k: None,
    )


class TestArtifactValidator:
    def test_matching_file_found(self, tmp_path):
        open(str(tmp_path / "report.md"), "w").write("x" * 10)
        t = _task(str(tmp_path), [{"type": "file", "pattern": "*.md", "description": "报告", "required": True}])
        v = ArtifactValidator(None).validate(t)
        assert v.ok
        assert len(v.found) == 1

    def test_missing_required_fails(self, tmp_path):
        t = _task(str(tmp_path), [{"type": "file", "pattern": "*.md", "description": "报告", "required": True}])
        v = ArtifactValidator(None).validate(t)
        assert not v.ok
        assert len(v.missing) == 1

    def test_png_pattern_accepts_jpg(self, tmp_path):
        # web_capture 输出 jpg 是已知行为：*.png 要求应兼容 .jpg
        open(str(tmp_path / "shot.jpg"), "wb").write(b"\xff\xd8\xff" + b"0" * 2000)
        t = _task(str(tmp_path), [{"type": "file", "pattern": "*.png", "description": "截图", "required": True}])
        v = ArtifactValidator(None).validate(t)
        assert v.ok, v.missing

    def test_internal_artifacts_excluded(self, tmp_path):
        open(str(tmp_path / "_step_step1.result.json"), "w").write("{}")
        open(str(tmp_path / "_error_report.md"), "w").write("x")
        open(str(tmp_path / "real.md"), "w").write("real" * 10)
        t = _task(str(tmp_path), [{"type": "file", "pattern": "*.md", "description": "报告", "required": True}])
        v = ArtifactValidator(None).validate(t)
        assert v.ok
        # 只匹配到 real.md
        found_paths = [p for f in v.found for p in f.get("paths", [])]
        assert len(found_paths) == 1
        assert found_paths[0].endswith("real.md")

    def test_optional_missing_ok(self, tmp_path):
        t = _task(str(tmp_path), [{"type": "file", "pattern": "*.md", "description": "报告", "required": False}])
        v = ArtifactValidator(None).validate(t)
        assert v.ok
        assert len(v.missing) == 0

    def test_global_old_screenshot_cannot_satisfy_current_task(self, tmp_path, monkeypatch):
        old_dir = tmp_path / "global_screenshots"
        old_dir.mkdir()
        (old_dir / "old.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 2000)
        task_dir = tmp_path / "current_task"
        task_dir.mkdir()
        monkeypatch.setattr("partner.utils.workspace.get_screenshots_dir", lambda: str(old_dir))
        t = _task(str(task_dir), [{"type": "file", "pattern": "*.png", "description": "当前截图", "required": True}])
        result = ArtifactValidator(None).validate(t)
        assert not result.ok

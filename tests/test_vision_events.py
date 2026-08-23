"""读图事件 —— 配置解析/workspace 上溯/最近图回退 单元测试"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from partner.v2.vision_events import (
    _find_workspace_root,
    _load_qwen_vision_cfg,
    _find_recent_image,
)


class TestFindWorkspaceRoot:
    def test_root_with_api_json(self, tmp_path):
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "api.json").write_text("{}")
        nested = tmp_path / "instances" / "03"
        nested.mkdir(parents=True)
        assert _find_workspace_root(str(nested)) == str(tmp_path)

    def test_root_itself(self, tmp_path):
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "api.json").write_text("{}")
        assert _find_workspace_root(str(tmp_path)) == str(tmp_path)

    def test_no_api_json_returns_empty(self, tmp_path):
        assert _find_workspace_root(str(tmp_path)) == ""


class TestLoadQwenVisionCfg:
    def _setup_api(self, tmp_path, model="qwen-image-3.0", vision_model=None):
        (tmp_path / "config").mkdir(exist_ok=True)
        apis = {"qwen": {"api_key": "sk-test", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                         "model": model}}
        if vision_model:
            apis["qwen"]["vision_model"] = vision_model
        (tmp_path / "config" / "api.json").write_text(json.dumps({"apis": apis}, ensure_ascii=False), encoding="utf-8")
        return str(tmp_path)

    def test_vision_model_priority(self, tmp_path):
        ws = self._setup_api(tmp_path, model="qwen-image-3.0", vision_model="qwen3-vl-flash")
        cfg = _load_qwen_vision_cfg(ws)
        assert cfg["model"] == "qwen3-vl-flash"  # vision_model 优先

    def test_fallback_to_model(self, tmp_path):
        ws = self._setup_api(tmp_path, model="qwen-image-3.0")
        cfg = _load_qwen_vision_cfg(ws)
        assert cfg["model"] == "qwen-image-3.0"

    def test_missing_key_returns_empty(self, tmp_path):
        (tmp_path / "config").mkdir(exist_ok=True)
        (tmp_path / "config" / "api.json").write_text(json.dumps({"apis": {"qwen": {"model": "x"}}}), encoding="utf-8")
        assert _load_qwen_vision_cfg(str(tmp_path)) == {}

    def test_workspace_auto_ascend(self, tmp_path):
        ws = self._setup_api(tmp_path)
        instance = tmp_path / "instances" / "05"
        instance.mkdir(parents=True)
        cfg = _load_qwen_vision_cfg(str(instance))
        assert cfg["model"]  # 实例路径自动上溯到根


class TestFindRecentImage:
    def test_finds_most_recent(self, tmp_path):
        a = tmp_path / "old.png"
        b = tmp_path / "new.jpg"
        a.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
        b.write_bytes(b"\xff\xd8\xff" + b"0" * 100)
        # 修改 mtime 确保 new 更新
        old_t = time.time() - 100
        os.utime(a, (old_t, old_t))
        found = _find_recent_image(str(tmp_path), str(tmp_path))
        assert found == str(b)

    def test_ignores_non_image(self, tmp_path):
        (tmp_path / "note.md").write_text("# hi")
        assert _find_recent_image(str(tmp_path), str(tmp_path)) == ""

    def test_workdir_then_workspace(self, tmp_path):
        wd = tmp_path / "wd"
        wd.mkdir()
        (wd / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
        found = _find_recent_image(str(tmp_path), str(wd))
        assert found == str(wd / "shot.png")

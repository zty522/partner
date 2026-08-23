"""代码运行日志 —— 单元测试"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from partner.tools.run_log import log_code_run, recent_code_runs


class TestRunLog:
    def test_write_and_read(self, tmp_path):
        ws = str(tmp_path)
        p = log_code_run(ws, event="execute_code", workdir="/tmp/x",
                         script="/tmp/x/_execute_code.py", exit_code=0, ok=True,
                         stdout="hello output", stderr="")
        assert p and os.path.exists(p)
        rows = recent_code_runs(ws, limit=10)
        assert len(rows) == 1
        r = rows[0]
        assert r["event"] == "execute_code"
        assert r["exit_code"] == 0
        assert r["ok"] is True
        assert r["stdout_preview"] == "hello output"
        assert r["script"] == "_execute_code.py"

    def test_failed_run_recorded(self, tmp_path):
        ws = str(tmp_path)
        log_code_run(ws, event="run_command", workdir="/tmp", script="ls -la",
                     exit_code=1, ok=False, stdout="", stderr="ls: cannot access")
        rows = recent_code_runs(ws, limit=10)
        assert rows[0]["ok"] is False
        assert rows[0]["exit_code"] == 1
        assert rows[0]["stderr_preview"] == "ls: cannot access"

    def test_limit(self, tmp_path):
        ws = str(tmp_path)
        for i in range(5):
            log_code_run(ws, event="execute_code", workdir="/tmp", script="s.py",
                         exit_code=0, ok=True, stdout=f"out{i}")
        rows = recent_code_runs(ws, limit=2)
        assert len(rows) == 2
        assert rows[-1]["stdout_preview"] == "out4"

    def test_never_raises_on_bad_workspace(self):
        # 非法路径不抛异常
        p = log_code_run("/nonexistent_dir_xyz", event="execute_code", workdir="",
                         script="", exit_code=0, ok=True, stdout="", stderr="")
        assert p == ""

import json
import os
import time
from datetime import datetime


class RestartTracker:
    """重启计数器：限制每小时最多 3 次自动重启。"""

    MAX_RESTARTS_PER_HOUR = 3

    def __init__(self, workspace: str):
        self.workspace = workspace
        self.log_path = os.path.join(workspace, "10_logs", "restart_tracker.json")
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

    def record_restart(self) -> bool:
        """记录一次重启。返回 False 表示超过限制(1小时>3次)。"""
        records = self._load()
        now = time.time()
        records.append({"timestamp": now, "time": datetime.now().isoformat()})
        # 清理超过 1 小时的记录
        records = [r for r in records if now - r["timestamp"] < 3600]
        self._save(records)
        return len(records) <= self.MAX_RESTARTS_PER_HOUR

    def check_limit(self) -> bool:
        """1小时内不超过3次返回 True"""
        records = self._load()
        now = time.time()
        recent = [r for r in records if now - r["timestamp"] < 3600]
        return len(recent) <= self.MAX_RESTARTS_PER_HOUR

    def should_stop(self) -> bool:
        """True 表示应该停止自动重启"""
        return not self.check_limit()

    def get_restart_count(self, hours=1) -> int:
        records = self._load()
        now = time.time()
        window = hours * 3600
        return len([r for r in records if now - r["timestamp"] < window])

    def _load(self) -> list:
        if not os.path.exists(self.log_path):
            return []
        try:
            with open(self.log_path) as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self, records: list):
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, "w") as f:
            json.dump(records, f, indent=2)

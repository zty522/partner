"""Token usage tracking system.

Records all LLM API token usage to CSV, supports querying by time/project/instance.
"""
import os
import csv
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any


class TokenTracker:
    """Token 用量追踪器"""

    _instance = None

    def __init__(self, workspace: str = "", instance_id: str = "default"):
        self._workspace = workspace
        self._instance_id = instance_id
        self._daily_budget = 0  # 0 = no limit

    def get_metrics_dir(self) -> str:
        if self._workspace:
            return os.path.join(self._workspace, "20_records", "metrics")
        return "./token_metrics"

    def record(self, prompt_tokens: int, completion_tokens: int, model: str = "",
               project: str = "", instance: str = ""):
        """记录一次 LLM 调用用量"""
        metrics_dir = self.get_metrics_dir()
        os.makedirs(metrics_dir, exist_ok=True)
        csv_path = os.path.join(metrics_dir, "token_usage.csv")

        entry = {
            "timestamp": datetime.now().isoformat(),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "model": model,
            "project": project or "",
            "instance": instance or self._instance_id,
        }

        file_exists = os.path.exists(csv_path)
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(entry.keys()))
            if not file_exists:
                writer.writeheader()
            writer.writerow(entry)

        return entry

    def query(self, period: str = "day", project: str = "",
              instance: str = "") -> Dict[str, Any]:
        """查询用量统计"""
        csv_path = os.path.join(self.get_metrics_dir(), "token_usage.csv")
        if not os.path.exists(csv_path):
            return {"total_tokens": 0, "total_calls": 0, "by_model": {}}

        now = datetime.now()
        if period == "day":
            cutoff = now - timedelta(days=1)
        elif period == "week":
            cutoff = now - timedelta(days=7)
        elif period == "month":
            cutoff = now - timedelta(days=30)
        else:
            cutoff = datetime.min

        total_prompt = 0
        total_completion = 0
        total_calls = 0
        by_model = {}
        by_project = {}

        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    ts = datetime.fromisoformat(row["timestamp"])
                except:
                    continue
                if ts < cutoff:
                    continue

                # 过滤项目和实例
                if project and row.get("project", "") != project:
                    continue
                if instance and row.get("instance", "") != instance:
                    continue

                p = int(row.get("prompt_tokens", 0))
                c = int(row.get("completion_tokens", 0))
                total_prompt += p
                total_completion += c
                total_calls += 1

                model = row.get("model", "unknown")
                by_model[model] = by_model.get(model, 0) + p + c

                proj = row.get("project", "unknown")
                by_project[proj] = by_project.get(proj, 0) + p + c

        return {
            "period": period,
            "total_calls": total_calls,
            "total_prompt": total_prompt,
            "total_completion": total_completion,
            "total_tokens": total_prompt + total_completion,
            "by_model": by_model,
            "by_project": by_project,
            "daily_budget": self._daily_budget,
            "budget_exceeded": self._daily_budget > 0 and (total_prompt + total_completion) > self._daily_budget,
        }

    def format_report(self, stats: Dict[str, Any]) -> str:
        """将统计格式化为自然语言"""
        lines = []
        lines.append(f"📊 Token 用量统计 ({stats['period']})")
        lines.append(f"调用次数: {stats['total_calls']}")
        lines.append(f"输入 Tokens: {stats['total_prompt']:,}")
        lines.append(f"输出 Tokens: {stats['total_completion']:,}")
        lines.append(f"合计: {stats['total_tokens']:,}")

        if stats.get("by_model"):
            lines.append("")
            lines.append("按模型:")
            for model, tokens in sorted(stats["by_model"].items(), key=lambda x: -x[1]):
                lines.append(f"  {model}: {tokens:,}")

        if stats.get("by_project"):
            lines.append("")
            lines.append("按项目:")
            for proj, tokens in sorted(stats["by_project"].items(), key=lambda x: -x[1]):
                lines.append(f"  {proj}: {tokens:,}")

        if stats.get("budget_exceeded"):
            lines.append("")
            lines.append(f"⚠️ 超出每日预算 ({stats['daily_budget']:,})！")

        return "\n".join(lines)

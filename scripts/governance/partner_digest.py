#!/usr/bin/env python3
"""Partner 多实例状态聚合器 — 把 4 个实例的最新动态汇总为一条消息。

用法: python3 partner_digest.py
输出: 纯文本汇总（供 QQ 推送）
"""
import json
import os
import subprocess
import sys
from datetime import datetime

INSTANCES = ["01", "02", "03", "05"]
BASE = "/mnt/e/work/partner_workspace/instances"
DIGEST_STATE = os.path.join(BASE, "..", "digest_state.json")


def instance_alive(i: str) -> bool:
    try:
        out = subprocess.run(
            ["pgrep", "-f", f"instance-id {i} "],
            capture_output=True, text=True, timeout=5,
        )
        return bool(out.stdout.strip())
    except Exception:
        return False


def last_delivery(i: str, since: float) -> list:
    """返回 delivery_queue.jsonl 中 ts > since 的条目。"""
    path = os.path.join(BASE, i, "state", "delivery_queue.jsonl")
    items = []
    if not os.path.exists(path):
        return items
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    ts = d.get("ts", "")
                    try:
                        t = datetime.fromisoformat(ts).timestamp()
                    except Exception:
                        continue
                    if t > since:
                        items.append(d)
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return items


def active_plan(i: str) -> str:
    path = os.path.join(BASE, i, "state", "active_plan.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return f"{d.get('status','?')} | {str(d.get('title',''))[:40]}"
    except Exception:
        return "无"


def main() -> None:
    now = datetime.now()
    # 读取上次聚合时间
    since = 0.0
    try:
        with open(DIGEST_STATE, "r", encoding="utf-8") as f:
            since = float(json.load(f).get("ts", 0))
    except Exception:
        pass

    lines = []
    lines.append(f"📊 Partner 实例状态汇总（{now.strftime('%H:%M')}）")
    total_new = 0
    for i in INSTANCES:
        alive = instance_alive(i)
        plan = active_plan(i)
        new_items = last_delivery(i, since)
        # 只保留 reply/notification/progress/error
        meaningful = [x for x in new_items if x.get("kind") in ("reply", "notification", "progress", "error", "file")]
        total_new += len(meaningful)
        status = "🟢" if alive else "🔴"
        lines.append(f"{status} 实例{i}: {plan}")
        for m in meaningful[-2:]:
            content = str(m.get("content", ""))[:90]
            kind = m.get("kind", "")
            lines.append(f"   · [{kind}] {content}")
    lines.append(f"（新增动态 {total_new} 条）" if total_new else "（本时段无新动态）")

    # 更新状态
    try:
        os.makedirs(os.path.dirname(DIGEST_STATE), exist_ok=True)
        with open(DIGEST_STATE, "w", encoding="utf-8") as f:
            json.dump({"ts": now.timestamp()}, f)
    except Exception:
        pass

    print("\n".join(lines))


if __name__ == "__main__":
    main()

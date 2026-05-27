#!/usr/bin/env python3
"""Research cycle runner — one cycle of Partner's autonomous research.

Runs before the Hermes cron prompt each cycle.
Handles:
  - State loading & heartbeat
  - Task selection (top 3 for parallel execution)
  - Reflection & hallucination check
  - Detailed execution logging
  - Cycle notification for QQ
"""

import json
import os
import sys
from datetime import datetime

WORKSPACE = os.environ.get("PARTNER_WORKSPACE", "")
if not WORKSPACE:
    pointer = os.path.expanduser("~/.partner")
    if os.path.exists(pointer):
        WORKSPACE = open(pointer).read().strip()
if not WORKSPACE:
    print("ERROR: No workspace found")
    sys.exit(1)

STATE_DIR = os.path.join(WORKSPACE, "state")
LOG_DIR = os.path.join(WORKSPACE, "logs")
EXEC_LOG = os.path.join(LOG_DIR, f"cycle_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")


def log(msg: str):
    """Write to execution log and print."""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(EXEC_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def load_json(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.loads(f.read(), strict=False)
    return {}


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main():
    log("=" * 50)
    log("研究周期开始")

    # ── 1. Load state ──
    hb = load_json(os.path.join(STATE_DIR, "heartbeat.json"))
    stats = load_json(os.path.join(STATE_DIR, "stats.json"))
    kb = load_json(os.path.join(STATE_DIR, "knowledge.json"))

    # ── 2. Heartbeat ──
    hb["status"] = "working"
    hb["last_heartbeat"] = datetime.now().isoformat()
    save_json(os.path.join(STATE_DIR, "heartbeat.json"), hb)
    log("心跳已更新 (working)")

    # ── 3. Load task queue ──
    tasks = load_json(os.path.join(STATE_DIR, "task_queue.json"))
    if isinstance(tasks, dict):
        tasks = tasks.get("tasks", [])

    pending = [t for t in tasks if isinstance(t, dict) and t.get("status") == "pending"]

    if not pending:
        log("队列为空——自动生成新任务")
        # Signal the Hermes prompt to generate ideas
        print("TASK_QUEUE_EMPTY: Generate new research ideas")
        save_json(os.path.join(STATE_DIR, "_cycle_context.json"), {
            "queue_empty": True,
            "needs_ideas": True,
        })

        # Write notification
        notif = {
            "timestamp": datetime.now().isoformat(),
            "type": "cycle_complete",
            "summary": "队列为空，正在生成新想法",
            "next_task": "idea_generation",
            "pending_count": 0,
            "details": [],
        }
        save_json(os.path.join(STATE_DIR, "notifications", "cycle_report.json"), notif)
        return

    # ── 4. Check for research directions config ──
    # If user specified 3 directions, pick top 1 from each direction
    config_path = os.path.join(WORKSPACE, "partner_config.json")
    partner_cfg = load_json(config_path)
    directions = partner_cfg.get("scheduler", {}).get("research_directions", [])

    batch = []
    if directions and len(directions) >= 1:
        log(f"检测到研究方向配置: {directions}")
        # For each direction, find top pending task matching it
        for direction in directions:
            dir_lower = direction.lower()
            # Match by task type, title, description, or a tags/direction field
            dir_tasks = []
            for t in pending:
                if t.get("status") != "pending":
                    continue
                t_title = (t.get("title", "") or "").lower()
                t_desc = (t.get("description", "") or "").lower()
                t_type = (t.get("type", "") or "").lower()
                t_dir = (t.get("direction", "") or "").lower()
                # Multi-keyword matching
                kw = dir_lower.replace("_", " ").replace("-", " ")
                if t_dir and kw in t_dir:
                    dir_tasks.append(t)
                elif kw in t_title:
                    dir_tasks.append(t)
                elif kw in t_type:
                    dir_tasks.append(t)
                elif kw in t_desc:
                    dir_tasks.append(t)
            if dir_tasks:
                dir_tasks.sort(key=lambda t: -t.get("priority", 0))
                best = dir_tasks[0]
                batch.append(best)
                log(f"  [{direction}] 选中: {best.get('title','?')[:60]}")
            else:
                log(f"  [{direction}] 方向无待处理任务")
    else:
        log("未配置研究方向——从全局队列选 top 3")

    # Fallback: if directions yielded < 3 tasks, fill remaining from global queue
    if len(batch) < 3:
        used_ids = {t.get("id") for t in batch}
        remaining = [t for t in pending if t.get("id") not in used_ids and t.get("status") == "pending"]
        remaining.sort(key=lambda t: -t.get("priority", 0))
        fill_count = 3 - len(batch)
        for t in remaining[:fill_count]:
            batch.append(t)
            log(f"  [补充] {t.get('title','?')[:60]}")

    log(f"选择了 {len(batch)} 个任务并行执行:")

    for i, t in enumerate(batch):
        title = t.get("title", "?")
        task_type = t.get("type", "general")
        t["status"] = "in_progress"
        log(f"  [{i+1}] [{task_type}] {title[:80]}")

    # Save updated queue (mark selected tasks as in_progress)
    save_json(os.path.join(STATE_DIR, "task_queue.json"), tasks)

    # ── 5. Save batch context for Hermes prompt ──
    # The script's stdout becomes available to the Hermes prompt
    # Hermes reads _cycle_context.json to know which tasks to execute
    ctx = {
        "batch": [
            {"id": t.get("id"), "type": t.get("type"), "title": t.get("title"),
             "description": t.get("description", ""), "priority": t.get("priority", 5)}
            for t in batch
        ],
        "remaining": len(pending) - len(batch),
        "exec_log": EXEC_LOG,
        "needs_ideas": False,
    }
    save_json(os.path.join(STATE_DIR, "_cycle_context.json"), ctx)
    log(f"批处理上下文已保存 ({len(batch)} 个任务)")

    # ── 6. Print summary for Hermes prompt (this becomes the prompt input) ──
    print(f"本周期有 {len(batch)} 个任务待并行执行。")
    print(f"使用 delegate_task 并行运行以下任务:")
    for i, t in enumerate(batch):
        print(f"  [{i+1}] {t.get('type','?')}: {t.get('title','?')}")
    print(f"每位子任务完成后, 汇总结果, 写入 state/ 文件。")
    print(f"最后进行反思总结: 结果是否合理? 有没有幻觉? 下一步方向?")

    # ── 7. Stats update ──
    stats["total_cycles"] = stats.get("total_cycles", 0) + 1
    stats["last_run"] = datetime.now().isoformat()
    stats["last_updated"] = datetime.now().isoformat()
    save_json(os.path.join(STATE_DIR, "stats.json"), stats)
    log("统计已更新")


if __name__ == "__main__":
    main()

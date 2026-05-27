#!/usr/bin/env python3
"""Send QQ notification for Partner cycle report.

Usage:
    python3 scripts/send_qq_report.py /path/to/partner_workspace

This script:
1. Reads cycle results from state files
2. Checks if user sent a message within 60 minutes
3. If yes → sends QQ report via REST API (passive reply)
4. If no → writes notification file for next user interaction
5. Always writes to state/notifications/ for bridge fallback
"""

import json
import os
import sys
import time
from datetime import datetime, timezone


def load_qq_credentials(workspace: str) -> tuple:
    """Load QQ bot credentials from workspace config files.

    Tries qq_config.json first, then partner_config.json messaging.qq section.
    Returns (app_id, app_secret, is_sandbox).
    """
    # Try qq_config.json
    qq_cfg_path = os.path.join(workspace, "qq_config.json")
    if os.path.exists(qq_cfg_path):
        with open(qq_cfg_path) as f:
            cfg = json.load(f)
        app_id = cfg.get("app_id", "")
        app_secret = cfg.get("app_secret", "")
        is_sandbox = cfg.get("is_sandbox", True)
        if app_id and app_secret:
            return app_id, app_secret, is_sandbox

    # Try partner_config.json
    pcfg_path = os.path.join(workspace, "partner_config.json")
    if os.path.exists(pcfg_path):
        with open(pcfg_path) as f:
            cfg = json.load(f)
        qq = cfg.get("messaging", {}).get("qq", {})
        app_id = qq.get("app_id", "")
        app_secret = qq.get("app_secret", "")
        is_sandbox = qq.get("is_sandbox", True)
        if app_id and app_secret:
            return app_id, app_secret, is_sandbox

    raise RuntimeError(
        f"QQ credentials not found. Configure via 'partner setup' or create {qq_cfg_path} "
        f"with app_id and app_secret fields."
    )


# Globals set in main()
APP_ID = ""
APP_SECRET = ""
IS_SANDBOX = True

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
API_BASE = "https://sandbox.api.sgroup.qq.com" if IS_SANDBOX else "https://api.sgroup.qq.com"


def log(msg: str):
    print(f"[send_qq_report] {msg}")


def get_access_token() -> str:
    """Get QQ Bot access token."""
    import urllib.request
    req = urllib.request.Request(
        TOKEN_URL,
        data=json.dumps({"appId": APP_ID, "clientSecret": APP_SECRET}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    if "access_token" not in data:
        raise RuntimeError(f"Token refresh failed: {data}")
    token = data["access_token"]
    expires_in = int(data.get("expires_in", 7200))
    log(f"Access token obtained, expires in {expires_in}s")
    return token


def send_qq_message(openid: str, content: str, access_token: str) -> bool:
    """Send a private QQ message via REST API."""
    import urllib.request
    url = f"{API_BASE}/v2/users/{openid}/messages"
    payload = json.dumps({
        "content": content,
        "msg_type": 0,
    }).encode()
    headers = {
        "Authorization": f"QQBot {access_token}",
        "Content-Type": "application/json",
        "X-Union-Appid": APP_ID,
    }
    try:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            log(f"QQ message sent: {result}")
            return True
    except Exception as e:
        log(f"Send failed: {e}")
        if hasattr(e, 'read'):
            try:
                body = e.read().decode()
                log(f"  Response body: {body}")
            except Exception:
                pass
        return False


def build_report(workspace: str) -> str:
    """Build a concise heartbeat report from state files.
    
    Reads active_plan.json for current plan status (new heartbeat model),
    falls back to task_queue.json for legacy compatibility.
    """
    state_dir = os.path.join(workspace, "state")

    # ── Active Plan (new heartbeat model) ──
    active_plan_path = os.path.join(state_dir, "active_plan.json")
    if os.path.exists(active_plan_path):
        try:
            with open(active_plan_path) as f:
                plan = json.load(f)
            plan_status = plan.get("status", "idle")
            title = plan.get("title", "")
            summary = plan.get("heartbeat_summary", "")
            phases = plan.get("phases", [])
            cur_idx = plan.get("current_phase_index", 0)

            done = sum(1 for p in phases if p.get("status") == "completed")
            total = len(phases)

            lines = [f"🤖 Partner 心跳"]
            if plan_status == "idle":
                lines.append("🟢 空闲中")
                lines.append(summary or "等待新任务")
            elif plan_status == "completed":
                lines.append(f"✅ 计划完成: {title}")
                lines.append(summary or f"共 {total} 个阶段全部完成")
            else:
                lines.append(f"🔵 {title or '执行中'}")
                if summary:
                    lines.append(summary)
                cur_phase = phases[cur_idx] if cur_idx < len(phases) else None
                if cur_phase:
                    step = cur_phase.get("current_step", "")
                    pname = cur_phase.get("name", f"阶段{cur_idx+1}")
                    line = f"📋 [{done}/{total}] {pname}"
                    if step:
                        line += f" → {step}"
                    lines.append(line)

            return "\n".join(lines)
        except Exception as e:
            log(f"Failed to read active_plan: {e}")

    # ── Legacy: stats + task_queue ──
    stats = {}
    stats_path = os.path.join(state_dir, "stats.json")
    if os.path.exists(stats_path):
        with open(stats_path) as f:
            stats = json.load(f)

    # Heartbeat
    hb = {}
    hb_path = os.path.join(state_dir, "heartbeat.json")
    if os.path.exists(hb_path):
        with open(hb_path) as f:
            hb = json.load(f)

    # Task queue summary
    queue_path = os.path.join(state_dir, "task_queue.json")
    pending_count = 0
    in_progress_count = 0
    in_progress_titles = []
    if os.path.exists(queue_path):
        with open(queue_path) as f:
            try:
                tasks = json.load(f)
                for t in tasks:
                    if isinstance(t, dict):
                        status = t.get("status", "")
                        if status == "pending":
                            pending_count += 1
                        elif status == "in_progress":
                            in_progress_count += 1
                            title = t.get("title", "?")
                            if len(in_progress_titles) < 3:
                                in_progress_titles.append(title)
            except Exception:
                pass

    # Cycle context (latest batch)
    ctx_path = os.path.join(state_dir, "_cycle_context.json")
    batch_titles = []
    if os.path.exists(ctx_path):
        try:
            with open(ctx_path) as f:
                ctx = json.load(f)
            for t in ctx.get("batch", []):
                title = t.get("title", "?")
                if len(batch_titles) < 3:
                    batch_titles.append(title)
        except Exception:
            pass

    total_cycles = stats.get("total_cycles", 0)
    completed = stats.get("total_tasks_completed", 0)
    knowledge = stats.get("total_knowledge_entries", 0)

    lines = [
        f"📍 Partner 研究汇报",
        f"📊 第 {total_cycles} 轮完成 | 累计完成任务 {completed} 项",
        f"📚 知识库 {knowledge} 条 | 队列 {pending_count} 待办, {in_progress_count} 进行中",
    ]
    if in_progress_titles:
        lines.append(f"▶️ 正在做: {' | '.join(in_progress_titles)}")
    if batch_titles:
        lines.append(f"📋 本轮: {' | '.join(batch_titles)}")

    return "\n".join(lines)


def save_notification(workspace: str, summary: str):
    """Save notification for next user interaction."""
    notif_dir = os.path.join(workspace, "state", "notifications")
    os.makedirs(notif_dir, exist_ok=True)

    notif = {
        "timestamp": datetime.now().isoformat(),
        "type": "cycle_complete",
        "summary": summary,
        "pending_count": 0,
        "details": [],
    }

    path = os.path.join(notif_dir, f"cycle_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(notif, f, ensure_ascii=False, indent=2)
    log(f"Notification saved: {path}")


def main():
    global APP_ID, APP_SECRET, IS_SANDBOX, API_BASE

    if len(sys.argv) < 2:
        print("Usage: send_qq_report.py <workspace_path>")
        sys.exit(1)

    workspace = sys.argv[1]
    state_dir = os.path.join(workspace, "state")

    # 0. Load QQ credentials from config
    try:
        APP_ID, APP_SECRET, IS_SANDBOX = load_qq_credentials(workspace)
        API_BASE = "https://sandbox.api.sgroup.qq.com" if IS_SANDBOX else "https://api.sgroup.qq.com"
    except RuntimeError as e:
        log(f"⚠️ {e}")
        sys.exit(1)

    # 1. Build report
    report = build_report(workspace)
    log(f"Report:\n{report}")

    # 2. Always save notification file
    save_notification(workspace, report)

    # 3. Check if user sent message within 60 minutes
    # Check heartbeat suppression flag (set when a task was just queued)
    flag_path = os.path.join(state_dir, "suppress_heartbeat.flag")
    if os.path.exists(flag_path):
        try:
            with open(flag_path) as f:
                flag_time = float(f.read().strip())
            if time.time() - flag_time < 180:  # 3 minutes
                log("Heartbeat suppressed — task was just queued, skipping push")
                os.remove(flag_path)
                return
            else:
                os.remove(flag_path)  # stale flag
        except Exception:
            pass

    ctx_path = os.path.join(state_dir, "qq_user_context.json")
    if not os.path.exists(ctx_path):
        log("No QQ user context found — skipping passive send, notification saved")
        return

    with open(ctx_path) as f:
        ctx = json.load(f)

    openid = ctx.get("openid", "")
    last_msg_at_str = ctx.get("last_message_at", "")
    if not openid or not last_msg_at_str:
        log("Incomplete QQ user context — skipping passive send")
        return

    try:
        last_msg_at = datetime.fromisoformat(last_msg_at_str)
        now = datetime.now(timezone.utc) if last_msg_at.tzinfo else datetime.now()
        elapsed = (now - last_msg_at).total_seconds()
    except Exception as e:
        log(f"Cannot parse last_message_at '{last_msg_at_str}': {e}")
        return

    if elapsed > 3600:
        log(f"User last messaged {elapsed:.0f}s ago (>60min) — passive send skipped, notification saved")
        return

    # 4. Send via QQ REST API
    log(f"User last messaged {elapsed:.0f}s ago — sending passive report to {openid}")
    try:
        token = get_access_token()
        success = send_qq_message(openid, report, token)
        if success:
            log("✅ Report sent to QQ!")
        else:
            log("⚠️ Report saved as notification (send failed)")
    except Exception as e:
        log(f"⚠️ Send error: {e} — notification saved for next interaction")


if __name__ == "__main__":
    main()

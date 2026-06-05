#!/usr/bin/env python3
"""Send QQ notification for Partner cycle report.

从 state 文件收集结构化数据，
在脚本内渲染成自然语言短汇报，
再通过 QQ API 发出。

Usage:
    python3 scripts/send_qq_report.py /path/to/partner_workspace

Output (stdout):
    JSON 对象包含 collected data + 推送成功/失败状态

Side effects:
    1. 保存 notification JSON 到 state/notifications/
    2. 如果用户在 60 分钟内发过消息，通过 QQ API 推送
"""

import json
import os
import sys
import time
from datetime import datetime, timezone


def load_qq_credentials(workspace: str) -> tuple:
    """Load QQ bot credentials from workspace config files."""
    qq_cfg_path = os.path.join(workspace, "qq_config.json")
    if os.path.exists(qq_cfg_path):
        with open(qq_cfg_path) as f:
            cfg = json.load(f)
        app_id = cfg.get("app_id", "")
        app_secret = cfg.get("app_secret", "")
        is_sandbox = cfg.get("is_sandbox", True)
        if app_id and app_secret:
            return app_id, app_secret, is_sandbox

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


APP_ID = ""
APP_SECRET = ""
IS_SANDBOX = True
API_BASE = "https://sandbox.api.sgroup.qq.com"

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"


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


def safe_read_json(path: str, default=None):
    """Read a JSON file, return default on failure."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def collect_state_data(workspace: str) -> dict:
    """从 state 文件收集结构化数据，不生成任何自然语言文本。

    Returns:
        dict 包含以下字段（所有字段都可能为空/不存在）：
        - workspace: 工作区路径
        - timestamp: 收集时间
        - plan: active_plan.json 原始数据
        - queue: task_queue.json 统计
        - stats: stats.json 数据
        - events: event_bus.jsonl 未推送事件摘要
        - qq_context: QQ 用户上下文（如果有）
    """
    state_dir = os.path.join(workspace, "state")
    data = {
        "workspace": workspace,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "plan": None,
        "queue": {"total": 0, "pending": 0, "in_progress": 0, "completed": 0, "failed": 0},
        "stats": {},
        "events": [],
        "qq_context": None,
    }

    # Active plan
    plan = safe_read_json(os.path.join(state_dir, "active_plan.json"))
    if plan:
        # 保留原始数据，只精简 phase 的详细内容
        phases = plan.get("phases", [])
        phase_summary = []
        for i, p in enumerate(phases):
            phase_summary.append({
                "index": i,
                "name": p.get("name", f"phase_{i}"),
                "type": p.get("type", ""),
                "status": p.get("status", "pending"),
                "current_step": p.get("current_step", ""),
                "started_at": p.get("started_at"),
                "completed_at": p.get("completed_at"),
            })
        data["plan"] = {
            "status": plan.get("status", "idle"),
            "title": plan.get("title", ""),
            "goal": plan.get("goal", ""),
            "current_phase_index": plan.get("current_phase_index", 0),
            "phases": phase_summary,
            "created_at": plan.get("created_at"),
            "heartbeat_summary": plan.get("heartbeat_summary", ""),
            "all_done": all(p.get("status") == "completed" for p in phases),
        }

    # Task queue
    queue = safe_read_json(os.path.join(state_dir, "task_queue.json"), default=[])
    if isinstance(queue, list):
        status_counts = {"pending": 0, "in_progress": 0, "completed": 0, "failed": 0}
        pending_titles = []
        in_progress_titles = []
        for t in queue:
            if isinstance(t, dict):
                s = t.get("status", "")
                if s in status_counts:
                    status_counts[s] += 1
                if s == "pending" and len(pending_titles) < 5:
                    pending_titles.append({
                        "title": t.get("title", "?"),
                        "priority": t.get("priority", 5),
                        "created_at": t.get("created_at", ""),
                    })
                elif s == "in_progress" and len(in_progress_titles) < 3:
                    in_progress_titles.append(t.get("title", "?"))
        data["queue"] = {
            **status_counts,
            "total": len(queue),
            "pending_titles": pending_titles,
            "in_progress_titles": in_progress_titles,
        }

    # Stats
    data["stats"] = safe_read_json(os.path.join(state_dir, "stats.json"), default={})

    # Knowledge base summary
    kb = safe_read_json(os.path.join(state_dir, "knowledge.json"))
    if kb:
        entries = kb.get("entries", kb if isinstance(kb, list) else [])
        today = datetime.now().strftime("%Y-%m-%d")
        data["knowledge"] = {
            "total_entries": len(entries),
            "recent_additions": sum(
                1 for e in entries
                if today in (e.get("created_at", "") or "")
            ),
        }
    else:
        data["knowledge"] = {"total_entries": 0, "recent_additions": 0}

    # Journal summary
    journal_path = os.path.join(state_dir, "journal.jsonl")
    if os.path.exists(journal_path):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            today_activities = 0
            with open(journal_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if today in entry.get("timestamp", ""):
                            today_activities += 1
                    except (json.JSONDecodeError, TypeError):
                        continue
            data["journal"] = {"today_activities": today_activities}
        except OSError:
            pass

    # Event bus — 未推送事件摘要
    event_bus_path = os.path.join(state_dir, "event_bus.jsonl")
    if os.path.exists(event_bus_path):
        unpushed = []
        try:
            with open(event_bus_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                        if not ev.get("pushed", False):
                            unpushed.append({
                                "type": ev.get("type", ""),
                                "subtype": ev.get("subtype", ""),
                                "title": ev.get("title", ""),
                                "priority": ev.get("priority", 5),
                            })
                    except (json.JSONDecodeError, TypeError):
                        continue
        except OSError:
            pass
        if unpushed:
            data["events"] = unpushed[:10]

    # QQ user context
    ctx_path = os.path.join(state_dir, "qq_user_context.json")
    if os.path.exists(ctx_path):
        ctx = safe_read_json(ctx_path)
        if ctx:
            data["qq_context"] = {
                "openid": ctx.get("openid", ""),
                "last_message_at": ctx.get("last_message_at", ""),
            }

    return data


def render_report_text(state_data: dict) -> str:
    """Render structured heartbeat data into a short user-facing update."""
    plan = state_data.get("plan") or {}
    queue = state_data.get("queue") or {}
    title = (plan.get("title") or "").strip()
    summary = (plan.get("heartbeat_summary") or "").strip()
    phases = plan.get("phases") or []

    completed = [p for p in phases if p.get("status") == "completed"]
    current = None
    for phase in phases:
        if phase.get("status") in ("in_progress", "pending"):
            current = phase
            break

    if not summary and not completed:
        return ""

    low_value_patterns = (
        "我先继续在后台处理",
        "继续盯着",
        "继续往下推",
        "有结果了再跟你说",
        "文件已更新",
        "目录",
    )
    if summary and any(p in summary for p in low_value_patterns):
        return ""

    lines = []
    if title:
        lines.append(f"「{title}」有阶段性更新。")
    else:
        lines.append("当前项目有阶段性更新。")

    if summary:
        lines.append(summary.rstrip("。") + "。")

    if current and current.get("current_step"):
        lines.append(f"现在在接着做：{current['current_step'].strip('。')}。")
    elif completed:
        lines.append(f"这轮已经完成了 {len(completed)} 个阶段。")

    pending = int(queue.get("pending", 0) or 0)
    if pending > 0 and current and current.get("current_step"):
        lines.append(f"后台还有 {pending} 个待处理动作。")

    text = "\n".join(lines).strip()
    if len(text) > 500:
        text = text[:497].rstrip() + "..."
    return text


def main():
    global APP_ID, APP_SECRET, IS_SANDBOX, API_BASE

    if len(sys.argv) < 2:
        print("Usage: send_qq_report.py <workspace_path>")
        sys.exit(1)

    workspace = sys.argv[1]
    state_dir = os.path.join(workspace, "state")

    # 0. Load QQ credentials
    try:
        APP_ID, APP_SECRET, IS_SANDBOX = load_qq_credentials(workspace)
        API_BASE = "https://sandbox.api.sgroup.qq.com" if IS_SANDBOX else "https://api.sgroup.qq.com"
    except RuntimeError as e:
        log(f"⚠️ {e}")
        sys.exit(1)

    # 1. Collect structured state data (no hardcoded text)
    state_data = collect_state_data(workspace)
    log(f"Collected state data: plan={state_data.get('plan', {}).get('status', 'N/A')}, "
        f"queue_pending={state_data['queue']['pending']}")

    # 2. Check QQ user context for push eligibility
    flag_path = os.path.join(state_dir, "suppress_heartbeat.flag")
    if os.path.exists(flag_path):
        try:
            with open(flag_path) as f:
                flag_time = float(f.read().strip())
            if time.time() - flag_time < 180:  # 3 minutes
                log("Heartbeat suppressed — task was just queued, skipping push")
                os.remove(flag_path)
                state_data["pushed"] = False
                state_data["push_reason"] = "suppressed"
                print(json.dumps(state_data, ensure_ascii=False))
                return
            else:
                os.remove(flag_path)
        except Exception:
            pass

    ctx = state_data.get("qq_context")
    if not ctx or not ctx.get("openid") or not ctx.get("last_message_at"):
        log("No QQ user context found — skipping push, notification saved")
        state_data["pushed"] = False
        state_data["push_reason"] = "no_context"
        print(json.dumps(state_data, ensure_ascii=False))
        return

    try:
        last_msg_at = datetime.fromisoformat(ctx["last_message_at"])
        now = datetime.now(timezone.utc) if last_msg_at.tzinfo else datetime.now()
        elapsed = (now - last_msg_at).total_seconds()
    except Exception as e:
        log(f"Cannot parse last_message_at: {e}")
        state_data["pushed"] = False
        state_data["push_reason"] = "parse_error"
        print(json.dumps(state_data, ensure_ascii=False))
        return

    if elapsed > 3600:
        log(f"User last messaged {elapsed:.0f}s ago (>60min) — push skipped, notification saved")
        state_data["pushed"] = False
        state_data["push_reason"] = "user_inactive"
        print(json.dumps(state_data, ensure_ascii=False))
        return

    # 4. Send a short natural-language report via QQ API
    log(f"User last messaged {elapsed:.0f}s ago — sending structured report to {ctx['openid']}")
    try:
        token = get_access_token()
        message_text = render_report_text(state_data)
        if not message_text:
            log("No meaningful heartbeat report — skipping push")
            state_data["pushed"] = False
            state_data["push_reason"] = "low_value"
            print(json.dumps(state_data, ensure_ascii=False))
            return
        success = send_qq_message(ctx["openid"], message_text, token)
        if success:
            log("✅ Natural-language report sent to QQ!")
            state_data["pushed"] = True
            state_data["push_reason"] = "sent"
            state_data["rendered_message"] = message_text
        else:
            log("⚠️ Send failed — notification saved")
            state_data["pushed"] = False
            state_data["push_reason"] = "send_failed"
    except Exception as e:
        log(f"⚠️ Send error: {e} — notification saved")
        state_data["pushed"] = False
        state_data["push_reason"] = "error"

    # 5. Output structured JSON to stdout
    print(json.dumps(state_data, ensure_ascii=False))


if __name__ == "__main__":
    main()

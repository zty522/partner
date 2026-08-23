"""自动迭代修复事件（auto-repair / login-wall / batch-plan fallback）。

四个新增事件，对应 self-evolution 需要的修复机制：
- atomic_auto_repair_plan：plan 执行失败时，根据已成功步骤重生成 plan
- atomic_handle_login_wall：read_image 识别到登录墙时，自动处理（关闭弹窗/记录状态）
- atomic_batch_plan_fallback：batch_planner minimax 超时时自动切本地 micro-plan
- atomic_write_artifact_fallback：atomic_write_artifact 失败时 fallback 到 atomic_generate_pdf
"""
from __future__ import annotations

import json
import os
import re
import time
import logging
import glob as _glob
from typing import Any

logger = logging.getLogger(__name__)


def _deliver_text(text: str) -> dict:
    """Resolve the live runtime bridge lazily after Partner initialization."""
    from partner.mind.executor import push_text_now
    return push_text_now(text)


# ── A: 自动修复 plan ────────────────────────────────────────
def atomic_auto_repair_plan(ctx, params: dict) -> dict:
    """根据 task 目录已成功生成的产物（PNG/stdout），重生成不依赖失败步骤的简化 plan。

    触发场景：artifact_validation 缺 *.md 报告，但 task 目录已有可用的产物（如 RDKit 数据、Loss 图）。
    实现：扫 task working_dir 的 PNG + 已成功 step 的 stdout，把它们列给 LLM 让它重写更简短的 plan。
    """
    wd = ""
    ti = getattr(ctx, "task_instance", None)
    if ti is not None:
        wd = getattr(ti, "working_dir", "") or ""
    if not wd or not os.path.isdir(wd):
        return {"ok": False, "error": "no working_dir"}
    # 1. 收集已成功的产物
    successful = []
    for f in sorted(os.listdir(wd)):
        fp = os.path.join(wd, f)
        if f.endswith(".png") and os.path.getsize(fp) > 10000:
            successful.append({"type": "screenshot", "path": fp, "size": os.path.getsize(fp)})
        elif f.endswith(".json") and f.startswith("_step_") and f.endswith(".result.json"):
            try:
                row = json.load(open(fp, encoding="utf-8"))
                if row.get("ok"):
                    et = row.get("event_type", "")
                    stdout = str((row.get("result") or {}).get("stdout", ""))[:500]
                    if stdout and et in ("execute_code", "run_command", "browser_extract"):
                        successful.append({"type": "stdout", "event": et, "content": stdout})
            except Exception:
                pass
    if not successful:
        return {"ok": False, "error": "no successful artifacts to base repair on"}
    # 2. 写修复计划到 inbox 让下一轮 LLM 看到这些产物
    ws = ""
    if ti is not None:
        ws = getattr(ti, "workspace", "") or ""
    if not ws:
        ws = getattr(ctx, "workspace", "") or ""
    if not ws:
        # 从 wd 推 ws
        m = re.search(r"^(/.+/instances/[^/]+)/state/", wd)
        if m:
            ws = m.group(1)
    if not ws:
        return {"ok": False, "error": "no workspace"}
    m = re.search(r"/instances/(\d{2})/", ws + " " + wd)
    if not m:
        return {"ok": False, "error": "no instance"}
    instance = m.group(1)
    inbox = os.path.join(ws, "instances", instance, "state", "desktop_inbox.jsonl")
    if not os.path.exists(os.path.dirname(inbox)):
        return {"ok": False, "error": "inbox parent not exist"}
    artifacts_desc = "\n".join([f"- {a.get('type','')}: {a.get('path', a.get('content',''))[:200]}" for a in successful[:8]])
    msg = {
        "id": f"auto_repair_{int(time.time())}_{instance}",
        "role": "user",
        "content": f"[auto-repair 触发] 上一轮 plan 失败但已有真实产物：\n{artifacts_desc}\n\n请基于这些产物**直接调用 atomic_generate_pdf source_path=<现有报告.md> + image_paths=<png 列表>** 生成 PDF，**不要再调 atomic_write_artifact**（会被占位符检测拦截）。",
        "source": "self_evolution_auto_repair",
    }
    try:
        with open(inbox, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    except Exception as e:
        return {"ok": False, "error": f"write inbox: {e}"}
    try:
        from partner.evolution.evolution_log import log_evolution
        log_evolution("auto_repair_triggered", detail={"instance": instance, "artifacts": len(successful)})
    except Exception:
        pass
    return {"ok": True, "artifacts_used": len(successful), "inbox": inbox}


# ── B: batch_plan fallback（timeout 后用本地模板） ──────────
def atomic_batch_plan_fallback(ctx, params: dict) -> dict:
    """batch_planner minimax 超时时，用本地 micro-plan 模板替代（不再调 LLM）。"""
    # 收集 task working_dir 的产物
    wd = ""
    ti = getattr(ctx, "task_instance", None)
    if ti is not None:
        wd = getattr(ti, "working_dir", "") or ""
    if not wd or not os.path.isdir(wd):
        return {"ok": False, "error": "no working_dir"}
    pngs = []
    for f in sorted(os.listdir(wd)):
        fp = os.path.join(wd, f)
        if f.endswith(".png") and os.path.getsize(fp) > 10000:
            pngs.append(fp)
    has_data = any(f.startswith("_step_") and f.endswith(".result.json") for f in os.listdir(wd))
    if not has_data:
        return {"ok": False, "error": "no data, fallback won't help"}
    # 构造最小 plan：直接生成 PDF
    image_paths = pngs if pngs else []
    # 写一份汇总 md
    summary_md = os.path.join(wd, "summary.md")
    try:
        with open(summary_md, "w", encoding="utf-8") as f:
            f.write("# Fallback Summary\n\n## Available Artifacts\n")
            for p in image_paths:
                f.write(f"- {os.path.basename(p)} ({os.path.getsize(p)}B)\n")
            for fpath in sorted(os.listdir(wd)):
                if fpath.startswith("_step_") and fpath.endswith(".result.json"):
                    try:
                        row = json.load(open(os.path.join(wd, fpath), encoding="utf-8"))
                        if row.get("ok"):
                            et = row.get("event_type", "")
                            stdout = str((row.get("result") or {}).get("stdout", ""))[:300]
                            if stdout:
                                f.write(f"\n## {et}\n\n```\n{stdout}\n```\n")
                    except Exception:
                        pass
    except Exception as e:
        return {"ok": False, "error": f"write summary: {e}"}
    # 调 atomic_generate_pdf
    from partner.v2.pdf_events import atomic_generate_pdf
    r = atomic_generate_pdf(ctx, {
        "source_path": summary_md,
        "output_path": os.path.join(wd, "fallback_report.pdf"),
        "title": "Fallback Report",
        "image_paths": image_paths,
    })
    try:
        from partner.evolution.evolution_log import log_evolution
        log_evolution("batch_plan_fallback", detail={"ok": r.get("ok"), "size": r.get("size", 0)})
    except Exception:
        pass
    return r


# ── C: 处理登录墙 ────────────────────────────────────────
def atomic_handle_login_wall(ctx, params: dict) -> dict:
    """read_image 识别到登录墙时调用：
    1. 写 LOGIN_WALL_NOTE.md 说明状态
    2. 写 inbox 消息请求人类介入（"请人工登录后回复继续"）
    3. 标记任务状态为 awaiting_human_input
    """
    import re as _re
    # 去重：60 秒内同 instance 同 task 已经触发过 login_wall 则跳过（避免 LLM 循环触发）
    try:
        dedup_key = ""
        ti0 = getattr(ctx, "task_instance", None)
        if ti0 is not None:
            wd0 = getattr(ti0, "working_dir", "") or ""
            import re as _re_dd
            mm0 = _re_dd.search(r"/instances/(\d{2})/state/tasks/([a-f0-9-]+)", wd0)
            if mm0:
                dedup_key = f"{mm0.group(1)}_{mm0.group(2)[:8]}"
        if dedup_key:
            dedup_path = f"/tmp/partner_login_dedup_{dedup_key}"
            import os.path as _osp
            if _osp.exists(dedup_path):
                import time as _t_dd
                age = _t_dd.time() - _osp.getmtime(dedup_path)
                if age < 60:
                    logger.info("[handle_login_wall] 去重：60 秒内已触发，跳过")
                    return {"ok": True, "dedup": True, "skipped": True}
            # 写去重标记
            try:
                open(dedup_path, "w").close()
            except Exception:
                pass
    except Exception as _exc_dd:
        pass
    try:
        from partner.evolution.evolution_log import log_evolution
        log_evolution("login_wall_detected", detail={"ctx": str(ctx)[:200]})
    except Exception:
        pass
    wd = ""
    ti = getattr(ctx, "task_instance", None)
    if ti is not None:
        wd = getattr(ti, "working_dir", "") or ""
    # 1. 写 LOGIN_WALL_NOTE.md
    note_path = os.path.join(wd, "LOGIN_WALL_NOTE.md") if wd else "/tmp/LOGIN_WALL_NOTE.md"
    try:
        with open(note_path, "w", encoding="utf-8") as f:
            f.write("# Login Wall Detected - 需要人工介入\n\n")
            f.write("此任务遇到登录墙（手机号/扫码/Cookie 验证等），自动化无法绕过。\n\n")
            f.write("## 等待人类指令\n\n")
            f.write("**Partner 已暂停，等待用户人工登录后发送指令继续。**\n\n")
            f.write("## 用户操作建议\n\n")
            f.write("1. 在浏览器手动登录（cookie 会保存在 worker 浏览器 profile）\n")
            f.write("2. 登录完成后，向此实例发送任意消息（如「已登录请继续」）\n")
            f.write("3. Partner 收到消息后会自动从断点继续\n\n")
            f.write("## 当前状态\n\n")
            f.write("- 登录墙截图已保存\n")
            f.write("- 浏览器 session 已保持（cookie 会复用）\n")
            f.write("- 任务标记：awaiting_human_input\n")
    except Exception:
        pass
    # 2. 写 inbox 请求人类
    # 优先从 working_dir 反推（最可靠：含 /instances/{id}/state/tasks/...）
    ws = ""
    instance = ""
    if wd:
        # working_dir 形态：{ws_root}/instances/{id}/state/tasks/xxx
        # m.group(1) = {ws_root}/instances/{id}（含 instances/XX）
        # 但我们只要 ws_root（去掉 /instances/XX）
        m = _re.search(r"^(/.+?/instances/(\d{2}))/", wd)
        if m:
            ws = m.group(1).rsplit("/instances/", 1)[0]  # 去掉末尾 /instances/{id}
            instance = m.group(2)
    if not ws and ti is not None:
        ws = getattr(ti, "workspace", "") or ""
    if not ws:
        ws = getattr(ctx, "workspace", "") or ""
    if not instance and ws:
        m = _re.search(r"/instances/(\d{2})(?:/|$)", ws)
        if m:
            instance = m.group(1)
    # 真实给用户发文本消息（通过 QQ Bot API，不是 inbox，避免 poller 自动消费）
    inbox_path = ""  # 故意留空，避免 poller 自动消费
    # 推实例编号（"01"/"02"）+ 任务标题（优先 task title，否则 instance 描述）
    inst_id = "01"
    task_title = ""
    if hasattr(ctx, "task_instance") and ctx.task_instance:
        ti = ctx.task_instance
        wd_str = getattr(ti, "working_dir", "") or ""
        import re as _re_inst
        mm = _re_inst.search(r"/instances/(\d{2})", wd_str)
        if mm:
            inst_id = mm.group(1)
        # 优先 task title / name / project，否则从 working_dir 推 task id
        task_title = (getattr(ti, "title", "") or
                      getattr(ti, "name", "") or
                      getattr(ti, "description", "") or
                      "").strip()
        if not task_title:
            mm2 = _re_inst.search(r"/tasks/([a-f0-9-]+)", wd_str)
            if mm2:
                task_title = f"任务 {mm2.group(1)[:8]}"
    # 任务名映射（让文案更友好）
    TASK_NAME_MAP = {
        "01": "小红书任务",
        "02": "分子生成任务",
        "03": "智能体自进化方法探索",
        "04": "丰富集成 agent 和 tool",
        "05": "Partner 自身代码改造",
    }
    # 如果 task_title 是 task_id 简写（"任务 2185daf7"）就忽略，用 TASK_NAME_MAP
    import re as _re_lbl
    if _re_lbl.match(r"^任务 [a-f0-9]{6,}$", task_title or ""):
        task_label = TASK_NAME_MAP.get(inst_id, f"实例 {inst_id} 任务")
    else:
        task_label = task_title or TASK_NAME_MAP.get(inst_id, f"实例 {inst_id} 任务")
    user_text_msg = (
        f"⚠️ 登录墙检测\n\n"
        f"我在尝试 **{task_label}**（实例 {inst_id}）时遇到登录墙（手机号/扫码/Cookie），自动化无法绕过。\n\n"
        f"**是否需要我把登录界面打开并等待你手动登录？**\n\n"
        f"回复 **是**（或 「打开」/「yes」）→ 我会调用 atomic_open_login_on_confirm 打开浏览器登录界面\n\n"
        f"回复 **否**（或 「跳过」/「no」）→ 我会调用 atomic_skip_login 跳过登录（部分功能受限）\n\n"
        f"登录后回复 **已登录** → 我会继续执行\n\n"
        f"📷 截图: {note_path}"
    )
    try:
        # 推 ws 根（去掉末尾 /instances/{id}）
        ws_for_send = ""
        if wd:
            import re as _re2
            mm = _re2.search(r"^(/.+?/instances/(\d{2}))/", wd)
            if mm:
                # mm.group(1) = {ws_root}/instances/{id}，去掉 instances/{id}
                ws_for_send = mm.group(1).rsplit("/instances/", 1)[0]
        if ws_for_send:
            from types import SimpleNamespace
            send_ctx = SimpleNamespace(workspace=ws_for_send)
            from partner.v2.repair_events import atomic_send_user_text
            send_r = atomic_send_user_text(send_ctx, {"text": user_text_msg})
            if send_r.get("ok"):
                inbox_path = "(sent via QQ to user)"
                try:
                    from partner.evolution.evolution_log import log_evolution
                    log_evolution("login_wall_text_sent", detail={"len": len(user_text_msg), "msg_id": send_r.get("msg_id", "")})
                except Exception:
                    pass
            else:
                # 发送失败：fallback 写 awaiting_user_decision.md（兜底）
                if wd:
                    try:
                        decision_path = os.path.join(wd, "awaiting_user_decision.md")
                        with open(decision_path, "w", encoding="utf-8") as f:
                            f.write("# Awaiting User Decision\n\n")
                            f.write(f"**时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                            f.write("**检测到登录墙**，自动化无法绕过。\n\n")
                            f.write("## 请选择\n\n")
                            f.write("- 回复 **是**：调用 atomic_open_login_on_confirm\n")
                            f.write("- 回复 **否**：调用 atomic_skip_login\n\n")
                            f.write(f"QQ 发送失败原因: {send_r.get('error','')}\n")
                    except Exception:
                        pass
    except Exception as _exc_send:
        try:
            from partner.evolution.evolution_log import log_evolution
            log_evolution("login_wall_text_send_error", detail={"error": str(_exc_send)[:200]})
        except Exception:
            pass
    # 3. 标记任务状态为 awaiting_human_input
    try:
        if ti is not None:
            ti.append_log("login_wall_status", {
                "detected": True,
                "human_intervention_required": True,
                "note": note_path,
                "human_inbox_written": bool(inbox_path),
                "status": "awaiting_human_input",
            })
            # 同时尝试设置 task status（task_instance dataclass 不一定有 status 字段）
            for attr in ("status", "completion_status"):
                try:
                    setattr(ti, attr, "awaiting_human_input")
                except Exception:
                    pass
    except Exception:
        pass
    try:
        from partner.evolution.evolution_log import log_evolution
        log_evolution("login_wall_waiting_human", detail={"note": note_path, "inbox": inbox_path})
    except Exception:
        pass
    return {"ok": True, "login_wall": True, "note": note_path, "human_inbox": inbox_path, "status": "awaiting_human_input"}




# ── E: 收到用户「是」后打开浏览器登录界面 ───────────────────────
def atomic_open_login_on_confirm(ctx, params: dict) -> dict:
    """用户回复「是」后调用：打开浏览器到登录页面（保持 worker 会话），等待人工登录。

    不自动填表单/不点击任何登录按钮——只把登录界面呈现给用户。
    登录完成后用户回「已登录」会触发 atomic_resume_after_login。
    """
    return atomic_open_browser_foreground_and_notify(ctx, params)


def atomic_open_browser_foreground_and_notify(ctx, params: dict) -> dict:
    """Open a persistent visible login page, bring it forward, and notify the user.

    The operation is successful only when the browser worker confirms the page
    open *and* the active user channel acknowledges the notification.  The
    browser worker is deliberately left running for manual login.
    """
    target_url = str(params.get("url") or "https://www.xiaohongshu.com/explore").strip()
    if not target_url:
        return {"ok": False, "status": "invalid", "error": "缺少 url 参数"}
    try:
        from partner.v2.browser import atomic_browser_open
        browser_result = atomic_browser_open(ctx, {
            "url": target_url,
            "visible": True,
            "foreground": True,
            "headless": False,
        })
    except Exception as exc:
        browser_result = {"ok": False, "status": "error", "error": str(exc)}
    browser_ok = bool(browser_result.get("ok") or browser_result.get("status") == "ok")
    if not browser_ok:
        return {
            "ok": False,
            "status": "browser_open_failed",
            "url": target_url,
            "browser": browser_result,
            "notified": False,
        }

    notification = str(params.get("message") or (
        "🌐 登录网页已经在电脑前台打开，并会保持运行。请在打开的浏览器窗口中完成手动登录；"
        "登录完成后回复“已登录”，我会继续执行。"
    )).strip()
    notify_result = atomic_send_user_text(ctx, {"text": notification})
    notified = bool(notify_result.get("ok") and notify_result.get("delivered"))
    status = "ready_for_manual_login" if notified else "browser_opened_notification_failed"
    result = {
        "ok": browser_ok and notified,
        "status": status,
        "login_opened": True,
        "visible": True,
        "foreground": True,
        "kept_open": True,
        "url": target_url,
        "browser": browser_result,
        "notified": notified,
        "notification": notify_result,
    }
    try:
        from partner.evolution.evolution_log import log_evolution
        log_evolution("login_foreground_ready" if result["ok"] else "login_notification_failed", detail={
            "url": target_url,
            "browser_opened": browser_ok,
            "notified": notified,
            "notification_status": notify_result.get("status", ""),
        })
    except Exception:
        pass
    return result


# ── F: 用户登录完成后继续（从断点恢复）───────────────────────
def atomic_resume_after_login(ctx, params: dict) -> dict:
    """用户回「已登录」后调用：标记登录状态恢复，继续原任务。"""
    try:
        from partner.evolution.evolution_log import log_evolution
        log_evolution("login_resumed", detail={"ctx": str(ctx)[:200]})
    except Exception:
        pass
    wd = ""
    ti = getattr(ctx, "task_instance", None)
    if ti is not None:
        wd = getattr(ti, "working_dir", "") or ""
    # 写登录完成记录
    if wd:
        try:
            with open(os.path.join(wd, "LOGIN_COMPLETED.md"), "w", encoding="utf-8") as f:
                f.write("# Login Completed\n\n")
                f.write(f"用户已完成登录（{time.strftime('%Y-%m-%d %H:%M:%S')}）。\n")
                f.write("Partner 已恢复执行，从断点继续。\n")
        except Exception:
            pass
    # 解除 awaiting_human_input 状态
    try:
        if ti is not None:
            ti.append_log("login_status", {"resumed": True, "completed_at": time.time()})
            for attr in ("status", "completion_status"):
                try:
                    setattr(ti, attr, "running")
                except Exception:
                    pass
    except Exception:
        pass
    return {"ok": True, "resumed": True}


def atomic_verify_login_and_continue(ctx, params: dict) -> dict:
    """Verify the persistent browser session, notify clearly, and enqueue work.

    A user's ``已登录`` message is only a claim.  This event verifies visible
    page evidence before recording login success.  Once verified it schedules
    a concrete next action instead of merely writing "下一步" to a report.
    """
    workspace = str(getattr(ctx, "workspace", "") or "")
    if not workspace and isinstance(ctx, dict):
        workspace = str(ctx.get("workspace") or "")
    if not workspace:
        return {"ok": False, "status": "invalid", "error": "无法确定实例 workspace"}

    url = str(params.get("url") or "https://www.xiaohongshu.com/explore")
    try:
        from partner.v2.browser import atomic_browser_open, atomic_browser_execute
        opened = atomic_browser_open(ctx, {
            "url": url,
            "visible": bool(params.get("visible", True)),
            "foreground": bool(params.get("foreground", True)),
            "headless": False,
        })
        if not (opened.get("ok") or opened.get("status") == "ok"):
            return {"ok": False, "status": "browser_open_failed", "browser": opened}
        inspected = atomic_browser_execute(ctx, {
            "script": "() => ({url: location.href, title: document.title, body: document.body ? document.body.innerText.slice(0, 3000) : '', cookieLength: document.cookie.length})",
        })
    except Exception as exc:
        return {"ok": False, "status": "verification_failed", "error": str(exc)}

    page = inspected.get("result") if isinstance(inspected, dict) else {}
    page = page if isinstance(page, dict) else {}
    body = str(page.get("body") or "")
    positive = [token for token in ("发布", "通知", "消息", "我") if token in body]
    login_wall = any(token in body for token in ("手机号登录", "扫码登录", "登录/注册"))
    verified = len(positive) >= 3 and not login_wall and int(page.get("cookieLength") or 0) > 0
    evidence = {
        "url": str(page.get("url") or opened.get("url") or url),
        "title": str(page.get("title") or opened.get("title") or ""),
        "navigation_signals": positive,
        "cookie_present": int(page.get("cookieLength") or 0) > 0,
        "login_wall_present": login_wall,
    }
    if not verified:
        notification = _deliver_text(
            "⚠️ 我收到了“已登录”，但网页证据仍不足：没有同时看到登录后的导航入口，或登录弹窗仍存在。"
            "我不会假报登录成功，请回到前台网页完成登录后再回复“已登录”。"
        )
        return {
            "ok": False,
            "status": "login_not_verified",
            "verified": False,
            "evidence": evidence,
            "notification": notification,
            "retryable": False,
        }

    state_dir = os.path.join(workspace, "state")
    os.makedirs(state_dir, exist_ok=True)
    login_state_path = os.path.join(state_dir, "login_session.json")
    with open(login_state_path, "w", encoding="utf-8") as f:
        json.dump({
            "verified": True,
            "verified_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "site": "xiaohongshu",
            "evidence": evidence,
        }, f, ensure_ascii=False, indent=2)

    next_task = str(params.get("next_task") or (
        "【登录后自动继续：小红书长期任务】登录态已经用真实页面证据验证。"
        "现在必须直接调用且只调用 xiaohongshu_open_publish_editor，进入“上传图文”入口并保存真实截图与 JSON 证据。"
        "不得把只显示侧栏的创作平台空壳当成成功，不上传图片、不点击最终发布。"
        "完成后输出清晰汇报：做了什么、真实证据、发现的问题、下一步将自动执行什么。"
    )).strip()
    inbox_path = os.path.join(state_dir, "desktop_inbox.jsonl")
    row = {
        "id": f"login_verified_continue_{int(time.time())}",
        "role": "user",
        "content": next_task,
        "source": "login_verified_auto_continue",
    }
    with open(inbox_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    notification = _deliver_text(
        "✅ 已用网页证据确认 01 登录成功：页面存在“发布、通知、消息、我”等登录后入口，且登录会话 Cookie 已存在。\n"
        "▶️ 我已经自动开始下一步：进入发布入口并核验编辑界面；本轮不会执行最终发布。完成后我会说明实际动作、证据、问题和后续动作。"
    )
    notified = bool(notification.get("ok") and notification.get("delivered"))
    try:
        from partner.evolution.evolution_log import log_evolution
        log_evolution("login_verified_and_continued", detail={
            "evidence": evidence,
            "next_task_queued": True,
            "notified": notified,
        })
    except Exception:
        pass
    return {
        "ok": notified,
        "status": "login_verified_continuation_started" if notified else "login_verified_notification_failed",
        "verified": True,
        "evidence": evidence,
        "login_state_path": login_state_path,
        "next_task_queued": True,
        "next_task": next_task,
        "inbox_file": inbox_path,
        "notified": notified,
        "notification": notification,
    }


# ── D: atomic_write_artifact fallback ───────────────────────
def atomic_write_artifact_fallback(ctx, params: dict) -> dict:
    """atomic_write_artifact 失败（placeholder/content 拒）→ 自动 fallback 到 atomic_generate_pdf。

    把已成功 step 的真实 stdout/description 拼成简单报告 + 嵌入已生成 PNG，转 PDF。
    """
    wd = ""
    ti = getattr(ctx, "task_instance", None)
    if ti is not None:
        wd = getattr(ti, "working_dir", "") or ""
    if not wd or not os.path.isdir(wd):
        return {"ok": False, "error": "no working_dir"}
    # 收集已成功 step 数据
    real_content = []
    pngs = []
    for f in sorted(os.listdir(wd)):
        fp = os.path.join(wd, f)
        if f.endswith(".png") and os.path.getsize(fp) > 10000:
            pngs.append(fp)
        elif f.endswith(".json") and f.startswith("_step_") and f.endswith(".result.json"):
            try:
                row = json.load(open(fp, encoding="utf-8"))
                if row.get("ok"):
                    et = row.get("event_type", "")
                    stdout = str((row.get("result") or {}).get("stdout", ""))[:500]
                    desc = str((row.get("result") or {}).get("description", ""))[:300]
                    content = str((row.get("result") or {}).get("content", ""))[:300]
                    # 过滤 thinking 冒充（content 或 stdout 以 <think> 开头就跳过）
                    if stdout and not stdout.lstrip().startswith("<think>") and not stdout.lstrip().startswith("<thinking>"):
                        real_content.append(f"## {et}\n\n```\n{stdout[:500]}\n```\n")
                    elif desc and not desc.lstrip().startswith("<think>") and not desc.lstrip().startswith("<thinking>"):
                        real_content.append(f"## {et}\n\n{desc[:400]}\n")
                    elif content and not content.lstrip().startswith("<think>") and not content.lstrip().startswith("<thinking>"):
                        # 不超过 2000 字符的内容是真实数据
                        if len(content) < 2000:
                            real_content.append(f"## {et}\n\n{content[:400]}\n")
            except Exception:
                pass
    if not real_content and not pngs:
        return {"ok": False, "error": "no real content to fallback with"}
    # 写 summary md
    summary = "# Real Execution Summary\n\n" + "\n".join(real_content)
    summary_md = os.path.join(wd, "real_execution_summary.md")
    try:
        with open(summary_md, "w", encoding="utf-8") as f:
            f.write(summary)
    except Exception as e:
        return {"ok": False, "error": f"write summary: {e}"}
    # 调 atomic_generate_pdf
    from partner.v2.pdf_events import atomic_generate_pdf
    r = atomic_generate_pdf(ctx, {
        "source_path": summary_md,
        "output_path": os.path.join(wd, "fallback_report.pdf"),
        "title": "Real Execution Report",
        "image_paths": pngs,
    })
    try:
        from partner.evolution.evolution_log import log_evolution
        log_evolution("write_fallback_to_pdf", detail={"ok": r.get("ok"), "size": r.get("size", 0), "pngs": len(pngs)})
    except Exception:
        pass
    return r

# ── G: 用户回复"否"时跳过登录 ────────────────────────
def atomic_skip_login(ctx, params: dict) -> dict:
    """用户回「否」后调用：跳过登录，继续原任务（部分功能受限）。"""
    try:
        from partner.evolution.evolution_log import log_evolution
        log_evolution("login_skipped", detail={"ctx": str(ctx)[:200]})
    except Exception:
        pass
    wd = ""
    ti = getattr(ctx, "task_instance", None)
    if ti is not None:
        wd = getattr(ti, "working_dir", "") or ""
    if wd:
        try:
            with open(os.path.join(wd, "LOGIN_SKIPPED.md"), "w", encoding="utf-8") as f:
                f.write("# Login Skipped\n\n")
                f.write(f"用户选择跳过登录（{time.strftime('%Y-%m-%d %H:%M:%S')}）。\n")
                f.write("Partner 继续原任务，部分需登录的功能将不可用。\n")
        except Exception:
            pass
    try:
        if ti is not None:
            ti.append_log("login_status", {"skipped": True, "at": time.time()})
            for attr in ("status", "completion_status"):
                try:
                    setattr(ti, attr, "running")
                except Exception:
                    pass
    except Exception:
        pass
    return {"ok": True, "skipped": True}


# ── H: 通过 QQ Bot API 真实发文本消息给用户 ────────────────────────
def atomic_send_user_text(ctx, params: dict) -> dict:
    """直接通过 QQ Bot API 发文本消息给用户（绕过 inbox/inbound）。

    参数:
        text: str — 消息内容（必填）
        instance: str — 实例编号 "01" (可选，自动从 ctx 推)
    """
    text = str(params.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "缺少 text 参数"}
    try:
        result = _deliver_text(text)
        try:
            from partner.evolution.evolution_log import log_evolution
            log_evolution("user_text_sent" if result.get("delivered") else "user_text_send_failed", detail={
                "len": len(text),
                "status": result.get("status", ""),
                "delivered": bool(result.get("delivered")),
            })
        except Exception:
            pass
        return result
    except Exception as exc:
        return {"ok": False, "delivered": False, "status": "failed", "error": f"text delivery failed: {exc}"}

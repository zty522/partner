"""迭代引擎事件 —— 用户意图理解 / 计划文档 / 严格反思批评 / 下一轮迭代启动。

四个事件构成"意图→计划→执行→反思→下一轮"的主动迭代闭环：
- understand_intent: 解析用户初始意图（目标/约束/成功标准/想要的"效果"）
- write_plan:       基于意图 + 可用能力写迭代计划文档
- strict_reflect:   对本轮执行结果做严格反思批评（证据驱动，找根因与缺口）
- next_iteration:   根据意图 + 上一轮反思提出下一轮计划并启动执行（写 inbox），不等待
"""

from __future__ import annotations
from partner.evolution.evolution_log import log_evolution


import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]

_ITER_ROOT = os.path.join("state", "iterations")
_MAX_ITERATIONS = 5


def _workspace_of(ctx) -> str:
    # 多源 fallback：ctx.workspace → ctx.task_instance.workspace → working_dir 推导
    ws = str(getattr(ctx, "workspace", "") or "")
    if ws:
        return ws
    ti = getattr(ctx, "task_instance", None)
    if ti is not None:
        ws = str(getattr(ti, "workspace", "") or "")
        if ws:
            return ws
        wd = str(getattr(ti, "working_dir", "") or "")
        if wd:
            # 从 working_dir 推导 workspace（如 .../instances/02/state/tasks/xxx → .../instances/02）
            import re as _re
            m = _re.search(r"^(/.+/instances/[^/]+)/state/", wd)
            if m:
                return m.group(1)
            # 或直接取父级
            if "/state/tasks" in wd:
                return wd.split("/state/tasks")[0]
    return ""


def _task_id_of(ctx) -> str:
    # 多源 fallback：ctx.task_id → ctx.task_instance.id/task_id → task_instance.working_dir
    tid = str(getattr(ctx, "task_id", "") or "")
    if tid:
        return tid[:12]
    ti = getattr(ctx, "task_instance", None)
    if ti is not None:
        tid = getattr(ti, "id", "") or getattr(ti, "task_id", "")
        if tid:
            return str(tid)[:12]
        # 从 working_dir 取 task id 段
        wd = str(getattr(ti, "working_dir", "") or "")
        if wd:
            import re as _re
            m = _re.search(r"/tasks/([^/]+)/?$", wd)
            if m:
                return m.group(1)[:12]
    return "task"


def _iter_dir(ctx) -> str:
    d = os.path.join(_workspace_of(ctx), _ITER_ROOT, _task_id_of(ctx))
    os.makedirs(d, exist_ok=True)
    return d


def _read_iter_meta(ctx) -> dict:
    p = os.path.join(_iter_dir(ctx), "meta.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"round": 0, "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}


def _write_iter_meta(ctx, meta: dict):
    with open(os.path.join(_iter_dir(ctx), "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _llm(prompt: str, purpose: str = "action", max_tokens: int = 8000) -> str:
    try:
        from ..adapters.direct_api import chat
        return chat(prompt, max_tokens=max_tokens, temperature=0.2, purpose=purpose, timeout=120)
    except Exception as exc:
        logger.warning("[iteration] LLM call failed: %s", exc)
        return ""


# ── 1. 用户意图理解 ──────────────────────────────────────────────
def _parse_round(raw, meta):
    """解析 round 参数（容错：LLM 可能传数字字符串/"current"/"next" 等）。"""
    import re as _re
    if raw is None or raw == "":
        return int(meta.get("round", 1))
    if isinstance(raw, int):
        return raw
    s = str(raw).strip().lower()
    # 数字字符串
    if s.isdigit():
        return int(s)
    # current/now → 当前轮
    if s in ("current", "now", "this", "cur"):
        return int(meta.get("round", 1))
    # next/下一个 → 当前轮 + 1
    if s in ("next", "next_round", "next_iter", "next_iteration"):
        return int(meta.get("round", 1)) + 1
    # 提取数字
    m = _re.search(r"(\d+)", s)
    if m:
        return int(m.group(1))
    return int(meta.get("round", 1))


def atomic_understand_intent(ctx, params: JsonDict) -> JsonDict:
    """理解用户初始意图：目标 / 约束 / 成功标准 / 期望的"效果"。

    参数:
        task (str, 可选): 任务描述（缺省用 ctx 当前任务）
    返回: {ok, intent, file}
    """
    task = str(params.get("task") or "").strip()

    iter_dir = _iter_dir(ctx)
    log_evolution("iter_event_start", detail={"event": "atomic_understand_intent", "path": iter_dir})
    if not task:
        ti = getattr(ctx, "task_instance", None)
        task = str(getattr(ti, "content", "") or "")[:2000]
    if not task:
        return {"ok": False, "error": "缺少任务描述"}

    prompt = f"""你是任务意图分析师。请严格分析下面的用户任务，输出 JSON（不要其他文字）：

任务：{task}

输出格式：
{{"goal": "一句话核心目标", "constraints": ["约束1", "约束2"], "success_criteria": ["成功标准1", "成功标准2"], "effect": "用户期望看到的实际效果（不是流程，是可见结果）", "risk_points": ["可能的失败点"]}}

要求：
- effect 必须描述"用户最终能看到什么"，例如"浏览器真实打开页面并截图，read_image 确认截图里有真实内容"
- success_criteria 必须可验证（有证据），不要"完成任务"这类空话"""
    out = _llm(prompt, purpose="action", max_tokens=3000)
    intent = {}
    try:
        # 剥离 <think>...</think> 段（DeepSeek-R1/QwQ/MiniMax-M3 带思考前缀）
        _stripped = _re.sub(r"<think>.*?</think>", "", out, flags=_re.DOTALL).strip()
        # 括号配对找最外层 {...}
        start = _stripped.find("{")
        end = -1
        if start >= 0:
            depth = 0
            for _i in range(start, len(_stripped)):
                if _stripped[_i] == "{":
                    depth += 1
                elif _stripped[_i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = _i
                        break
        if start >= 0 and end > start:
            intent = json.loads(_stripped[start:end + 1])
    except Exception:
        intent = {"goal": task[:200], "effect": task[:200]}
    meta = _read_iter_meta(ctx)
    meta["intent"] = intent
    _write_iter_meta(ctx, meta)

    fpath = os.path.join(_iter_dir(ctx), "intent.md")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write("# 用户意图\n\n- 目标: " + str(intent.get("goal", "")) + "\n")
        f.write("- 期望效果: " + str(intent.get("effect", "")) + "\n")
        f.write("- 约束:\n" + "\n".join("  - " + str(c) for c in intent.get("constraints", [])) + "\n")
        f.write("- 成功标准:\n" + "\n".join("  - " + str(c) for c in intent.get("success_criteria", [])) + "\n")
    logger.info("[ITERATION] intent understood: %s", str(intent.get("goal", ""))[:80])
    return {"ok": True, "intent": intent, "file": fpath}


# ── 2. 写计划文档 ────────────────────────────────────────────────
async def atomic_write_plan(ctx, params: JsonDict) -> JsonDict:
    """基于意图 + 可用能力写本轮迭代计划文档。

    参数:
        round (int, 可选): 轮次（缺省 auto +1）
    返回: {ok, plan, file, round}
    """
    meta = _read_iter_meta(ctx)

    iter_dir = _iter_dir(ctx)
    log_evolution("iter_event_start", detail={"event": "atomic_write_plan", "path": iter_dir})
    try:
        rnd = int(params.get("round") or meta.get("round", 0) + 1)
    except (TypeError, ValueError):
        rnd = (meta.get("round", 0) if isinstance(meta.get("round"), int) else 0) + 1
    meta["round"] = rnd
    _write_iter_meta(ctx, meta)

    intent = meta.get("intent", {})
    prev_reflect = ""
    rp = os.path.join(_iter_dir(ctx), "reflect_" + str(rnd - 1) + ".md")
    if os.path.exists(rp):
        prev_reflect = open(rp, encoding="utf-8").read()[:2000]

    prompt = f"""你是迭代计划制定者。基于用户意图和上一轮反思（如有），制定第 {rnd} 轮执行计划。
输出 JSON（不要其他文字）：

用户意图：{json.dumps(intent, ensure_ascii=False)[:800]}
上一轮反思（可能为空）：{prev_reflect[:800]}

输出格式：
{{"round": {rnd}, "focus": "本轮聚焦点（改进/换策略/补能力）", "steps": [{{"event": "事件名", "params": {{}}, "reason": "为什么这步"}}], "expected_artifacts": ["预期产物"], "verify": "如何验证成功（必须有可见证据）", "stop_condition": "什么情况下迭代可以停止"}}

要求：
- 步骤必须用真实可用事件（browser_open/browser_type/browser_screenshot/read_image/execute_code/run_command/create_file/atomic_write_artifact/strict_reflect 等）
- 上一轮失败的根因必须在 steps 里体现改进（换选择器/等待/滚动/重试/换策略）
- verify 必须描述可见证据（截图内容/运行输出/文件内容）"""
    out = _llm(prompt, purpose="action", max_tokens=8000)
    plan = {}
    try:
        _stripped = _re.sub(r"<think>.*?</think>", "", out, flags=_re.DOTALL).strip()
        start = _stripped.find("{")
        end = -1
        if start >= 0:
            depth = 0
            for _i in range(start, len(_stripped)):
                if _stripped[_i] == "{":
                    depth += 1
                elif _stripped[_i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = _i
                        break
        if start >= 0 and end > start:
            plan = json.loads(_stripped[start:end + 1])
    except Exception:
        plan = {"round": rnd, "focus": "执行", "steps": [], "verify": ""}

    fpath = os.path.join(_iter_dir(ctx), "plan_" + str(rnd) + ".md")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write("# 第 " + str(rnd) + " 轮计划\n\n- 聚焦: " + str(plan.get("focus", "")) + "\n")
        f.write("- 验证: " + str(plan.get("verify", "")) + "\n")
        for i, st in enumerate(plan.get("steps", []), 1):
            f.write("- 步骤" + str(i) + ": " + str(st.get("event", "")) + " " + json.dumps(st.get("params", {}), ensure_ascii=False)[:200] + " — " + str(st.get("reason", "")) + "\n")
        f.write("- 停止条件: " + str(plan.get("stop_condition", "")) + "\n")
    logger.info("[ITERATION] plan written: round %d, %d steps", rnd, len(plan.get("steps", [])))
    return {"ok": True, "plan": plan, "file": fpath, "round": rnd}


# ── 3. 严格反思批评 ──────────────────────────────────────────────
async def atomic_strict_reflect(ctx, params: JsonDict) -> JsonDict:
    """对本轮执行结果做严格反思批评（证据驱动）。

    读取任务目录产物 + 步骤结果，LLM 严格批评：真实成功 or 假成功、缺口、根因、下一轮改进。
    参数:
        round (int, 可选)
    返回: {ok, reflection, file, verdict}
    """
    from partner.state.config import manual_stable_mode, runtime_capability_enabled
    workspace = _workspace_of(ctx)
    if manual_stable_mode(workspace) or not runtime_capability_enabled(workspace, "automatic_iteration"):
        return {"ok": False, "status": "disabled_in_manual_stable", "retryable": False,
                "error": "自动反思/迭代已暂停；等待用户明确的新指令"}
    meta = _read_iter_meta(ctx)

    iter_dir = _iter_dir(ctx)
    log_evolution("iter_event_start", detail={"event": "atomic_strict_reflect", "path": iter_dir})
    try:
        rnd = int(params.get("round") or meta.get("round", 1))
    except (TypeError, ValueError):
        rnd = 1
    work_dir = str(getattr(ctx, "working_dir", "") or "")
    task_dir = str(getattr(getattr(ctx, "task_instance", None), "working_dir", "") or work_dir)

    evidence = []
    if task_dir and os.path.isdir(task_dir):
        for f in sorted(os.listdir(task_dir))[:30]:
            fp = os.path.join(task_dir, f)
            if f.endswith(".json") and "step" in f:
                try:
                    row = json.load(open(fp, encoding="utf-8"))
                    res = row.get("result", {})
                    err = str(res.get("error", ""))[:120]
                    line = "[" + f + "] " + str(row.get("event_type")) + " ok=" + str(row.get("ok"))
                    if err:
                        line += " err=" + err
                    evidence.append(line)
                except Exception:
                    pass
            elif f.endswith((".md", ".png", ".jpg", ".py", ".csv", ".txt")):
                sz = os.path.getsize(fp)
                preview = ""
                if f.endswith((".md", ".txt", ".py")) and sz < 3000:
                    try:
                        preview = open(fp, encoding="utf-8").read()[:120].replace("\n", " ")
                    except Exception:
                        pass
                evidence.append("[" + f + "] " + str(sz) + "B " + preview)

    intent = meta.get("intent", {})
    prompt = f"""你是严格的质量批评者，绝不放水。基于以下证据批评第 {rnd} 轮执行结果。

用户意图（期望效果）：{json.dumps(intent, ensure_ascii=False)[:500]}

本轮产物与步骤证据：
{chr(10).join(evidence)[:4000]}

输出 JSON（不要其他文字）：
{{"verdict": "success" 或 "fail", "evidence_based": "基于什么证据下此结论（引用具体文件/输出）", "issues": ["问题1", "问题2"], "root_causes": ["根因1"], "gaps": ["能力/工具/策略缺口"], "next_improvements": ["下一轮具体改进"]}}

要求：
- 必须基于证据（截图是否真实有内容、运行是否成功、文件内容是否真实结果），禁止只看"步骤 ok"
- 截图类产物要质疑：截图存在 ≠ 内容有效（可能空白/错误页）
- 文件内容要质疑：是否真实结果而非 design 模板/占位"""
    out = _llm(prompt, purpose="action", max_tokens=4000)
    reflection = {}
    try:
        _stripped = _re.sub(r"<think>.*?</think>", "", out, flags=_re.DOTALL).strip()
        start = _stripped.find("{")
        end = -1
        if start >= 0:
            depth = 0
            for _i in range(start, len(_stripped)):
                if _stripped[_i] == "{":
                    depth += 1
                elif _stripped[_i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = _i
                        break
        if start >= 0 and end > start:
            reflection = json.loads(_stripped[start:end + 1])
    except Exception:
        reflection = {"verdict": "fail", "evidence_based": "解析失败", "issues": [], "gaps": []}
    verdict = reflection.get("verdict", "fail")

    fpath = os.path.join(_iter_dir(ctx), "reflect_" + str(rnd) + ".md")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write("# 第 " + str(rnd) + " 轮反思（" + str(verdict) + "）\n\n- 证据: " + str(reflection.get("evidence_based", "")) + "\n")
        f.write("- 问题:\n" + "\n".join("  - " + str(i) for i in reflection.get("issues", [])) + "\n")
        f.write("- 根因:\n" + "\n".join("  - " + str(r) for r in reflection.get("root_causes", [])) + "\n")
        f.write("- 缺口:\n" + "\n".join("  - " + str(g) for g in reflection.get("gaps", [])) + "\n")
        f.write("- 下一轮改进:\n" + "\n".join("  - " + str(i) for i in reflection.get("next_improvements", [])) + "\n")
    logger.info("[ITERATION] reflect round %d: %s", rnd, verdict)
    # Scheduling is deliberately owned by the explicit next_iteration step.
    # Hidden scheduling here used to race with the plan's own next_iteration
    # step and created duplicate/self-referential tasks.
    return {"ok": True, "reflection": reflection, "file": fpath, "verdict": verdict}


def _log_next_iteration_to_inbox(ctx, rnd: int, verdict: str, hint: str) -> None:
    """fallback: 把下一轮提示写到 instance desktop_inbox，让 poller 自然触发。"""
    import json as _json
    import re as _re
    ws = ""
    ti = getattr(ctx, "task_instance", None)
    if ti is not None:
        ws = getattr(ti, "workspace", "") or ""
    if not ws:
        ws = getattr(ctx, "workspace", "") or ""
    if not ws:
        return
    m = _re.search(r"/instances/(\d{2})/", ws + " " + str(getattr(ctx, "task_instance", "")))
    if not m:
        return
    instance = m.group(1)
    inbox = os.path.join(ws, "instances", instance, "state", "desktop_inbox.jsonl")
    if not os.path.exists(os.path.dirname(inbox)):
        return
    msg = {
        "id": f"auto_next_iter_{rnd}_{instance}",
        "role": "user",
        "content": f"[自动迭代触发] 上一轮反思 {verdict}：{hint}。请基于上轮反思直接执行改进（不要等用户指令），完成后再次调用 atomic_strict_reflect + atomic_next_iteration。",
        "source": "self_evolution_auto_iter",
    }
    try:
        with open(inbox, "a", encoding="utf-8") as f:
            f.write(_json.dumps(msg, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ── 4. 下一轮计划并启动迭代 ──────────────────────────────────────
async def atomic_next_iteration(ctx, params: JsonDict) -> JsonDict:
    """根据意图 + 上一轮反思提出下一轮计划并启动执行（写 inbox），不等待。

    参数:
        round (int, 可选): 当前已完成轮次
    返回: {ok, status: "done"(目标达成) | "started"(已启动下一轮) | "max_reached", next_content, inbox_file}
    """
    from partner.state.config import manual_stable_mode, runtime_capability_enabled
    workspace = _workspace_of(ctx)
    if manual_stable_mode(workspace) or not runtime_capability_enabled(workspace, "automatic_iteration"):
        return {"ok": False, "status": "disabled_in_manual_stable", "retryable": False,
                "error": "自动下一轮已暂停；等待用户明确的新指令"}
    meta = _read_iter_meta(ctx)

    iter_dir = _iter_dir(ctx)
    log_evolution("iter_event_start", detail={"event": "atomic_next_iteration", "path": iter_dir})
    try:
        rnd = int(params.get("round") or meta.get("round", 1))
    except (TypeError, ValueError):
        rnd = 1
    try:
        max_iter = int(params.get("max_iterations") or _MAX_ITERATIONS)
    except (TypeError, ValueError):
        max_iter = _MAX_ITERATIONS

    intent = meta.get("intent", {})
    reflect_path = os.path.join(_iter_dir(ctx), "reflect_" + str(rnd) + ".md")
    # 容错：LLM 可能传"下一轮"编号（如 strict_reflect 后传 round=2），回退到已存在的最近反思
    while rnd > 1 and not os.path.exists(reflect_path):
        rnd -= 1
        reflect_path = os.path.join(_iter_dir(ctx), "reflect_" + str(rnd) + ".md")
    if not os.path.exists(reflect_path):
        return {"ok": False, "error": "缺少第 " + str(rnd) + " 轮反思（先执行 strict_reflect）"}

    if rnd >= max_iter:
        return {"ok": True, "status": "max_reached", "message": "已达最大迭代 " + str(max_iter) + " 轮", "round": rnd}

    reflect = open(reflect_path, encoding="utf-8").read()[:2000]

    explicit_next_task = str(params.get("next_task") or "").strip()
    prompt = f"""你是迭代启动器。基于意图和上一轮反思，生成下一轮（第 {rnd + 1} 轮）的执行指令。
该指令将作为新任务发给同一实例继续执行（不等待、不放弃）。

用户意图：{json.dumps(intent, ensure_ascii=False)[:600]}
第 {rnd} 轮反思：{reflect[:1200]}

输出 JSON（不要其他文字）：
{{"continue": true 或 false, "reason": "继续或停止的原因", "next_task": "给下一轮执行者的完整指令（包含：意图重申、上轮反思要点、本轮必须改进的具体策略、要使用的真实事件、预期产物与验证方式）"}}

要求：
- 如果反思 verdict=success 且证据充分（产物真实有效），continue=false
- 否则 continue=true，next_task 必须具体可执行（比如"改用 browser_execute 执行 JS 点击登录按钮"而不是"想办法登录"）"""
    out = "" if explicit_next_task else _llm(prompt, purpose="action", max_tokens=4000)
    decision = {}
    try:
        _stripped = _re.sub(r"<think>.*?</think>", "", out, flags=_re.DOTALL).strip()
        start = _stripped.find("{")
        end = -1
        if start >= 0:
            depth = 0
            for _i in range(start, len(_stripped)):
                if _stripped[_i] == "{":
                    depth += 1
                elif _stripped[_i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = _i
                        break
        if start >= 0 and end > start:
            decision = json.loads(_stripped[start:end + 1])
    except Exception:
        decision = {"continue": True, "next_task": ""}

    if not decision.get("continue"):
        meta["done"] = True
        _write_iter_meta(ctx, meta)
        return {"ok": True, "status": "done", "message": str(decision.get("reason", "目标达成")), "round": rnd}

    next_task = explicit_next_task or str(decision.get("next_task") or "").strip()
    if not next_task:
        improvements = []
        in_improvements = False
        for line in reflect.splitlines():
            stripped = line.strip()
            if stripped.startswith("- 下一轮改进"):
                in_improvements = True
                continue
            if in_improvements and stripped.startswith("- "):
                improvements.append(stripped[2:].strip())
            elif in_improvements and stripped and not stripped.startswith("-"):
                break
        if improvements:
            next_task = (
                f"【第 {rnd + 1} 轮自动改进】基于真实反思，立即执行以下改进："
                + "；".join(improvements[:3])
                + "。必须包含真实操作与验证证据；完成后清楚汇报已做动作、证据、未解决问题和后续动作。"
            )
        else:
            return {"ok": False, "retryable": False, "error": "反思中没有可执行的下一轮改进，拒绝生成空泛任务"}

    workspace = _workspace_of(ctx)
    inbox = os.path.join(workspace, "state", "desktop_inbox.jsonl")
    row = {
        "id": "iter_" + _task_id_of(ctx) + "_" + str(rnd + 1) + "_" + str(int(time.time())),
        "role": "user",
        "content": "【第 " + str(rnd + 1) + " 轮迭代（迭代引擎）】" + next_task,
        "source": "iteration_engine",
        "iteration": {"round": rnd + 1, "task_id": _task_id_of(ctx)},
    }
    try:
        with open(inbox, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        return {"ok": False, "error": "启动下一轮失败: " + str(exc)}

    meta["round"] = rnd + 1
    _write_iter_meta(ctx, meta)
    logger.info("[ITERATION] next round %d started via inbox: %s", rnd + 1, inbox)
    return {"ok": True, "status": "started", "next_content": row["content"], "inbox_file": inbox, "round": rnd + 1}

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_content_digest.py
对 partner/mind/executor.py::_handle_content_digest 的分析验证脚本。

方法（保证"运行的是仓库里的真实代码"）：
  1) AST 静态分析真实源码（不执行），定位可疑模式与行号；
  2) 用 ast.get_source_segment 从真实源码【逐字抽取】被测函数
     _handle_content_digest，以及 content_feed.py 中的
     get_open_content_items / mark_content_processed / _clip / _load_feed / _save_feed、
     executor.py 中的 _sanitize_user_report_text / _is_internal_fallback_text，
     在受控桩环境下真实运行（被测代码字节级等于仓库源码）。
  3) 逐场景断言，输出 PASS / 问题确认。
运行：python3 scripts/verify_content_digest.py
"""

import asyncio
import ast
import importlib.util
import json
import os
import re
import sys
import tempfile
import types
from datetime import datetime
from typing import Any, Optional

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXECUTOR = os.path.join(REPO, "partner", "mind", "executor.py")
CONTENT_FEED = os.path.join(REPO, "partner", "knowledge", "content_feed.py")
EVENT_TYPES = os.path.join(REPO, "partner", "mind", "event_types.py")

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    tag = "已确认" if ok else "未复现"
    print(f"[{tag}] {name}" + (f"  -- {detail}" if detail else ""))


# ---- 0. 真实加载轻量依赖 event_types.py（纯标准库） ----
def _make_pkg(name, path):
    m = types.ModuleType(name)
    m.__path__ = [path]
    m.__package__ = name
    sys.modules[name] = m
    return m


_make_pkg("partner", os.path.join(REPO, "partner"))
_make_pkg("partner.mind", os.path.join(REPO, "partner", "mind"))
_make_pkg("partner.knowledge", os.path.join(REPO, "partner", "knowledge"))
_make_pkg("partner.projects", os.path.join(REPO, "partner", "projects"))

_spec = importlib.util.spec_from_file_location("partner.mind.event_types", EVENT_TYPES)
_event_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_event_mod)
EventType = _event_mod.EventType
MindEvent = _event_mod.MindEvent


# ---- 1. AST 静态分析（真实源码，不执行） ----
def _extract_src(path, name):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node), src, tree, node.lineno
    raise SystemExit(f"未找到函数 {name} in {path}")


digest_src, executor_src, _, _FUNC_LINE = _extract_src(EXECUTOR, "_handle_content_digest")
OFFSET = _FUNC_LINE - 1  # 相对行号 -> 文件真实行号
digest_ast = ast.parse(digest_src)

print("=" * 78)
print("第一部分：AST 静态分析（真实源码 executor.py）")
print("=" * 78)

assign_lines = [
    n.lineno for n in ast.walk(digest_ast)
    if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "prompt" for t in n.targets)
]
load_lines = [
    n.lineno for n in ast.walk(digest_ast)
    if isinstance(n, ast.Name) and n.id == "prompt" and isinstance(n.ctx, ast.Load)
]
assign_lines = [l + OFFSET for l in assign_lines]
load_lines = [l + OFFSET for l in load_lines]
print(f"\n[静态] 'prompt' 赋值行(真实): {assign_lines}")
print(f"[静态] 'prompt' 读取行(真实): {load_lines}")
dead = [ln for ln in assign_lines if not any(l > ln for l in load_lines)]
record("F1 主分类 prompt 是死代码（item 分支赋值后从未被读取）", bool(dead),
       f"无后续读取的赋值行={dead}；item 分支的消化主 prompt 从未传给任何 LLM 调用")

stop_lines = [
    n.lineno for n in ast.walk(digest_ast)
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    and n.func.attr == "get" and n.args
    and isinstance(n.args[0], ast.Constant) and n.args[0].value == "stop_after_completion"
]
item_assign = next(
    n.lineno for n in ast.walk(digest_ast)
    if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "item" for t in n.targets)
)
print(f"\n[静态] stop_after_completion 读取行(真实): {[l + OFFSET for l in stop_lines]}；'item = items[0]' 行(真实): {item_assign + OFFSET}")
record("F2 stop_after_completion 仅在无 items 分支生效", all(l < item_assign for l in stop_lines),
       "有内容可消化时该标志被忽略（不停止、不清活跃项目）")

def _slice_parts(sl):
    if isinstance(sl, ast.Constant):
        return [sl.value]
    if isinstance(sl, ast.Slice):
        out = []
        for v in (sl.lower, sl.upper, sl.step):
            if isinstance(v, ast.Constant):
                out.append(v.value)
        return out
    return []

slices = [
    (n.lineno + OFFSET, ast.unparse(n))
    for n in ast.walk(digest_ast)
    if isinstance(n, ast.Subscript) and _slice_parts(n.slice)
]
print(f"\n[静态] 常量切片(真实行号): {slices}")
record("F3 media_paths[:4] + vision_paths[:8] 存在丢图风险",
       any("media_paths" in s and ":4" in s for _, s in slices),
       "第4张之后的图片在进入分割前即被截断（详见运行时 S3）")

mark_line = next(
    n.lineno for n in ast.walk(digest_ast)
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    and n.func.id == "mark_content_processed"
)
open_digest = next(
    n.lineno for n in ast.walk(digest_ast)
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "open"
)
print(f"\n[静态] mark_content_processed 行(真实)={mark_line + OFFSET}；首次 open(写盘) 行(真实)={open_digest + OFFSET}")
record("F4 先标记 digested 再写盘（写盘失败则消化内容永久丢失）", mark_line < open_digest,
       "写盘异常会使 item 已标记但日志缺失，且异常向上冒泡")

llm_calls = [
    (n.lineno, n.func.attr) for n in ast.walk(digest_ast)
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    and n.func.attr in ("chat", "chat_with_images")
]
print(f"\n[静态] LLM 调用点(真实): {[(l + OFFSET, a) for l, a in llm_calls]}")
chat_lines = [ln for ln, a in llm_calls if a == "chat"]
record("F5 文本类内容不调用任何 LLM（chat 仅在无 items 的 payload-only 分支）",
       all(l < item_assign for l in chat_lines)
       and any(a == "chat_with_images" for _, a in llm_calls),
       f"chat 行={chat_lines}；chat_with_images 行="
       f"{[ln for ln, a in llm_calls if a == 'chat_with_images']}")

kw_patterns = [
    "TMEM41B", "CLCC1", "APOB", "VLDL", "MASH", "脂肪肝",
    "内质网", "磷脂翻转", "健康建议", "证据", "机制",
]
stripped = [p for p in kw_patterns if p.strip("\\r") != p]
record("F6 pattern.strip('\\\\r') 为无操作（不影响结果，属代码噪音）", not stripped,
       f"所有 {len(kw_patterns)} 个关键词 strip 后均不变" if not stripped
       else f"被改动: {stripped}")


# ---- 2. 抽取真实辅助函数并执行（content_feed.py / executor.py 逐字源码） ----
def _exec_many(path, names, globals_dict):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    parts = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name in names:
            parts.append(ast.get_source_segment(src, node))
    code = "\n\n".join(parts)
    exec(compile(code, path, "exec"), globals_dict)
    return globals_dict


def _state_path(workspace, *parts):
    path = os.path.join(workspace, "state", *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _now():
    return datetime.now().isoformat(timespec="seconds")


feed_g = {
    "os": os, "re": re, "json": json, "datetime": datetime,
    "Any": Any, "Optional": Optional,
    "_now": _now, "_state_path": _state_path,
    "_write_user_summary": lambda workspace, feed: None,
}
_exec_many(CONTENT_FEED,
           {"get_open_content_items", "mark_content_processed", "_clip",
            "_load_feed", "_save_feed"}, feed_g)
get_open_content_items = feed_g["get_open_content_items"]
mark_content_processed = feed_g["mark_content_processed"]
globals().update(feed_g)  # _load_feed/_save_feed/_clip 等供 harness 直接引用

ex_g = {"re": re, "json": json,
        "_is_internal_fallback_text": lambda t: False,
        "strip_internal_diff": lambda t: t,
        "has_internal_diff": lambda t: False}
_exec_many(EXECUTOR, {"_sanitize_user_report_text"}, ex_g)
_sanitize = ex_g["_sanitize_user_report_text"]


# ---- 3. 桩环境（被测函数内部 import 的模块，注册进 sys.modules） ----
CALLS = {"signals": [], "episodes": [], "reports": [], "pool": [],
         "project_state": [], "split": [], "ocr": [], "clear_active": []}


class Logger:
    def info(self, *a):  pass
    def warning(self, *a):  pass
    def debug(self, *a):  pass
    def error(self, *a):  print("[ERROR]", *a)


logger = Logger()


class FakeAdapter:
    """记录 chat / chat_with_images 调用，按场景配置返回值。"""

    def __init__(self, chat_reply="", vision_reply="ok", has_vision=True):
        self.chat_reply = chat_reply
        self.vision_reply = vision_reply
        self.has_vision = has_vision
        self.chat_calls = []
        self.vision_calls = []

    def chat(self, prompt, purpose="", **kw):
        self.chat_calls.append({"purpose": purpose, "prompt_head": prompt[:40]})
        return self.chat_reply

    def chat_with_images(self, prompt, paths, purpose="", **kw):
        self.vision_calls.append({"purpose": purpose, "paths": list(paths),
                                  "prompt_head": prompt[:40]})
        if not self.has_vision:
            return ""
        return self.vision_reply


class FakePool:
    async def put(self, event):
        CALLS["pool"].append(event)


async def _enqueue_visible_report(content, event_type, *, event_kind="", priority=3,
                                  source="", parent_id="", force_send=True,
                                  bypass_rate_limit=False, files=None):
    CALLS["reports"].append({"content": content, "event_kind": event_kind,
                             "priority": priority, "source": source,
                             "force_send": force_send, "bypass_rate_limit": bypass_rate_limit})


async def _ensure_pool():
    return _POOL


_POOL = FakePool()


ct_mod = types.ModuleType("partner.knowledge.content_tools")
ct_mod.__package__ = "partner.knowledge"


def _split_image_for_vision(path, dest_dir, **kw):
    CALLS["split"].append(path)
    return [path]  # 默认不切分（普通图片）


def _ocr_image_path(path):
    CALLS["ocr"].append(path)

    class R:  # AcquisitionResult 最小替身
        status = "text_available"
        text_preview = "OCR 转写：TMEM41B 与内质网应激……"
    return R()


ct_mod.split_image_for_vision = _split_image_for_vision
ct_mod.ocr_image_path = _ocr_image_path
sys.modules["partner.knowledge.content_tools"] = ct_mod

rm_mod = types.ModuleType("partner.knowledge.research_memory")
rm_mod.__package__ = "partner.knowledge"


def _record_user_signal(workspace, project, text, kind="user_idea"):
    CALLS["signals"].append({"project": project, "kind": kind, "text_head": text[:60]})


def _record_episode(workspace, project, event, evidence="", lesson="", risk="", links=None):
    CALLS["episodes"].append({"project": project, "evidence": evidence, "risk": risk})


rm_mod.record_user_signal = _record_user_signal
rm_mod.record_episode = _record_episode
sys.modules["partner.knowledge.research_memory"] = rm_mod

ps_mod = types.ModuleType("partner.projects.project_state")
ps_mod.__package__ = "partner.projects"


def _clear_active(workspace, name=""):
    CALLS["clear_active"].append(name)


def _get_project_status(workspace, project):
    return CALLS["project_state"][0] if CALLS["project_state"] else "waiting"


def _set_project_status(workspace, project, status, reason=""):
    CALLS["project_state"].append((project, status, reason))


ps_mod.clear_active = _clear_active
ps_mod.get_project_status = _get_project_status
ps_mod.set_project_status = _set_project_status
sys.modules["partner.projects.project_state"] = ps_mod


# ---- 4. 运行被测函数（真实源码逐字执行） ----
def make_handler(workspace, adapter, wake_stub=None):
    fn_globals = {
        "os": os, "re": re, "json": json, "datetime": datetime,
        "EventType": EventType, "MindEvent": MindEvent,
        "logger": logger,
        "_workspace": workspace, "_adapter": adapter,
        "get_open_content_items": get_open_content_items,
        "mark_content_processed": mark_content_processed,
        "_enqueue_visible_report": _enqueue_visible_report,
        "UNAVAILABLE_NOTICE": "[暂不可用]",
        "_sanitize_user_report_text": _sanitize,
        "_is_internal_fallback_text": lambda t: any(
            tok in (t or "") for tok in (
                "PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE",
                "Error: agent backend not available",
                "Reached maximum iterations", "tirith security scanner",
                "cannot access the image", "image was not provided",
            )),
        "ensure_pool": _ensure_pool,
        "_should_wake_waiting_literature_project": wake_stub or (lambda project, item: True),
        "__package__": "partner.mind", "__name__": "partner.mind.executor",
    }
    exec(compile(digest_src, EXECUTOR, "exec"), fn_globals)
    return fn_globals["_handle_content_digest"]


def reset_calls():
    for k in CALLS:
        CALLS[k] = []


def write_feed(workspace, items):
    feed = {"version": 1, "updated_at": _now(), "items": items}
    _save_feed(workspace, feed)


def load_feed(workspace):
    return _load_feed(workspace)


def read(path):
    if os.path.exists(path):
        return open(path, encoding="utf-8").read()
    return ""


def new_workspace():
    ws = tempfile.mkdtemp(prefix="digest_verify_")
    os.makedirs(os.path.join(ws, "system", "content_feed"), exist_ok=True)
    os.makedirs(os.path.join(ws, "system", "media", "vision_segments"), exist_ok=True)
    return ws


def make_item(iid, **over):
    base = {
        "id": iid, "time": _now(), "source": "test", "sender": "user",
        "project": "", "platform": "xhs", "intent": "general_learning",
        "access_status": "text_available", "scope": "general",
        "should_nudge_project": False, "urls": [], "text": "测试分享内容",
        "acquisition": {}, "media_files": [], "attachment_files": [],
        "visible_body": "", "raw_hint": "", "status": "open", "digest": "",
        "hypotheses": [], "risk": "",
    }
    base.update(over)
    return base


def run(name, items, payload, adapter=None, ws=None, wake_stub=None, setup=None):
    reset_calls()
    ws = ws or new_workspace()
    if setup:
        setup(ws)
    write_feed(ws, items)
    adapter = adapter or FakeAdapter()
    handler = make_handler(ws, adapter, wake_stub)
    ev = MindEvent(type=EventType.CONTENT_DIGEST, priority=1,
                   payload=payload, source="verify", parent_id="evt_root")
    asyncio.run(handler(ev))
    return {"ws": ws, "adapter": adapter, "feed": load_feed(ws),
            "digests": read(os.path.join(ws, "system", "content_feed", "digests.md")),
            "journal": read(os.path.join(ws, "state", "user", "partner_mind",
                                         "general_learning_journal.md"))}


print("\n" + "=" * 78)
print("第二部分：运行时验证（被测函数 = 仓库真实源码，逐字抽取执行）")
print("=" * 78)

# ---- S1: 纯文本 general_learning：不调用 LLM，输出为本地模板 ----
print("\n--- S1 纯文本内容（无媒体）---")
body = ("TMEM41B 与 CLCC1 在内质网应激中的作用，APOB 与 VLDL 组装，MASH 脂肪肝机制。"
        "这是一篇科普改写，需要查原始论文核验。")
r = run("S1", [make_item("cf_A", visible_body=body, text=body[:80],
                         urls=["https://x.com/a"], platform="xhs")],
        {"project": ""})
adapter = r["adapter"]
record("S1a 文本类内容 adapter.chat 调用数为 0（主分类 prompt 从未执行）",
       len(adapter.chat_calls) == 0, f"chat_calls={adapter.chat_calls}")
record("S1b 文本类 digest 为本地模板拼装（关键词+摘要），非 LLM 摘要",
       "1. 内容要点：已收到可读正文" in r["digests"] and "TMEM41B" in r["digests"],
       "digests.md 中出现'已收到可读正文'模板与关键词 TMEM41B")
record("S1c 通用学习信号 kind=external_learning 且写入通用学习日志",
       CALLS["signals"] and CALLS["signals"][0]["kind"] == "external_learning"
       and "general_learning" in r["journal"],
       f"signal={CALLS['signals'][0] if CALLS['signals'] else None}")
record("S1d item 被真实 mark_content_processed 标记为 digested",
       [i for i in r["feed"]["items"] if i["id"] == "cf_A"][0]["status"] == "digested")

# ---- S1b: 多个 open item 且无 content_id：取"最新5条中最旧一条"(FIFO) ----
print("\n--- S1b 无 content_id 时多 item 的选取顺序 ---")
r = run("S1b", [
    make_item("cf_1", visible_body="第一条", urls=["https://x/1"]),
    make_item("cf_2", visible_body="第二条", urls=["https://x/2"]),
    make_item("cf_3", visible_body="第三条", urls=["https://x/3"]),
], {"project": ""})
digested_ids = [i["id"] for i in r["feed"]["items"] if i["status"] == "digested"]
record("S1b 无 content_id 只消化 1 条且取的是最旧一条（FIFO，非最新）",
       digested_ids == ["cf_1"],
       f"digested={digested_ids}；get_open_content_items 内部 reversed 两次导致顺序反转")

# ---- S2: 视觉成功路径：general_learning 被静默改写为 project_reference ----
print("\n--- S2 视觉成功 + 有 project + general_learning ---")
ws = new_workspace()
img1 = os.path.join(ws, "shot1.png")
open(img1, "w").write("x")
r = run("S2", [make_item("cf_B", project="P", intent="general_learning", scope="general",
                         access_status="media_available",
                         media_files=[{"text_preview": img1}],
                         text="随手分享的健康科普截图")],
        {"project": "P"},
        adapter=FakeAdapter(vision_reply="1. 图片可见正文：截图文字。\n"
                                         "2. 核心健康主张：A。\n"
                                         "3. 初步分类：事实。\n"
                                         "4. 证据风险：需核验。"))
sig = CALLS["signals"][0] if CALLS["signals"] else {}
record("S2a general_learning 被改写为 project_reference（intent/scope 突变）",
       "[project_reference/text_available]" in r["digests"],
       "digests.md 头部标记 intent=project_reference")
record("S2b 改写后信号被记为项目 user_idea（本应为 external_learning）",
       sig.get("kind") == "user_idea" and sig.get("project") == "P",
       f"signal={sig}")
record("S2c 改写后不再写入通用学习日志（scope 被改为 project）",
       r["journal"] == "",
       "journal 为空 → 随手分享的图片材料丢失于通用学习库")

# ---- S2b: 视觉失败 → OCR 兜底，同样触发改写 ----
print("\n--- S2b 视觉失败、OCR 兜底成功 ---")
r = run("S2b", [make_item("cf_C", project="P", intent="general_learning", scope="general",
                          access_status="media_available",
                          media_files=[{"text_preview": img1}])],
        {"project": "P"},
        adapter=FakeAdapter(vision_reply="", has_vision=True))
record("S2b OCR 兜底路径同样把 general_learning 改写成 project_reference",
       CALLS["ocr"] and "[project_reference/text_available]" in r["digests"],
       f"ocr_calls={len(CALLS['ocr'])}")

# ---- S3: 6 张图，只处理前 4 张 ----
print("\n--- S3 6 张图片：media_paths[:4] 静默丢弃 ---")
ws = new_workspace()
imgs = []
for i in range(6):
    p = os.path.join(ws, f"img{i}.png")
    open(p, "w").write("x")
    imgs.append({"text_preview": p})
r = run("S3", [make_item("cf_D", project="", access_status="media_available",
                         media_files=imgs)],
        {"project": ""},
        adapter=FakeAdapter(vision_reply="vision ok"))
split_paths = CALLS["split"]
s3_adapter = r["adapter"]
vision_paths = s3_adapter.vision_calls[0]["paths"] if s3_adapter.vision_calls else []
record("S3 第 5、6 张图从未进入分割/视觉/OCR（静默丢弃）",
       len(split_paths) == 4 and len(vision_paths) == 4,
       f"split 收到 {len(split_paths)} 张；vision 收到 {len(vision_paths)} 张（共 6 张）")

# ---- S4: stop_after_completion=True 但有内容可消化 → 被忽略 ----
print("\n--- S4 stop_after_completion 被忽略 ---")
r = run("S4", [make_item("cf_E", visible_body="正文内容")],
        {"project": "", "stop_after_completion": True})
status_e = [i for i in r["feed"]["items"] if i["id"] == "cf_E"][0]["status"]
record("S4 有内容时 stop_after_completion 被忽略（不停止、不清活跃项目）",
       CALLS["clear_active"] == [] and status_e == "digested",
       f"clear_active 未被调用；item 状态={status_e}")

# ---- S5: should_nudge_project="false" 字符串 → bool() 为 True → 误唤醒 ----
print("\n--- S5 should_nudge_project 类型陷阱 ---")
r = run("S5", [make_item("cf_F", project="P", intent="project_reference",
                         should_nudge_project="false")],
        {"project": "P"},
        wake_stub=lambda project, item: True)
woke = [c for c in CALLS["project_state"] if c[1] == "active"]
record("S5 字符串 'false' 被 bool() 判为 True → 项目被误唤醒",
       len(woke) == 1 and len(CALLS["pool"]) == 2,
       f"set_project_status(active) 调用={woke}；入队 REPORT+PROJECT 事件={len(CALLS['pool'])}")
r = run("S5b", [make_item("cf_G", project="P", intent="project_reference",
                          should_nudge_project=False)],
        {"project": "P"}, wake_stub=lambda project, item: True)
record("S5 对照组：布尔 False 不唤醒", CALLS["project_state"] == [] and CALLS["pool"] == [],
       "无唤醒、无事件")

# ---- S6: item 缺 id → mark_content_processed 为 no-op → 永不消化 ----
print("\n--- S6 缺 id 的 item ---")
bad = make_item("cf_H", visible_body="x")
del bad["id"]
r = run("S6", [bad], {"project": ""})
record("S6 无 id 的 item 永远无法被标记 digested（mark 内部直接 return）",
       [i for i in r["feed"]["items"]][0]["status"] == "open",
       "状态仍为 open → 后续每次 digest 事件都会重复消费同一条")

# ---- S7: 关键词提取与 strip 噪音 ----
print("\n--- S7 关键词提取 ---")
compact = re.sub(r"\s+", " ", "TMEM41B 与 CLCC1；TMEM41B 重复出现，APOB 相关。")
kws = []
for pattern in kw_patterns:
    if re.search(pattern, compact, re.I):
        kws.append(pattern.strip("\\r"))
key_line = "、".join(list(dict.fromkeys(kws))[:8])
record("S7 关键词提取 + 去重正常，strip('\\\\r') 无副作用",
       key_line == "TMEM41B、CLCC1、APOB" and all(p.strip("\\r") == p for p in kw_patterns),
       f"key_line={key_line}")

# ---- S8: 无 project 的 item 被项目级事件消费 → digests.md 错标 ----
print("\n--- S8 归属错标 ---")
r = run("S8", [make_item("cf_I", intent="general_learning", visible_body="无归属内容")],
        {"project": "P"})
sig = CALLS["signals"][0] if CALLS["signals"] else {}
record("S8 项目级事件消费无归属 item：digests.md 标为 P 项目但信号记为无项目",
       "P [general_learning/text_available]" in r["digests"] and sig.get("project") == "",
       f"digests 头标 P；signal.project={sig.get('project')!r}")

# ---- S9: 写盘失败：item 已先标记 digested，内容丢失且异常冒泡 ----
print("\n--- S9 digests.md 写盘失败 ---")
import builtins
_real_open = builtins.open


def _flaky_open(path, *a, **kw):
    if str(path).endswith("digests.md"):
        raise OSError("disk full (simulated)")
    return _real_open(path, *a, **kw)


ws9 = new_workspace()
builtins.open = _flaky_open
try:
    r = run("S9", [make_item("cf_J", visible_body="重要正文")], {"project": ""}, ws=ws9)
    propagated = False
except OSError:
    propagated = True
finally:
    builtins.open = _real_open
status_j = [i for i in load_feed(ws9)["items"] if i["id"] == "cf_J"][0]["status"]
record("S9 写盘异常：事件抛错 且 item 已被标记 digested（内容永久丢失）",
       propagated and status_j == "digested",
       f"异常冒泡={propagated}；item 状态={status_j}（mark 先于写盘）")

# ---- S10: 无 items 的 payload-only 兜底 + stop_after_completion 生效 ----
print("\n--- S10 无 items 的 payload-only 兜底 ---")
r = run("S10", [],
        {"user_request": "帮我消化这个", "title": "某文章", "event_kind": "user_shared",
         "stop_after_completion": True},
        adapter=FakeAdapter(chat_reply="已确认：本轮仅能确认分享线索，不能确认正文。"))
rep = CALLS["reports"][0] if CALLS["reports"] else {}
record("S10 payload-only 兜底：发送回执且 stop_after_completion 在此分支生效",
       rep.get("force_send") is True and rep.get("bypass_rate_limit") is True
       and len(CALLS["clear_active"]) == 1,
       f"report={rep.get('source')}；clear_active={CALLS['clear_active']}")

# ---- 5. 汇总 ----
print("\n" + "=" * 78)
print("汇总")
print("=" * 78)
confirmed = [(n, d) for n, ok, d in RESULTS if ok]
not_repro = [(n, d) for n, ok, d in RESULTS if not ok]
print(f"已确认结论: {len(confirmed)}")
for n, d in confirmed:
    print(f"  [已确认] {n}")
    if d:
        print(f"      证据: {d}")
print(f"\n未复现项: {len(not_repro)}")
for n, d in not_repro:
    print(f"  [未复现] {n}")
print("\n完成。")

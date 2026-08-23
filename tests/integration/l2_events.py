"""L2 事件级集成测试（P3）— 真实调用 harness/v2 事件验证契约。

运行: python3 tests/integration/l2_events.py
要求: 真实环境（api.json 已配 qwen 视觉、Edge 可用）
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(cond)


def section(title):
    print(f"\n===== {title} =====")


def main():
    import partner.core  # noqa: F401 先破 mind↔core 循环导入
    from partner.mind.harness import HarnessContext, default_registry

    tmp_ws = tempfile.mkdtemp(prefix="l2_ws_")
    workdir = os.path.join(tmp_ws, "tasks", "t1")
    os.makedirs(workdir, exist_ok=True)
    reg = default_registry()

    def mkctx(ws=tmp_ws, wd=workdir):
        return HarnessContext(
            workspace=ws, event=None, title="l2_test", project_dir=wd,
            state_md="", artifact_path="",
        )

    # ── 1. execute_code 真实运行 + run_log ──────────────────────────
    section("1. execute_code + run_log 契约")
    spec = reg.get("execute_code")
    check("execute_code 事件已注册", spec is not None)
    r = spec.handler(mkctx(), {"code": "print('L2-TEST', 1 + 1)"})
    check("运行成功", r.get("ok"), f"exit={r.get('exit_code')}")
    check("stdout 正确", "L2-TEST 2" in r.get("stdout", ""), r.get("stdout", "")[:60])
    check("script_path 存在", os.path.exists(r.get("script_path", "")), r.get("script_path", ""))
    # run_log 记录（tmp workspace）
    log_path = os.path.join(tmp_ws, "state", "logs", "code_runs.jsonl")
    check("code_runs.jsonl 已生成", os.path.exists(log_path), log_path)
    if os.path.exists(log_path):
        rows = [json.loads(l) for l in open(log_path, encoding="utf-8") if l.strip()]
        check("记录含 execute_code", rows and rows[0]["event"] == "execute_code")
        check("stdout_preview 正确", rows and "L2-TEST 2" in rows[0]["stdout_preview"])
        check("ok=true 记录", rows and rows[0]["ok"] is True)

    # ── 2. run_command 成功 + 失败 ──────────────────────────────────
    section("2. run_command 契约（成功/失败）")
    spec2 = reg.get("run_command")
    check("run_command 事件已注册", spec2 is not None)
    r = spec2.handler(mkctx(), {"command": "echo L2-OK"})
    check("成功命令 ok", r.get("ok"), f"exit={r.get('exit_code')}")
    check("stdout 含输出", "L2-OK" in r.get("stdout", ""))
    r = spec2.handler(mkctx(), {"command": "exit 3"})
    check("失败命令 ok=false", r.get("ok") is False, f"exit={r.get('exit_code')}")
    check("失败 exit_code=3", r.get("exit_code") == 3)
    rows = [json.loads(l) for l in open(log_path, encoding="utf-8") if l.strip()]
    check("run_command 已记录", any(x["event"] == "run_command" for x in rows))
    check("失败命令记录 ok=false", any(x["event"] == "run_command" and x["ok"] is False for x in rows))

    # ── 3. ensure_tool 三态 ─────────────────────────────────────────
    section("3. ensure_tool 契约（真实工具检测）")
    import asyncio as _asyncio
    from partner.v2.gap_events import atomic_ensure_tool
    from types import SimpleNamespace

    ctx = SimpleNamespace(workspace=tmp_ws)
    r = _asyncio.run(atomic_ensure_tool(ctx, {"tool": "iqtree"}))
    check("已装工具 → already_present", r.get("status") == "already_present", r.get("message", "")[:60])
    r = _asyncio.run(atomic_ensure_tool(ctx, {"tool": "prokka"}))
    check("未装工具 → manual_required", r.get("status") == "manual_required", r.get("message", "")[:60])
    r = _asyncio.run(atomic_ensure_tool(ctx, {"tool": "no_such_tool_xyz"}))
    check("未知工具 → unsupported", r.get("status") == "unsupported")
    r = _asyncio.run(atomic_ensure_tool(ctx, {}))
    check("缺参数 → missing_param", r.get("status") == "missing_param")

    # ── 4. read_image 真实读图（qwen3-vl-flash 真实 API）─────────────
    section("4. read_image 契约（真实 qwen API）")
    from partner.v2.vision_events import atomic_read_image

    test_img = os.path.join(workdir, "l2_test_image.png")
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (600, 200), "white")
    ImageDraw.Draw(img).text((30, 80), "L2 VISION TEST 456", fill="black")
    img.save(test_img)

    ctx4 = SimpleNamespace(workspace="/mnt/e/work/partner_workspace/instances/03",
                           working_dir=workdir)
    t0 = time.time()
    r = atomic_read_image(ctx4, {"path": test_img, "prompt": "图片里有什么文字？"})
    dt = time.time() - t0
    check("真实读图成功", r.get("ok"), f"{dt:.1f}s")
    check("描述含预期文字", "L2" in str(r.get("description", "")), str(r.get("description", ""))[:80])
    check("model 为 qwen VL", "qwen" in str(r.get("model", "")), r.get("model", ""))

    # 4b. 路径不存在 → 回退最近截图（workdir 有测试图）
    r = atomic_read_image(ctx4, {"path": os.path.join(workdir, "not_exist.png")})
    check("错误路径回退最近图", r.get("ok"), r.get("error", "")[:60])

    # 4c. 非图文件 → 错误
    bad = os.path.join(workdir, "note.md")
    open(bad, "w").write("# not an image")
    r = atomic_read_image(ctx4, {"path": bad})
    check("非图文件返回错误", r.get("ok") is False, r.get("error", "")[:60])

    # ── 5. web_capture 真实 Edge 截图 ───────────────────────────────
    section("5. web_capture 契约（真实 Edge headless）")
    spec5 = reg.get("web_capture")
    check("web_capture 事件已注册", spec5 is not None)
    out_jpg = os.path.join(workdir, "web_l2.jpg")
    t0 = time.time()
    r = spec5.handler(mkctx(), {"url": "https://example.com", "output": out_jpg})
    dt = time.time() - t0
    check("截图成功", r.get("ok"), f"{dt:.1f}s {r.get('error','')[:60]}")
    if os.path.exists(out_jpg):
        with open(out_jpg, "rb") as f:
            head = f.read(3)
        check("产物为有效 JPEG", head == b"\xff\xd8\xff", f"{os.path.getsize(out_jpg)}B")
        check("产物非空", os.path.getsize(out_jpg) > 2000)

    # ── 6. capability_inventory 真实盘点 ────────────────────────────
    section("6. capability_inventory 契约（真实盘点）")
    import asyncio

    from partner.v2.capability_events import atomic_capability_inventory

    async def run_inventory():
        ctx6 = SimpleNamespace(workspace="/mnt/e/work/partner_workspace/instances/03")
        save = os.path.join(tmp_ws, "capabilities_test.md")
        return await atomic_capability_inventory(ctx6, {"save_path": save})

    inv = asyncio.run(run_inventory())
    stats = inv.get("stats", {})
    check("agents ≥ 14（含新增生信 agent）", stats.get("agents", 0) >= 14, f"agents={stats.get('agents')}")
    check("gaps 为真实缺口（< 10）", stats.get("gaps", 99) < 10, f"gaps={stats.get('gaps')}")
    cap_path = os.path.join(tmp_ws, "capabilities_test.md")
    check("盘点文件生成", os.path.exists(cap_path) and os.path.getsize(cap_path) > 500)

    # ── 汇总 ────────────────────────────────────────────────────────
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n===== 汇总: {passed}/{total} 通过 =====")
    failed = [n for n, ok, _ in RESULTS if not ok]
    if failed:
        print("失败项:", failed)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()

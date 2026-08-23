"""L5 回归 + L6 稳定性（P6）— batch_plan 真实调用、write_design 计时、planner 过滤回归。

运行: python3 tests/integration/l6_stability.py
要求: 真实 api.json（deepseek key）+ 真实 workspace
"""
import json
import os
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(cond)


def section(title):
    print(f"\n===== {title} =====")


def main():
    import partner.core  # noqa: F401 破循环导入

    # ── 1. L5-回归: batch_plan 真实调用 3 次（JSON 完整 + 计时 <60s）────
    section("1. L5 回归: batch_plan 真实调用（deepseek-chat，3 次）")
    import asyncio

    from partner.adapters.direct_api import chat as direct_chat

    prompt = (
        "你是 Partner 的任务规划器。用户要求：写一个 Python 脚本计算 1 到 50 的平方和并运行验证。\n"
        "可用事件：create_file, run_command, execute_code, atomic_write_artifact, smart_llm_structured_action\n"
        "规划规则：\n"
        "- 输出必须是纯 JSON 数组，不要 markdown 代码块\n"
        "- 计划 JSON 总长不超过 3000 字符，每步 description 一句话\n"
        "- 方案必须建立在实际执行之上，包含运行验证步骤\n"
        "工作目录：/tmp\n"
        "输出长度约束：纯 JSON、≤3000 字符"
    )
    ok_count = 0
    for i in range(3):
        t0 = time.time()
        try:
            raw = direct_chat(prompt, purpose="batch_plan", timeout=90)
        except Exception as exc:
            raw = ""
            print(f"    第{i+1}次异常: {exc}")
        dt = time.time() - t0
        parsed = None
        if raw:
            text = raw.strip()
            if text.startswith("```"):
                import re
                text = re.sub(r"^```(?:json|plain|text)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()
            try:
                parsed = json.loads(text)
            except Exception as exc:
                print(f"    第{i+1}次 JSON 解析失败: {exc}")
        ok = parsed is not None and isinstance(parsed, list) and len(parsed) >= 2
        ok_count += int(ok)
        check(f"第 {i+1} 次调用", ok, f"{dt:.1f}s steps={len(parsed) if isinstance(parsed, list) else '?'}")
        check(f"第 {i+1} 次计时 <60s", dt < 60, f"{dt:.1f}s")
    check("3 次全部成功", ok_count == 3, f"{ok_count}/3")

    # ── 2. L5-回归: write_design 不卡死（计时 <120s）──────────────────
    section("2. L5 回归: write_design 真实调用（长生成不卡死）")
    from partner.v2.capability_events import atomic_write_design
    from types import SimpleNamespace

    tmp_design = os.path.join("/tmp", f"design_test_{int(time.time())}.md")

    class _TestAdapter:
        """轻量 adapter：chat 走真实 direct_api（长生成 purpose → deepseek-chat）。"""

        def chat(self, message, purpose="chat", **kw):
            from partner.adapters.direct_api import chat as _dc
            return _dc(str(message or ""), purpose=purpose, timeout=120)

    async def run_design():
        ctx = SimpleNamespace(workspace="/mnt/e/work/partner_workspace/instances/03",
                              working_dir="/tmp", project_dir="/tmp",
                              user_goal="测试写设计文档：分析示例函数的实现并设计改进方案", title="t",
                              adapter=_TestAdapter())
        return await atomic_write_design(ctx, {"goal": "测试写设计文档：分析示例函数的实现并设计改进方案",
                                               "save_path": tmp_design})

    t0 = time.time()
    r = asyncio.run(run_design())
    dt = time.time() - t0
    check("write_design 完成", r.get("ok") is True, f"{dt:.1f}s")
    check("计时 <120s（不卡死）", dt < 120, f"{dt:.1f}s")
    check("产物 design.md 生成", os.path.exists(tmp_design) and os.path.getsize(tmp_design) > 500,
          f"{os.path.getsize(tmp_design) if os.path.exists(tmp_design) else 0}B")

    # ── 3. L5-回归: planner 不选未安装 agent（cline 过滤）────────────
    section("3. L5 回归: planner agent 过滤（cline 不在列表）")
    from partner.planner.prompt_builder import build_available_agents_section

    agents_text = build_available_agents_section()
    check("cline 不在可用列表", "`cline`" not in agents_text, "")
    check("skyvern 不在可用列表", "`skyvern`" not in agents_text, "")
    check("新增生信 agent 在列表", any(a in agents_text for a in ("`plink`", "`enrichment`", "`bcftools`", "`diffexp`", "`iqtree`")),
          "plink/enrichment/bcftools/diffexp/iqtree")

    # ── 4. L6: run_command python 兼容回归（模拟实例 PATH）───────────
    section("4. L6 回归: run_command python→python3（模拟实例 PATH）")
    from partner.mind.harness import HarnessContext, default_registry

    reg = default_registry()
    spec = reg.get("run_command")
    ctx = HarnessContext(workspace="/tmp", event=None, title="t", project_dir="/tmp",
                         state_md="", artifact_path="")
    import shutil
    if not shutil.which("python"):
        r = spec.handler(ctx, {"command": "python -c 'print(41+1)'"})
        check("python 命令成功（替换为 python3）", r.get("ok"), f"exit={r.get('exit_code')} stdout={r.get('stdout','')[:40]}")
        check("stdout 正确", "42" in r.get("stdout", ""))
    else:
        # 本环境有 python 则直接验证 python3 命令
        r = spec.handler(ctx, {"command": "python3 -c 'print(41+1)'"})
        check("python3 命令成功", r.get("ok") and "42" in r.get("stdout", ""))

    # ── 5. L6: 5 实例并发运行状态检查（P5 已并发注入；此处验证互不干扰）──
    section("5. L6: 5 实例运行状态与隔离")
    import glob

    inst_dirs = sorted(glob.glob("/mnt/e/work/partner_workspace/instances/0*"))
    check("5 个实例目录存在", len(inst_dirs) == 5, f"{len(inst_dirs)}")
    active = 0
    for d in inst_dirs:
        ap = os.path.join(d, "state", "active_plan.json")
        if os.path.exists(ap):
            active += 1
    check("active_plan 独立存在", active >= 1, f"{active}/5 有活动计划（各自隔离）")
    inboxes = [os.path.exists(os.path.join(d, "state", "desktop_inbox.jsonl")) for d in inst_dirs]
    check("各实例独立 inbox", all(inboxes))

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

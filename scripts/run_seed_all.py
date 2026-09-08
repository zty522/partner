#!/usr/bin/env python3
"""Send one fresh, auditable project instruction to each of the five instances.

Each invocation gets a run id, so it is usable as a real acceptance probe and
does not silently disappear behind a same-day id.  After emission the script
exits; the resource-adaptive native runtime decides when each lane runs.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s seed_all %(levelname)s %(message)s")
logger = logging.getLogger("seed_all")

sys.path.insert(0, "/mnt/e/work/partner")
WORKSPACE = Path(os.environ.get("PARTNER_WORKSPACE", "/mnt/e/work/partner_workspace"))

ROLES = {
    "01": {
        "project_id": "xiaohongshu_operations",
        "instructions": (
            "01实例原生项目续跑_项目_小红书账户推送与维护。\n"
            "目标：承接最近 Receipt 执行下一项内容证据、风险队列、编辑队列或来源核验 Event；"
            "只报告真实输入记录、来源、风险与可发布状态，不编造草稿或发布授权。"
        ),
    },
    "02": {
        "project_id": "molecular_generation",
        "instructions": (
            "02实例原生项目续跑_分子生成主动实验。真实承接最近 Receipt，执行下一项尚未完成的"
            "官方 split、候选生成或误差切片 Event；必须写出新实验参数、真实数值结果、与上一轮差异及下一步。"
        ),
    },
    "03": {
        "project_id": "molecular_dynamics_study",
        "instructions": (
            "03实例原生项目续跑_分子动力学学习与实践。真实运行一个与最近 Receipt 参数不同的数值实验，"
            "报告积分器、步长/温度、稳定性指标、产物路径和基于结果选择的下一项实验。"
        ),
    },
    "04": {
        "project_id": "literature_github_learning",
        "instructions": (
            "04实例原生项目续跑_项目_文献与代码学习。\n"
            "目标：用外部知识主动学习 Event 检索一个新的 GitHub Agent/Harness 仓库和一篇相关论文，"
            "由 LLM 提出知识缺口、选择高信息价值来源、基于真实 README/摘要或 PDF 提出可证伪想法；"
            "把仓库、论文与札记保存到 workspace/external，并且不把元数据或既有报告冒充完成阅读。"
        ),
    },
    "05": {
        "project_id": "hermes_partner_explore",
        "instructions": (
            "05实例原生项目续跑_Partner 架构主动学习与自进化。承接最近 Receipt，选择当前信息价值最高的"
            "契约盘点、失败回归、缺口整理或受限改进 Event；若真实机制和证据足够，再形成会改变可观察行为的"
            "候选改进，完成隔离对照、回归与可逆实施。不得每轮强行造新方案，也不得用注释改动冒充进化。"
        ),
    },
}


def _bucket() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _build_request(role: str, info: dict) -> str:
    text = info["instructions"].replace("<UTC日期>", _bucket())
    return (
        f"【{role}实例自驱】项目：{info['project_id']}\n\n"
        f"目标：{text}\n\n"
        f"actions_executed 列动作；findings 三要素。\n"
        f"[instance_native=true] [native_kind=project] "
        f"[project_id={info['project_id']}]\n"
        f"[self_drive=true] [seed_all=true] "
        f"[emitted_at={datetime.now(timezone.utc).astimezone().isoformat()}]"
    )


def _write(role: str, mid: str, text: str, project_id: str) -> bool:
    inbox = WORKSPACE / "instances" / role / "state" / "desktop_inbox.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "id": mid, "message_id": mid, "role": "user",
        "text": text, "content": text,
        "source": "instance_native", "channel": "local",
        "kind": "project", "project_id": project_id,
        "sender_id": f"partner_{role}_seed_all",
        "sender_name": f"Partner{role}项目自驱",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "self_drive": True, "seed_all": True,
    }
    with inbox.open("a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    parser.add_argument(
        "--reopen-blocked", action="store_true",
        help="explicit acceptance run: preserve failure evidence but grant a fresh bounded attempt",
    )
    parser.add_argument("--instances", nargs="*", choices=sorted(ROLES),
                        help="optional subset; default sends to all five")
    args = parser.parse_args()
    bucket = _bucket()
    selected_roles = args.instances or list(ROLES)
    for role in selected_roles:
        info = ROLES[role]
        mid = f"seed_all_{role}_{args.run_id}_" + hashlib.sha1(
            (role + args.run_id).encode()).hexdigest()[:10]
        text = _build_request(role, info)
        if args.reopen_blocked:
            from partner.governance.instance_native import load_state, save_state
            state = load_state(WORKSPACE, role)
            if state.phase == "BLOCKED":
                state.phase = "WAITING"
                state.pending_message_id = ""
                state.pending_kind = ""
                state.consecutive_failures = 0
                state.learning_interruptions = 0
                state.reason = f"explicit acceptance run {args.run_id}; prior failure retained"
                save_state(WORKSPACE, state)
        if _write(role, mid, text, info["project_id"]):
            logger.info("seeded task for %s -> %s (mid=%s)",
                        role, info["project_id"], mid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

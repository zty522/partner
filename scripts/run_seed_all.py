#!/usr/bin/env python3
"""Submit one fresh project Job to every selected instance.

This is a one-shot acceptance helper, not a Campaign or periodic scheduler.
Every request enters the same Application -> Event Flow boundary as GUI/TUI/QQ;
the resource-adaptive runtime independently decides how many Jobs may run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s seed_all %(levelname)s %(message)s")
logger = logging.getLogger("seed_all")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from partner.application import PartnerApplicationService

WORKSPACE = Path(os.environ.get("PARTNER_WORKSPACE", "/mnt/e/work/partner_workspace"))
ROLES = {
    "01": ("xiaohongshu_operations", "继续小红书运营项目。承接最近真实进展，完成一项不需要发布授权的高价值工作，并说明新发现、证据和下一步。"),
    "02": ("molecular_generation", "继续分子生成方法创新项目。选择一个能减少关键不确定性的新实验，真实运行并比较数值结果。"),
    "03": ("molecular_dynamics_study", "继续分子动力学项目。选择一个与上轮参数不同且有信息价值的实验，真实运行并分析稳定性。"),
    "04": ("literature_github_learning", "继续前沿代码与文献主动学习。找出影响项目决策的知识缺口，检索、拉取、阅读并保存高价值原始资料和札记。"),
    "05": ("hermes_partner_explore", "继续 Partner 自进化项目。检查近期真实运行证据，诊断最影响推进的机制问题；证据充分时隔离验证可回滚 Candidate。"),
}


def _real_qq_sender(instance_id: str) -> str:
    """Return only a recipient observed on the real QQ inbound channel."""
    history = WORKSPACE / "instances" / instance_id / "state/qq_chat_history.jsonl"
    sender = ""
    try:
        lines = history.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        candidate = str(row.get("sender_id") or "").strip()
        if (row.get("role") == "user" and row.get("source") == "qq"
                and candidate and not candidate.startswith("partner_")):
            sender = candidate
    return sender


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="")  # retained for operator audit labels
    parser.add_argument("--reopen-blocked", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--instances", nargs="*", choices=sorted(ROLES),
                        help="optional subset; default submits to all five")
    args = parser.parse_args()
    selected = args.instances or list(ROLES)
    service = PartnerApplicationService(WORKSPACE)
    failed = False
    for instance_id in selected:
        project_id, request = ROLES[instance_id]
        sender = _real_qq_sender(instance_id)
        channel = "qq" if sender else "local"
        if not sender:
            sender = f"partner_{instance_id}_local_acceptance"
            logger.warning("%s has no real QQ inbound recipient; result stays local", instance_id)
        result = service.submit(
            request, channel=channel, sender_id=sender, sender_name="ZLL",
            persona_hint=instance_id, project_id=project_id, report_policy="milestone",
        )
        recipient_fp = hashlib.sha256(sender.encode()).hexdigest()[:8]
        logger.info("%s accepted=%s job=%s channel=%s recipient=%s",
                    instance_id, result.accepted, result.job_id, channel, recipient_fp)
        failed = failed or not result.accepted
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

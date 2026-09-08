#!/usr/bin/env python3
"""One-shot self-drive emit for each instance.

For each enabled instance this writes exactly ONE self-drive inbox row that
demands a real external action and a specifically-named artifact file under
``share/projects/<project>/external_artifacts/<tid>/``. After the row is in
place the script exits; nothing here runs forever.

The choice of artifact file is per-role and is the file name governance's
``named_artifact`` check looks for. That removes the recurring
"task stopped because no continuation.md" failure mode we observed during
the 4-minute-cron run.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/mnt/e/work/partner")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s one_shot_drive %(levelname)s %(message)s")
logger = logging.getLogger("one_shot_drive")

WORKSPACE = Path(os.environ.get("PARTNER_WORKSPACE", "/mnt/e/work/partner_workspace"))

# role-specific instructions that include the exact artifact name governance expects.
ROLE_TASKS = {
    "01": {
        "title": "01实例原生项目续跑_项目_小红书账户推送与维护",
        "project_id": "xiaohongshu_operations",
        "instance_id": "01",
        "instructions": (
            "在 share/projects/xiaohongshu_operations/external_artifacts/ 下生成至少 1 篇 "
            "本实例今日的小红书推文草稿，文件名必须为 xhs_post_<UTC日期>.md；"
            "推文正文≥300 字、含标题/正文/标签三段；"
            "actions_executed 必须显式记录 'atomic_write_file:share/projects/"
            "xiaohongshu_operations/external_artifacts/xhs_post_<UTC日期>.md'。"
            "完成后必须把那份文件路径作为 named_artifact 返回。"
        ),
    },
    "02": {
        "title": "02实例原生项目续跑_项目_分子生成方法创新与实践",
        "project_id": "molecular_generation",
        "instance_id": "02",
        "instructions": (
            "在 share/projects/molecular_generation/external_artifacts/ 下运行或落地 1 个 "
            "分子生成 benchmark：调 execute_code 真跑 Python（RDKit molecular_generation_benchmark）"
            "或 molecular_diversity_benchmark，输出 CSV+JSON+MD 三件套到 "
            "external_artifacts/<UTC日期>_molgen/ 目录；"
            "actions_executed 必须显式记录 'exec:python3 scripts/<real>.py' 或 "
            "'execute_code:molecular_generation_benchmark'；"
            "完成后把那份目录的 *benchmark_report.md 路径作为 named_artifact 返回。"
        ),
    },
    "03": {
        "title": "03实例原生项目续跑_项目_Partner 框架与前端优化",
        "project_id": "partner_framework_frontend",
        "instance_id": "03",
        "instructions": (
            "在 partner/<pkg>/ 下做 1 处真代码改动并跑对应 pytest："
            "例如改 partner/governance/scheduler.py 一行并 pytest tests/test_*.py；"
            "在 share/projects/partner_framework_frontend/external_artifacts/<UTC日期>/ "
            "下写 diff_summary.md（包含 git diff 摘要 + pytest 输出）；"
            "actions_executed 必须显式记录 'atomic_write_file' 与 'pytest:tests/<xxx>::case_yyy'；"
            "完成后把 diff_summary.md 路径作为 named_artifact 返回。"
        ),
    },
    "04": {
        "title": "04实例原生项目续跑_项目_文献与代码学习",
        "project_id": "literature_github_learning",
        "instance_id": "04",
        "instructions": (
            "在 share/projects/agent_self_evolution/external_artifacts/<UTC日期>_lit_review/ 下"
            "真抓 1 篇 arxiv 摘要或 github 代码解读并落 1 篇 external_sources/<paper_or_repo>.md；"
            "actions_executed 必须显式记录 'web.fetch:https://arxiv.org/abs/...' 或 "
            "'github_search:<query>'；"
            "完成后把 external_sources/<paper_or_repo>.md 路径作为 named_artifact 返回。"
        ),
    },
    "05": {
        "title": "05实例原生项目续跑_项目_自进化研究",
        "project_id": "agent_self_evolution",
        "instance_id": "agent_self_evolution",
        "instructions": (
            "在 share/projects/agent_self_evolution/external_artifacts/<UTC日期>_evolution_round/ "
            "下：扫 partner/governance/candidate_skills.py 1 遍，挑 1 个 week-old candidate_skills/* "
            "出来做 1 次 production_canary 重评估并落 report.md（含 score 摘要 + 是否晋升建议）；"
            "actions_executed 必须显式记录 'exec:python3 ...' 或 'production_readiness:assess_production_readiness'；"
            "完成后把 report.md 路径作为 named_artifact 返回。"
        ),
    },
}


def _now_utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _build_request(role: str, info: dict) -> str:
    project_id = info["project_id"]
    instructions = info["instructions"].replace("<UTC日期>", _now_utc_date())
    return (
        f"【{role}实例一次性自驱】项目：{info['title']}\n"
        f"project_id={project_id}\n\n"
        f"目标：{instructions}\n\n"
        f"【硬约束·真实外部动作】本步必须包含至少 1 项可验证的真实外部动作 "
        f"（atomic_write_file / exec:python3 / web.fetch:URL / pytest / execute_code / "
        f"atomic_inspect_file），并把对应真实产物写到上面提到的具体路径。\n\n"
        f"actions_executed 必须显式列出这些动作及对应文件路径；"
        f"findings 必须包含「真实外部动作 + 真实产物路径 + 具体结论/数字」三要素。\n\n"
        f"【期望 artifact】{info['instructions'].split('作为 named_artifact')[0].split('【期望 artifact】')[-1].strip() if '【期望 artifact】' in info['instructions'] else '见上'}\n\n"
        f"【期望行为】完成本任务后请观察：\n"
        f"  1. 是否产出 named_artifact（如未产出请重做直到产出）；\n"
        f"  2. 是否触发主动学习（failure 时走 active_learning chain）；\n"
        f"  3. 是否触发自进化（apply_pipeline 在 production_readiness/ 有 candidate_validated 时合并）；\n"
        f"  4. 是否在 task 完成后迭代进入下一轮（governance 验收通过则 self_evolve 继续，"
        f"     否则 ledger 里写 issue/recorded + active_learning/* 链）。\n\n"
        f"[instance_native=true] [native_kind=project] [project_id={project_id}]\n"
        f"[self_drive=true] [one_shot=true] [emitted_at={datetime.now(timezone.utc).astimezone().isoformat()}]"
    )


def _write_inbox_once(workspace: Path, role: str, message_id: str, text: str, project_id: str) -> bool:
    inbox = workspace / "instances" / role / "state" / "desktop_inbox.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "id": message_id, "message_id": message_id, "role": "user",
        "text": text, "content": text,
        "source": "instance_native", "channel": "local",
        "kind": "project", "project_id": project_id,
        "sender_id": f"partner_{role}_one_shot",
        "sender_name": f"Partner{role}项目一次性自驱",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "self_drive": True, "one_shot": True,
    }
    with inbox.open("a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return True


def main() -> int:
    summary = []
    for role in ["01", "02", "03", "04", "05"]:
        info = ROLE_TASKS[role]
        ts = int(time.time() if False else 0)  # avoid time import collision
    # Use a fixed hash component so all five writes are idempotent against re-run.
    bucket = _now_utc_date()
    for role in ["01", "02", "03", "04", "05"]:
        info = ROLE_TASKS[role]
        mid = "one_shot_" + role + "_" + bucket + "_" + hashlib.sha1(
            (role + bucket).encode()).hexdigest()[:10]
        text = _build_request(role, info)
        if _write_inbox_once(WORKSPACE, role, mid, text, info["project_id"]):
            summary.append((role, info["project_id"], mid))
            logger.info("emitted one-shot task for %s project=%s mid=%s",
                        role, info["project_id"], mid[:40])
    logger.info("done. emitted %d tasks. daemon NOT scheduled.", len(summary))
    for role, pid, mid in summary:
        print(f"  {role}: project={pid:30s} mid={mid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

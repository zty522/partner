"""Partner identity registry (instance-concept convergence, 2026-09-17).

``01``--``05`` are NOT fixed capability executors.  They are independent
Partner identities (compat IDs) that each carry their own memory,
preferences, habits, growth, permissions and default config.  A readable
name is a human-facing alias; the compat ID stays the canonical identity
key so existing state (jobs.db, instances/, bot bindings) keeps working.

A Partner's ``expertise`` is a *default suggestion* only.  It may steer
the default project or a routing hint, but it must NEVER overwrite an
explicitly specified Partner for a task.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Partner:
    partner_id: str          # canonical compat ID: "01".."05"
    name: str                # readable alias
    expertise: str           # default specialty suggestion (not a hard type)
    default_project_id: str  # suggested default project (not a hard binding)


PARTNERS: dict[str, Partner] = {
    "01": Partner("01", "小红书运营伙伴", "内容运营与账号维护", "xiaohongshu_operations"),
    "02": Partner("02", "分子生成伙伴", "分子生成方法与实验", "molecular_generation"),
    "03": Partner("03", "分子动力学伙伴", "分子动力学模拟学习", "molecular_dynamics_study"),
    "04": Partner("04", "文献学习伙伴", "文献与外部代码学习", "literature_github_learning"),
    "05": Partner("05", "代码探索伙伴", "框架与工具代码探索", "partner_explore"),
}

_COMPAT_ID_RE = re.compile(r"^0[1-5]$")


def is_partner_id(value: str) -> bool:
    return bool(value) and _COMPAT_ID_RE.match(value)


def resolve_partner(ref: str) -> str:
    """Resolve a Partner reference (compat ID or readable name) to a canonical ID.

    Returns the canonical ``partner_id``.  Raises ``ValueError`` for unknown
    references.  This is the single source of truth for identity parsing —
    callers must NOT invent a parallel mapping.
    """
    ref = str(ref or "").strip()
    if _COMPAT_ID_RE.match(ref):
        return ref
    for pid, partner in PARTNERS.items():
        if ref == partner.name:
            return pid
    raise ValueError(f"unknown Partner reference: {ref!r}")


def partner_name(partner_id: str) -> str:
    p = PARTNERS.get(partner_id)
    return p.name if p else partner_id


def default_project_for(partner_id: str) -> str:
    """Suggested default project for a Partner — a hint, never a hard binding."""
    p = PARTNERS.get(partner_id)
    return p.default_project_id if p else ""


__all__ = ["Partner", "PARTNERS", "is_partner_id", "resolve_partner",
           "partner_name", "default_project_for"]

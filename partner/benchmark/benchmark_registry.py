"""Benchmark 注册表。

管理所有可用的 benchmark 协议，提供按名称查找、列出和查询功能。
"""

from __future__ import annotations

import logging
from typing import Any

from .protocols.base import BenchmarkProtocol
from .protocols.literature_review import LITERATURE_REVIEW_PROTOCOL_V1
from .protocols.data_analysis import DATA_ANALYSIS_PROTOCOL_V1

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]


class BenchmarkRegistry:
    """单例 benchmark 注册表。"""

    _instance: BenchmarkRegistry | None = None
    _protocols: dict[str, BenchmarkProtocol] = {}

    def __new__(cls) -> BenchmarkRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._protocols = {}
        return cls._instance

    def register(self, protocol: BenchmarkProtocol) -> None:
        """注册一个 benchmark protocol。"""
        bid = protocol.benchmark_id
        if bid in self._protocols:
            logger.warning("[BENCHMARK] 覆盖已注册的 benchmark: %s", bid)
        self._protocols[bid] = protocol
        logger.info("[BENCHMARK] 已注册 benchmark: %s v%s (%d tasks)",
                     bid, protocol.version, protocol.task_count())

    def get(self, benchmark_id: str) -> BenchmarkProtocol | None:
        """按 ID 获取 benchmark protocol。"""
        return self._protocols.get(benchmark_id)

    def list_suites(self) -> list[dict[str, Any]]:
        """列出所有注册的 benchmark 套件概要。"""
        return [
            {
                "benchmark_id": p.benchmark_id,
                "display_name": p.display_name,
                "version": p.version,
                "task_count": p.task_count(),
                "description": p.description[:120] + "…" if len(p.description) > 120 else p.description,
                "tags": p.metadata.get("tags", []),
                "difficulty_range": p.metadata.get("difficulty_range", []),
            }
            for p in sorted(self._protocols.values(), key=lambda x: x.benchmark_id)
        ]

    def list_by_tag(self, tag: str) -> list[BenchmarkProtocol]:
        """按标签筛选。"""
        return [
            p for p in self._protocols.values()
            if tag in p.metadata.get("tags", [])
        ]

    def get_all(self) -> list[BenchmarkProtocol]:
        return list(self._protocols.values())

    def task_count_total(self) -> int:
        return sum(p.task_count() for p in self._protocols.values())

    def __len__(self) -> int:
        return len(self._protocols)


# ── 自动注册已知 benchmark ─────────────────────────────────────────────

_BUILTIN_BENCHMARKS = [
    LITERATURE_REVIEW_PROTOCOL_V1,
    DATA_ANALYSIS_PROTOCOL_V1,
]


def _init_registry() -> None:
    """初始化注册表并注册内建 benchmark。"""
    registry = BenchmarkRegistry()
    if not registry._protocols:
        for protocol in _BUILTIN_BENCHMARKS:
            registry.register(protocol)


# 模块级便利函数
def get_registry() -> BenchmarkRegistry:
    _init_registry()
    return BenchmarkRegistry()


def list_benchmark_suites() -> list[dict[str, Any]]:
    return get_registry().list_suites()

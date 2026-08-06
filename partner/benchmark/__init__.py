"""Partner Benchmark — 科研评估基准框架。

NatureBench 和自定义 benchmark 的对接层，复用 Partner Harness 执行引擎
对 Agent 的科研能力进行可复现的量化评估。
"""

from .benchmark_registry import (
    BenchmarkRegistry,
    get_registry,
    list_benchmark_suites,
)
from .benchmark_scorer import (
    BenchScore,
    BenchScoreSet,
    score_benchmark_run,
    format_score_report,
)
from .naturebench_runner import (
    BenchmarkRun,
    BenchmarkResult,
    NatureBenchRunner,
    run_benchmark,
)
from .export import (
    export_event_trace,
    export_benchmark_results,
    NATUREBENCH_EXPORT_VERSION,
)
from .learning_integration import (
    record_benchmark_to_learning,
    make_learning_callback,
)

__all__ = [
    "BenchmarkRegistry",
    "BenchmarkRun",
    "BenchmarkResult",
    "BenchScore",
    "BenchScoreSet",
    "NatureBenchRunner",
    "get_registry",
    "list_benchmark_suites",
    "score_benchmark_run",
    "format_score_report",
    "run_benchmark",
    "export_event_trace",
    "export_benchmark_results",
    "NATUREBENCH_EXPORT_VERSION",
    "record_benchmark_to_learning",
    "make_learning_callback",
]

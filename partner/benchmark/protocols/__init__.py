"""Benchmark Protocol 集合。"""

from .base import (
    BenchmarkProtocol,
    BenchmarkTask,
    JsonDict,
    SCORE_METHOD_EXACT_MATCH,
    SCORE_METHOD_FUZZY_MATCH,
    SCORE_METHOD_RUBRIC,
    SCORE_METHOD_LLM_JUDGE,
    SCORE_METHOD_CUSTOM,
    VALID_SCORE_METHODS,
)

__all__ = [
    "BenchmarkProtocol",
    "BenchmarkTask",
    "JsonDict",
    "SCORE_METHOD_EXACT_MATCH",
    "SCORE_METHOD_FUZZY_MATCH",
    "SCORE_METHOD_RUBRIC",
    "SCORE_METHOD_LLM_JUDGE",
    "SCORE_METHOD_CUSTOM",
    "VALID_SCORE_METHODS",
]

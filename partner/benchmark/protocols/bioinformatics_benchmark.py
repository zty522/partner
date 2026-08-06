"""Bioinformatics 工具 Benchmark 协议。

测试 Partner 调用生信 CLI 工具的能力：
1. samtools — BAM 统计
2. seqkit — 序列统计 
3. fastqc — 质控报告
4. muscle — 多序列比对
5. bwa — 序列比对
"""
from __future__ import annotations

import logging
import os

from .base import BenchmarkProtocol, BenchmarkTask

logger = logging.getLogger(__name__)

BENCHMARK_ID = "partner/bioinformatics_v1"
VERSION = "1.0.0"

# ── 任务 1: samtools stats ──

task_samtools = BenchmarkTask(
    task_id="bio_samtools_stats",
    title="对 BAM 文件生成比对统计信息",
    description="使用 samtools stats 和 samtools flagstat 分析 BAM 文件的比对统计信息，"
                "包括总 reads 数、比对率、配对情况等指标。",
    input_data={
        "query": "对 /data/test.bam 生成 samtools 比对统计报告",
        "tool": "samtools",
        "command": "samtools stats /data/test.bam",
    },
    expected_output={
        "format": "text",
        "key_points": [
            "总 reads 数",
            "比对率",
            "配对 reads 数",
            "高质量比对",
            "GC 含量",
        ],
    },
    scoring_rules={
        "method": "llm_judge",
        "passing_threshold": 0.6,
        "dimensions": {
            "completeness": 0.4,
            "accuracy": 0.3,
            "presentation": 0.3,
        },
    },
)

# ── 任务 2: seqkit stats ──

task_seqkit = BenchmarkTask(
    task_id="bio_seqkit_stats",
    title="对 FASTA 文件生成序列统计",
    description="使用 seqkit stats 分析 FASTA 文件的基本统计信息，"
                "包括序列数、总长度、最短/最长序列、N50 等。",
    input_data={
        "query": "对 /data/test.fasta 生成 seqkit 序列统计报告",
        "tool": "seqkit",
        "command": "seqkit stats /data/test.fasta",
    },
    expected_output={
        "format": "text",
        "key_points": [
            "序列总数",
            "总长度",
            "最短序列长度",
            "最长序列长度",
            "N50 值",
            "GC 含量",
        ],
    },
    scoring_rules={
        "method": "llm_judge",
        "passing_threshold": 0.6,
        "dimensions": {
            "completeness": 0.4,
            "accuracy": 0.3,
            "presentation": 0.3,
        },
    },
)

# ── 任务 3: fastqc ──

task_fastqc = BenchmarkTask(
    task_id="bio_fastqc",
    title="对 FASTQ 文件进行质量控制和报告",
    description="使用 fastqc 对测序数据进行质量控制分析，生成质量报告。"
                "需要调用 fastqc 命令并解读结果。",
    input_data={
        "query": "对 /data/test.fastq 运行 FastQC 质量检查并总结结果",
        "tool": "fastqc",
        "command": "fastqc /data/test.fastq",
    },
    expected_output={
        "format": "text",
        "key_points": [
            "碱基质量分数",
            "GC 含量分布",
            "序列重复水平",
            "接头污染检测",
            "总体质量评估",
        ],
    },
    scoring_rules={
        "method": "llm_judge",
        "passing_threshold": 0.6,
        "dimensions": {
            "completeness": 0.4,
            "accuracy": 0.3,
            "presentation": 0.3,
        },
    },
)

# ── 任务 4: muscle 多序列比对 ──

task_muscle = BenchmarkTask(
    task_id="bio_muscle_alignment",
    title="多序列比对",
    description="使用 muscle 或 mafft 对给定序列进行多序列比对，"
                "输出比对结果并总结保守区域。",
    input_data={
        "query": "对输入序列进行多序列比对，使用 muscle 或 mafft",
        "tool": "muscle",
        "command": "muscle -align /data/input.fasta -output /data/aligned.fasta",
    },
    expected_output={
        "format": "text",
        "key_points": [
            "比对的物种/序列数",
            "比对方法",
            "保守区域识别",
            "序列一致性统计",
        ],
    },
    scoring_rules={
        "method": "llm_judge",
        "passing_threshold": 0.6,
        "dimensions": {
            "completeness": 0.4,
            "accuracy": 0.3,
            "presentation": 0.3,
        },
    },
)

# ── 任务 5: bwa 序列比对 ──

task_bwa = BenchmarkTask(
    task_id="bio_bwa_alignment",
    title="短序列比对到参考基因组",
    description="使用 bwa mem 将 FASTQ 序列比对到参考基因组，生成 SAM/BAM 文件。",
    input_data={
        "query": "将 FASTQ 序列比对到参考基因组，使用 bwa mem",
        "tool": "bwa",
        "command": "bwa mem -t 4 /data/reference.fasta /data/reads.fastq -o /data/output.sam",
    },
    expected_output={
        "format": "text",
        "key_points": [
            "比对工具和版本",
            "比对参数",
            "比对率统计",
            "输出文件格式",
        ],
    },
    scoring_rules={
        "method": "llm_judge",
        "passing_threshold": 0.6,
        "dimensions": {
            "completeness": 0.4,
            "accuracy": 0.3,
            "presentation": 0.3,
        },
    },
)

# ── 组装 Protocol ──

bioinformatics_protocol = BenchmarkProtocol(
    benchmark_id=BENCHMARK_ID,
    display_name="生物信息学 CLI 工具 Benchmark v1",
    version=VERSION,
    description="Bioinformatics CLI 工具调用能力评估",
    tasks=[
        task_samtools,
        task_seqkit,
        task_fastqc,
        task_muscle,
        task_bwa,
    ],
    metadata={
        "category": "bioinformatics",
        "tools": ["samtools", "seqkit", "fastqc", "muscle", "bwa"],
        "required_runtime": "cli_tools",
    },
)

# Auto-register (lazy, to avoid circular import)
def register_protocol():
    try:
        from ..benchmark_registry import get_registry
        get_registry().register(bioinformatics_protocol)
        logger.info("[BENCHMARK] registered bioinformatics protocol: %s v%s", BENCHMARK_ID, VERSION)
        return True
    except Exception as exc:
        logger.warning("[BENCHMARK] failed to register bioinformatics protocol: %s", exc)
        return False

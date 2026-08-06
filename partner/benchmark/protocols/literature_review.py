"""文献综述 benchmark 原型。

评估 Agent 的文献调研、信息综合和结构化摘要能力。
每个任务要求根据特定主题进行文献检索，输出结构化综述。
"""

from __future__ import annotations

from .base import BenchmarkProtocol, BenchmarkTask, SCORE_METHOD_RUBRIC


# ── 任务定义 ───────────────────────────────────────────────────────────

_LITERATURE_REVIEW_TASKS = [
    BenchmarkTask(
        task_id="lit_review_baseline_001",
        title="单细胞测序技术进展综述",
        description=(
            "请调研单细胞 RNA 测序（scRNA-seq）技术的最新进展（2023-2025），"
            "输出一份结构化综述。需覆盖以下技术路线：\n"
            "1. 微流控平台（10x Genomics, Drop-seq 等）\n"
            "2. 基于组合条码的方案（Split-seq, sci-RNA-seq 等）\n"
            "3. 空间转录组联合分析\n"
            "4. 多组学单细胞联合检测（CITE-seq, DOGMA-seq 等）"
        ),
        input_data={
            "query": "单细胞 RNA 测序技术进展 2023-2025",
            "context": "",
            "parameters": {
                "output_format": "markdown",
                "max_length": 3000,
                "min_citations": 8,
                "language": "zh",
            },
        },
        expected_output={
            "format": "markdown",
            "key_points": [
                "各技术路线原理简述",
                "性能对比（通量、灵敏度、成本）",
                "代表性应用案例",
                "技术局限性与未来方向",
            ],
            "constraints": {
                "min_citations": 8,
                "min_sections": 4,
                "has_comparison_table": True,
            },
        },
        scoring_rules={
            "method": SCORE_METHOD_RUBRIC,
            "weight": 1.0,
            "passing_threshold": 0.6,
            "rubric": [
                {
                    "item": "覆盖全面性",
                    "max_score": 5,
                    "criteria": "是否覆盖 ≥4 条指定的技术路线，每条有实质内容",
                },
                {
                    "item": "引用质量",
                    "max_score": 5,
                    "criteria": "引用 ≥8 篇相关文献，引用格式规范，文献真实存在",
                },
                {
                    "item": "结构清晰度",
                    "max_score": 3,
                    "criteria": "有标题层级、段落分明、逻辑连贯",
                },
                {
                    "item": "对比分析",
                    "max_score": 4,
                    "criteria": "包含技术性能对比（表格优先），分析有深度",
                },
                {
                    "item": "批判性思考",
                    "max_score": 3,
                    "criteria": "指出各技术的局限性和未来发展方向",
                },
            ],
        },
        metadata={"difficulty": "medium", "estimated_time_min": 15, "tags": ["literature", "single-cell", "sequencing"]},
    ),
    BenchmarkTask(
        task_id="lit_review_baseline_002",
        title="深度学习在药物发现中的应用",
        description=(
            "请调研深度学习在药物发现领域的最新应用（2023-2025），"
            "输出结构化综述。重点覆盖：\n"
            "1. 分子生成模型（VAE, GAN, Diffusion）\n"
            "2. 蛋白质-配体相互作用预测\n"
            "3. 临床前 ADMET 预测\n"
            "4. 大语言模型在药物设计中的应用"
        ),
        input_data={
            "query": "deep learning drug discovery 2023 2024 2025 review",
            "context": "",
            "parameters": {
                "output_format": "markdown",
                "max_length": 3000,
                "min_citations": 10,
                "language": "en",
            },
        },
        expected_output={
            "format": "markdown",
            "key_points": [
                "各深度学习技术的核心创新点",
                "代表性模型与公开基准结果",
                "现有方法的局限性",
                "未来有前景的研究方向",
            ],
            "constraints": {
                "min_citations": 10,
                "min_sections": 4,
                "has_comparison_table": True,
            },
        },
        scoring_rules={
            "method": SCORE_METHOD_RUBRIC,
            "weight": 1.0,
            "passing_threshold": 0.6,
            "rubric": [
                {
                    "item": "覆盖全面性",
                    "max_score": 5,
                    "criteria": "覆盖全部 4 个指定主题，每个有实质性讨论",
                },
                {
                    "item": "引用质量",
                    "max_score": 5,
                    "criteria": "引用 ≥10 篇文献，包含高影响力期刊/会议论文",
                },
                {
                    "item": "技术准确度",
                    "max_score": 4,
                    "criteria": "对方法原理的描述准确，无技术性错误",
                },
                {
                    "item": "批判性分析",
                    "max_score": 4,
                    "criteria": "包含对比、局限性和未来方向，而非简单罗列",
                },
                {
                    "item": "格式与可读性",
                    "max_score": 2,
                    "criteria": "Markdown 格式正确，段落清晰，阅读体验好",
                },
            ],
        },
        metadata={"difficulty": "medium", "estimated_time_min": 15, "tags": ["literature", "drug-discovery", "deep-learning"]},
    ),
    BenchmarkTask(
        task_id="lit_review_baseline_003",
        title="空间转录组数据分析方法综述",
        description=(
            "请调研空间转录组数据（10x Visium, MERFISH, Slide-seq 等）"
            "的分析方法和技术挑战（2022-2025），输出综述。\n"
            "需覆盖：\n"
            "1. 数据预处理与质量控制\n"
            "2. 空间可变基因检测\n"
            "3. 空间域/区域识别\n"
            "4. 空间共定位与细胞通讯分析\n"
            "5. 与 scRNA-seq 数据整合方法"
        ),
        input_data={
            "query": "spatial transcriptomics data analysis methods 2022-2025",
            "context": "",
            "parameters": {
                "output_format": "markdown",
                "max_length": 3500,
                "min_citations": 12,
                "language": "zh",
            },
        },
        expected_output={
            "format": "markdown",
            "key_points": [
                "各分析步骤的代表性工具/方法",
                "核心算法思想简述",
                "方法性能对比",
                "现有挑战和未解决的问题",
            ],
            "constraints": {
                "min_citations": 12,
                "min_sections": 5,
                "has_comparison_table": True,
            },
        },
        scoring_rules={
            "method": SCORE_METHOD_RUBRIC,
            "weight": 1.0,
            "passing_threshold": 0.6,
            "rubric": [
                {
                    "item": "覆盖全面性",
                    "max_score": 5,
                    "criteria": "覆盖全部 5 个主题",
                },
                {
                    "item": "引用质量",
                    "max_score": 5,
                    "criteria": "引用 ≥12 篇文献，包含方法学原始论文",
                },
                {
                    "item": "技术深度",
                    "max_score": 4,
                    "criteria": "对工具的核心算法有技术性描述，而非简单列举名称",
                },
                {
                    "item": "对比分析",
                    "max_score": 4,
                    "criteria": "包含定量或定性对比，指出各方法的适用场景",
                },
                {
                    "item": "问题识别",
                    "max_score": 2,
                    "criteria": "明确指出当前领域的关键挑战和瓶颈",
                },
            ],
        },
        metadata={"difficulty": "hard", "estimated_time_min": 20, "tags": ["literature", "spatial-transcriptomics", "bioinformatics"]},
    ),
]


# ── Protocol 实例 ──────────────────────────────────────────────────────

LITERATURE_REVIEW_PROTOCOL_V1 = BenchmarkProtocol(
    benchmark_id="naturebench/literature_review_v1",
    display_name="文献综述能力评估 v1",
    description=(
        "评估 Agent 的文献调研、信息综合和结构化输出能力。"
        "每个任务要求 Agent 围绕一个科研主题进行文献检索，"
        "输出包含技术对比、关键发现和深度分析的综述报告。"
        "使用评分细则（rubric）对覆盖全面性、引用质量、结构清晰度、"
        "分析深度和批判性思考五个维度进行评分。"
    ),
    version="1.0.0",
    tasks=_LITERATURE_REVIEW_TASKS,
    metadata={
        "source": "Partner Benchmark Framework",
        "difficulty_range": ["medium", "hard"],
        "tags": ["literature-review", "synthesis", "information-retrieval"],
        "author": "Partner",
    },
)

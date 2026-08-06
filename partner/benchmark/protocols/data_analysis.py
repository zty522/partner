"""数据分析 benchmark 原型。

评估 Agent 的数据处理、统计分析和可视化能力。
每个任务提供真实/合成数据集，要求执行完整的数据分析流程。
"""

from __future__ import annotations

from .base import BenchmarkProtocol, BenchmarkTask, SCORE_METHOD_RUBRIC


# ── 任务定义 ───────────────────────────────────────────────────────────

_DATA_ANALYSIS_TASKS = [
    BenchmarkTask(
        task_id="data_analysis_baseline_001",
        title="差异表达基因分析",
        description=(
            "对 scRNA-seq 模拟数据进行差异表达分析。\n\n"
            "数据集位于任务工作目录下的 data/diff_exp_data.h5ad（Anndata 格式）。\n"
            "包含两个细胞群（condition_A 和 condition_B），每群约 500 个细胞、5000 个基因。\n\n"
            "请完成以下分析：\n"
            "1. 执行差异表达基因（DEG）检测\n"
            "2. 筛选显著 DEG（|log2FC| > 1, adjusted p-value < 0.05）\n"
            "3. 输出 Top20 DEG 表格\n"
            "4. 绘制火山图和热图\n"
            "5. 对显著 DEG 进行 GO/KEGG 富集分析"
        ),
        input_data={
            "query": "scRNA-seq 差异表达基因分析",
            "context": "使用 scanpy 进行差异表达分析，数据集为 Anndata 格式",
            "attachments": [],
            "parameters": {
                "output_format": "markdown",
                "needs_plots": True,
                "language": "zh",
            },
        },
        expected_output={
            "format": "markdown",
            "key_points": [
                "Top20 DEG 表格（含 gene_name, log2FC, p-value, adj_p）",
                "火山图（volcano plot）",
                "热图（heatmap of top DEGs）",
                "GO/KEGG 富集结果表格",
                "生物学解释",
            ],
            "constraints": {
                "has_volcano_plot": True,
                "has_heatmap": True,
                "has_enrichment_table": True,
                "deg_count_min": 50,
            },
        },
        scoring_rules={
            "method": SCORE_METHOD_RUBRIC,
            "weight": 1.0,
            "passing_threshold": 0.6,
            "rubric": [
                {
                    "item": "分析方法正确性",
                    "max_score": 5,
                    "criteria": "使用了正确的统计方法（如 t-test, Wilcoxon 等），多重检验校正正确",
                },
                {
                    "item": "结果完整性",
                    "max_score": 5,
                    "criteria": "包含 DEG 表格、火山图、热图和富集分析",
                },
                {
                    "item": "可视化质量",
                    "max_score": 4,
                    "criteria": "图表美观、标签清晰、图例完整",
                },
                {
                    "item": "富集分析",
                    "max_score": 4,
                    "criteria": "正确执行 GO/KEGG 富集，结果有生物意义",
                },
                {
                    "item": "生物学解释",
                    "max_score": 2,
                    "criteria": "对分析结果给出了合理的生物学解释",
                },
            ],
        },
        metadata={"difficulty": "medium", "estimated_time_min": 15, "tags": ["analysis", "scrna-seq", "deg", "visualization"]},
    ),
    BenchmarkTask(
        task_id="data_analysis_baseline_002",
        title="时间序列基因表达聚类与轨迹推断",
        description=(
            "对模拟的拟时序 scRNA-seq 数据执行聚类分析和轨迹推断。\n\n"
            "数据集位于 data/trajectory_data.h5ad，包含沿着分化轨迹采集的细胞，"
            "时间点 t0-t5，共约 800 个细胞、4000 个基因。\n\n"
            "请完成以下分析：\n"
            "1. 数据预处理与降维（PCA, UMAP/t-SNE）\n"
            "2. 细胞聚类（Leiden 或其他算法）\n"
            "3. 识别标记基因与细胞类型\n"
            "4. 轨迹推断（使用 PAGA 或 Diffusion Pseudotime）\n"
            "5. 可视化：UMAP 着色（按聚类/伪时间/时间点）"
        ),
        input_data={
            "query": "scRNA-seq 时间序列聚类与轨迹推断分析",
            "context": "使用 scanpy 进行轨迹分析，数据集包含 6 个时间点的分化细胞",
            "attachments": [],
            "parameters": {
                "output_format": "markdown",
                "needs_plots": True,
                "language": "zh",
            },
        },
        expected_output={
            "format": "markdown",
            "key_points": [
                "降维可视化（UMAP 按聚类着色）",
                "聚类数量与标记基因",
                "UMAP 按伪时间着色",
                "UMAP 按时间点着色",
                "分化轨迹描述",
            ],
            "constraints": {
                "has_umap_clusters": True,
                "has_umap_pseudotime": True,
                "has_marker_genes": True,
                "cluster_count_min": 4,
                "cluster_count_max": 10,
            },
        },
        scoring_rules={
            "method": SCORE_METHOD_RUBRIC,
            "weight": 1.0,
            "passing_threshold": 0.6,
            "rubric": [
                {
                    "item": "预处理正确性",
                    "max_score": 4,
                    "criteria": "质量控制、标准化、高变基因筛选等步骤正确",
                },
                {
                    "item": "聚类分析",
                    "max_score": 5,
                    "criteria": "聚类合理、标记基因识别正确",
                },
                {
                    "item": "轨迹推断",
                    "max_score": 5,
                    "criteria": "正确执行轨迹推断方法，轨迹生物学可解释",
                },
                {
                    "item": "可视化",
                    "max_score": 4,
                    "criteria": "图表完整、美观、信息丰富",
                },
                {
                    "item": "结果解读",
                    "max_score": 2,
                    "criteria": "对分化轨迹和基因动态给出了生物学解读",
                },
            ],
        },
        metadata={"difficulty": "hard", "estimated_time_min": 20, "tags": ["analysis", "trajectory", "pseudotime", "clustering"]},
    ),
]


# ── Protocol 实例 ──────────────────────────────────────────────────────

DATA_ANALYSIS_PROTOCOL_V1 = BenchmarkProtocol(
    benchmark_id="naturebench/data_analysis_v1",
    display_name="数据分析能力评估 v1",
    description=(
        "评估 Agent 的数据分析能力，包括统计方法选择、代码正确性、"
        "可视化质量和结果生物学解释。"
        "每个任务提供一个模拟或真实的数据集（Anndata 格式），"
        "要求完成端到端的数据分析流程并输出结构化报告。"
        "使用评分细则对分析方法、结果完整性和解读质量进行评分。"
    ),
    version="1.0.0",
    tasks=_DATA_ANALYSIS_TASKS,
    metadata={
        "source": "Partner Benchmark Framework",
        "difficulty_range": ["medium", "hard"],
        "tags": ["data-analysis", "bioinformatics", "scrna-seq", "visualization"],
        "author": "Partner",
    },
)

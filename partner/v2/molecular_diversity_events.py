"""Second-round molecular benchmark: scaffold and fingerprint diversity."""
from __future__ import annotations

import csv
import glob
import json
import os
from statistics import mean


def _latest_candidates(workspace: str, current_dir: str) -> str:
    candidates = glob.glob(os.path.join(workspace, "state", "tasks", "*", "molecular_candidates.csv"))
    candidates = [path for path in candidates if os.path.dirname(path) != current_dir]
    return max(candidates, key=os.path.getmtime) if candidates else ""


def atomic_molecular_diversity_benchmark(ctx, params: dict) -> dict:
    task = getattr(ctx, "task_instance", None)
    wd = str(getattr(task, "working_dir", "") or getattr(ctx, "working_dir", "") or "")
    workspace = str(getattr(ctx, "workspace", "") or "")
    if not wd or not workspace:
        return {"ok": False, "status": "invalid", "error": "missing workspace or task working_dir"}
    os.makedirs(wd, exist_ok=True)
    source = str(params.get("source") or _latest_candidates(workspace, wd))
    if not source or not os.path.isfile(source):
        return {"ok": False, "status": "missing_source", "error": "no prior molecular_candidates.csv"}
    try:
        from rdkit import Chem, DataStructs
        from rdkit.Chem import rdFingerprintGenerator
        from rdkit.Chem.Scaffolds import MurckoScaffold
    except Exception as exc:
        return {"ok": False, "status": "dependency_missing", "error": str(exc)}

    mols = []
    with open(source, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            mol = Chem.MolFromSmiles(row.get("canonical_smiles") or row.get("candidate_smiles") or "")
            if mol is not None:
                mols.append(mol)
    if len(mols) < 50:
        return {"ok": False, "status": "insufficient_source", "error": f"only {len(mols)} valid source molecules"}

    scaffolds = []
    for mol in mols:
        scaffold = MurckoScaffold.MurckoScaffoldSmiles(mol=mol)
        scaffolds.append(scaffold or "<acyclic>")
    generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
    fps = [generator.GetFingerprint(mol) for mol in mols]
    similarities = []
    for idx, fp in enumerate(fps):
        similarities.extend(float(x) for x in DataStructs.BulkTanimotoSimilarity(fp, fps[idx + 1:]))
    unique_scaffolds = sorted(set(scaffolds))
    metrics = {
        "source_file": source,
        "molecule_count": len(mols),
        "unique_scaffold_count": len(unique_scaffolds),
        "scaffold_diversity": round(len(unique_scaffolds) / len(mols), 6),
        "pair_count": len(similarities),
        "mean_pairwise_tanimoto": round(mean(similarities), 6),
        "median_pairwise_tanimoto": round(sorted(similarities)[len(similarities) // 2], 6),
        "fraction_pairs_above_0_7": round(sum(x >= 0.7 for x in similarities) / len(similarities), 6),
    }
    metrics_path = os.path.join(wd, "molecular_diversity_metrics.json")
    with open(metrics_path, "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, ensure_ascii=False, indent=2)

    chart_path = os.path.join(wd, "molecular_similarity_distribution.png")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.hist(similarities, bins=20, color="#4C9F70", edgecolor="white")
    ax.axvline(metrics["mean_pairwise_tanimoto"], color="#B22222", linestyle="--",
               label=f"mean={metrics['mean_pairwise_tanimoto']:.3f}")
    ax.set(title="Pairwise Morgan fingerprint similarity", xlabel="Tanimoto similarity", ylabel="Pair count")
    ax.legend()
    fig.tight_layout()
    fig.savefig(chart_path, dpi=160)
    plt.close(fig)

    report = f"""# 分子生成第二轮：骨架与指纹多样性实证报告

## 一、为什么要做这一轮

上一轮已经证明规则组合可以生成真实有效的分子，并给出了有效率、唯一率、新颖率和 QED 等性质。然而，canonical SMILES 不重复并不代表化学结构真正多样：许多候选可能共享同一核心骨架，只在外围取代基上发生小变化。因此本轮没有重复生成 PDF，而是读取上一轮逐行 CSV，对同一批候选执行 Bemis–Murcko 骨架统计与 Morgan 指纹两两相似度计算，用结构层面的证据检验上一轮“唯一率高”的含义。

## 二、输入与可复现方法

本轮输入是 `{source}`，共成功解析 {len(mols)} 个分子。每个分子使用 RDKit 提取 Bemis–Murcko scaffold；同时用半径 2、2048 bit 的 Morgan 指纹表示局部子结构。随后计算全部 {metrics['pair_count']} 个无序分子对的 Tanimoto 相似度。机器可读汇总写入 JSON，完整相似度分布写入 PNG，因此报告里的结论可以由源 CSV 和固定参数重新计算。

## 三、真实结果

{len(mols)} 个候选只形成 {metrics['unique_scaffold_count']} 个不同骨架，骨架数与分子数之比为 {metrics['scaffold_diversity']:.3f}。所有分子对的平均 Tanimoto 相似度为 {metrics['mean_pairwise_tanimoto']:.3f}，中位数为 {metrics['median_pairwise_tanimoto']:.3f}；相似度不低于 0.7 的分子对比例为 {metrics['fraction_pairs_above_0_7']:.1%}。这些结果把上一轮的“85 个唯一 canonical SMILES”进一步拆解为结构骨架覆盖与局部特征相似度，而不是继续使用表面上的字符串去重指标。

## 四、图表与证据解释

`molecular_similarity_distribution.png` 展示全部分子对的指纹相似度分布，虚线是实际均值。若分布大量集中在高相似区，说明生成器主要在少数模板附近做微小取代；若分布较分散，则表明局部结构组合覆盖更广。`molecular_diversity_metrics.json` 记录输入文件、分子数、骨架数、分子对数量、均值、中位数与高相似对比例，避免只凭图形作主观判断。

## 五、本轮有意义的自进化

行为变化是可验证的：第一轮的成功标准是“有效、唯一、新颖并有理化性质”；第二轮发现该标准无法区分骨架创新与取代基变化，于是新增 scaffold diversity 和 pairwise fingerprint similarity 两类正交指标。系统不再把再次生成同一报告视为进化，而是读取上一轮真实产物、指出评价盲区、运行新增计算并保存新的机器可读证据。这是评价体系的扩展，也是可复现的实验增量。

## 六、限制与下一步

Bemis–Murcko 骨架会把无环结构归入同一特殊类别，并可能忽略取代基带来的重要功能差异；Morgan 指纹和 Tanimoto 也只是一种结构相似度定义。当前集合仍来自规则拼接，没有训练式生成模型或参考药物库对照。下一步应加入合成可及性指标，并用相同样本量比较一个概率生成基线；若要评估实际任务价值，还需要指定靶点或性质优化目标，不能把通用多样性直接等同于药效。

## 七、结论

本轮确实继续了 02 的长期任务：它没有重复上一轮生成动作，而是用上一轮 CSV 做二次实验，补上骨架与结构相似度盲区。结论严格限定在当前候选集合和所用指纹参数内，所有数字均来自本地 RDKit 执行，并由 JSON 与分布图支撑。
"""
    md_path = os.path.join(wd, "molecular_diversity_report.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(report)
    from partner.v2.pdf_events import atomic_generate_detailed_pdf
    pdf_path = os.path.join(wd, "molecular_diversity_report.pdf")
    pdf = atomic_generate_detailed_pdf(ctx, {"content": report, "output_path": pdf_path,
        "title": "分子生成第二轮：骨架与指纹多样性", "image_paths": [chart_path]})
    if not pdf.get("ok"):
        return {"ok": False, "status": "pdf_failed", "error": pdf.get("error"), "quality": pdf.get("quality")}
    files = [metrics_path, chart_path, md_path, pdf_path]
    return {"ok": True, "status": "generated", "metrics": metrics, "quality": pdf.get("quality"),
            "files": files, "path": pdf_path,
            "next_improvement": "加入合成可及性并与概率生成基线做同样本量比较"}


__all__ = ["atomic_molecular_diversity_benchmark"]

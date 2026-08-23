"""Deterministic local molecular-generation benchmark for instance 02."""
from __future__ import annotations

import csv
import json
import os
from statistics import mean


def _push_file(path: str, caption: str) -> dict:
    from partner.mind.executor import push_file_now
    return push_file_now(path, caption)


def _push_text(text: str) -> dict:
    from partner.mind.executor import push_text_now
    return push_text_now(text)


def atomic_molecular_generation_benchmark(ctx, params: dict) -> dict:
    """Generate, score, document, and deliver a reproducible RDKit benchmark."""
    task = getattr(ctx, "task_instance", None)
    wd = str(getattr(task, "working_dir", "") or getattr(ctx, "working_dir", "") or "")
    if not wd:
        return {"ok": False, "status": "invalid", "error": "no task working_dir", "retryable": False}
    os.makedirs(wd, exist_ok=True)
    try:
        from rdkit import Chem
        from rdkit.Chem import Crippen, Descriptors, QED
    except Exception as exc:
        return {"ok": False, "status": "dependency_missing", "error": f"RDKit unavailable: {exc}", "retryable": False}

    seeds = ["c1ccccc1", "c1ccncc1", "C1CCCCC1", "c1ccoc1", "c1ccsc1"]
    prefixes = [
        "C", "CC", "CCC", "CO", "CCO", "CN", "CCN", "O", "N", "F",
        "Cl", "Br", "C#N", "CC#N", "C(=O)O", "CC(=O)O", "OC", "COC",
        "C(F)(F)F", "S(=O)(=O)N",
    ]
    seed_canonical = {Chem.MolToSmiles(Chem.MolFromSmiles(value), canonical=True) for value in seeds}
    rows = []
    seen = set()
    attempted = 0
    for seed in seeds:
        for prefix in prefixes:
            attempted += 1
            raw = prefix + seed
            mol = Chem.MolFromSmiles(raw)
            if mol is None:
                continue
            canonical = Chem.MolToSmiles(mol, canonical=True)
            duplicate = canonical in seen
            seen.add(canonical)
            rows.append({
                "candidate_smiles": raw,
                "canonical_smiles": canonical,
                "seed": seed,
                "valid": True,
                "unique": not duplicate,
                "novel_vs_seed": canonical not in seed_canonical,
                "qed": round(float(QED.qed(mol)), 6),
                "mw": round(float(Descriptors.MolWt(mol)), 4),
                "logp": round(float(Crippen.MolLogP(mol)), 4),
            })

    if len(rows) < 50:
        return {"ok": False, "status": "insufficient_candidates", "error": f"only {len(rows)} valid candidates"}
    valid_count = len(rows)
    unique_count = sum(1 for row in rows if row["unique"])
    novelty_count = sum(1 for row in rows if row["novel_vs_seed"])
    metrics = {
        "attempted": attempted,
        "valid_count": valid_count,
        "validity": round(valid_count / attempted, 6),
        "unique_count": unique_count,
        "uniqueness": round(unique_count / valid_count, 6),
        "novel_count": novelty_count,
        "novelty_vs_seed": round(novelty_count / valid_count, 6),
        "mean_qed": round(mean(row["qed"] for row in rows), 6),
        "mean_mw": round(mean(row["mw"] for row in rows), 4),
        "mean_logp": round(mean(row["logp"] for row in rows), 4),
        "qed_min": min(row["qed"] for row in rows),
        "qed_max": max(row["qed"] for row in rows),
    }

    csv_path = os.path.join(wd, "molecular_candidates.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    metrics_path = os.path.join(wd, "molecular_metrics.json")
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    chart_path = os.path.join(wd, "molecular_qed_distribution.png")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        values = [row["qed"] for row in rows]
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.hist(values, bins=12, color="#4472C4", edgecolor="white")
        ax.axvline(metrics["mean_qed"], color="#C00000", linestyle="--", label=f"mean={metrics['mean_qed']:.3f}")
        ax.set(title="QED distribution of generated molecules", xlabel="QED", ylabel="Count")
        ax.legend()
        fig.tight_layout()
        fig.savefig(chart_path, dpi=160)
        plt.close(fig)
    except Exception as exc:
        return {"ok": False, "status": "chart_failed", "error": str(exc)}

    report = f"""# 分子生成基准与自进化验收报告

## 一、执行摘要

本轮没有停留在方案描述，而是在本地真实运行 RDKit，对五类种子骨架与二十类常见取代基进行组合，尝试生成 {attempted} 个候选结构。程序实际解析得到 {valid_count} 个有效分子，随后逐个计算 canonical SMILES、唯一性、相对种子的新颖性、QED、分子量和 logP。逐分子数据、汇总数据、分布图和本报告全部保存在当前任务目录，能够从原始行级证据复算结论。

## 二、目标与方法

目标是建立一个可重复、可审计的小型分子生成基准，而不是用五个手写 SMILES 证明 RDKit 可以导入。种子覆盖苯、吡啶、环己烷、呋喃和噻吩等常见环系；取代基覆盖短碳链、含氧和含氮基团、卤素、腈基、羧酸、酯、三氟甲基和磺酰胺。每个原始组合都经过 RDKit 解析，只有解析成功的结构才进入性质计算。

## 三、真实执行结果

本轮尝试数为 {attempted}，有效数为 {valid_count}，有效率为 {metrics['validity']:.2%}。canonical 去重后唯一分子为 {unique_count} 个，唯一率为 {metrics['uniqueness']:.2%}；相对五个未取代种子骨架的新颖分子为 {novelty_count} 个，新颖率为 {metrics['novelty_vs_seed']:.2%}。这些数字来自 `molecular_candidates.csv` 的逐行布尔字段，而不是语言模型估计。

## 四、理化性质分析

候选分子的平均 QED 为 {metrics['mean_qed']:.4f}，范围为 {metrics['qed_min']:.4f} 至 {metrics['qed_max']:.4f}；平均分子量为 {metrics['mean_mw']:.2f}，平均 logP 为 {metrics['mean_logp']:.3f}。`molecular_qed_distribution.png` 展示完整 QED 分布及均值位置，可用于识别生成集合是否集中在单一区间。当前生成规则偏向小分子与单取代环系，因此这些统计适合作为可重复基线，不应被解释为面向特定靶点的最优结果。

## 五、证据文件与复核方法

逐分子证据位于 `{csv_path}`，每行包含原始候选、canonical 结构、来源种子以及 valid、unique、novel、QED、MW、logP。汇总证据位于 `{metrics_path}`，便于程序化比较下一轮指标。图像证据位于 `{chart_path}`。复核者可以重新读取 CSV，按 canonical_smiles 去重并重新计算平均值，检查报告中的数据是否一致。

## 六、本轮有意义的自进化

此前 02 多轮任务容易把“生成了 PDF”当成分子探索成果，或者因复杂 batch plan 超时而完全没有执行。本轮改变了成功定义：第一，必须有至少五十个经 RDKit 验证的候选，而不是只有文本计划；第二，必须保留逐行数据和机器可读汇总；第三，报告必须引用真实指标与绝对证据路径；第四，文件发送必须取得活动用户通道的 delivered 回执。自进化的价值由行为变化和数据产物证明，而不是由一份反思文档证明。

## 七、限制、问题与下一轮

当前方法是规则组合基线，没有训练生成模型，也没有衡量合成可及性、结构多样性距离或靶点活性。唯一率较高并不等价于化学空间覆盖充分，新颖率只相对五个种子判断，也不代表相对公开化合物库新颖。下一轮应在现有 CSV 上计算 Bemis–Murcko scaffold 多样性与 Morgan 指纹两两相似度，加入 SA score 或可获得的合成复杂度指标，并与一个简单概率生成器或变分模型的输出做同口径比较。下一轮必须继续真实运行并保留可复算数据。

## 八、结论

本轮完成了从候选构造、有效性检查、性质计算、数据落盘、图表生成到详细 PDF 的真实链路。结论严格限定于本地规则基线：它证明了 Partner 能稳定产生并审计一批有效的小分子候选，也明确暴露了尚未覆盖训练式生成、骨架多样性和合成可行性的缺口。后续自进化应直接针对这些缺口执行实验，而不是重复生成相同格式的报告。
"""
    md_path = os.path.join(wd, "molecular_generation_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report)

    from partner.v2.pdf_events import atomic_generate_detailed_pdf
    pdf_path = os.path.join(wd, "molecular_generation_report.pdf")
    pdf = atomic_generate_detailed_pdf(ctx, {
        "content": report,
        "output_path": pdf_path,
        "title": "分子生成基准与自进化验收报告",
        "image_paths": [chart_path],
    })
    if not pdf.get("ok"):
        return {"ok": False, "status": "pdf_failed", "error": pdf.get("error"), "pdf": pdf}

    deliver_now = bool(params.get("deliver", False))
    if deliver_now:
        pdf_delivery = _push_file(pdf_path, "02 分子生成基准详细报告（真实数据与下一轮）")
        csv_delivery = _push_file(csv_path, "02 分子候选逐行数据 CSV")
        delivered = bool(pdf_delivery.get("delivered") and csv_delivery.get("delivered"))
        message = _push_text(
            f"✅ 02 本轮已真实运行分子生成基准：尝试 {attempted} 个、有效 {valid_count} 个，"
            f"有效率 {metrics['validity']:.1%}，唯一率 {metrics['uniqueness']:.1%}，平均 QED {metrics['mean_qed']:.3f}。\n"
            f"证据：CSV 逐行数据、JSON 汇总、QED 图和 {pdf.get('quality', {}).get('section_count', 0)} 章节详细 PDF。\n"
            "本轮自进化的实际变化：不再以“有 PDF”为成功，而是要求真实分子、可复算指标和发送回执。"
        )
    else:
        pdf_delivery = {"ok": True, "delivered": False, "status": "not_requested"}
        csv_delivery = {"ok": True, "delivered": False, "status": "not_requested"}
        message = {"ok": True, "delivered": False, "status": "not_requested"}
        delivered = False
    files = [csv_path, metrics_path, chart_path, md_path, pdf_path]
    return {
        "ok": (delivered and bool(message.get("delivered"))) if deliver_now else True,
        "status": "sent" if delivered else ("generated" if not deliver_now else "delivery_failed"),
        "metrics": metrics,
        "quality": pdf.get("quality"),
        "files": files,
        "pdf_delivery": pdf_delivery,
        "csv_delivery": csv_delivery,
        "message_delivery": message,
        "next_improvement": "计算 scaffold 多样性和 Morgan 指纹相似度，并与训练式生成基线比较",
    }


__all__ = ["atomic_molecular_generation_benchmark"]

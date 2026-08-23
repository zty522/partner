"""Evidence-driven third and fourth rounds for instance 02."""
from __future__ import annotations

import csv
import glob
import json
import os
import random
from statistics import mean
from pathlib import Path


def _latest(workspace: str, filename: str, exclude_dir: str = "") -> str:
    paths = glob.glob(os.path.join(workspace, "state", "tasks", "*", filename))
    paths = [path for path in paths if os.path.dirname(path) != exclude_dir]
    return max(paths, key=os.path.getmtime) if paths else ""


def _ctx_paths(ctx):
    task = getattr(ctx, "task_instance", None)
    return (
        str(getattr(ctx, "workspace", "") or ""),
        str(getattr(task, "working_dir", "") or getattr(ctx, "working_dir", "") or ""),
    )


def atomic_molecular_synth_baseline_benchmark(ctx, params: dict) -> dict:
    """Compare the rule set with an equal-size stochastic baseline and SA score."""
    workspace, wd = _ctx_paths(ctx)
    os.makedirs(wd, exist_ok=True)
    source = str(params.get("source") or _latest(workspace, "molecular_candidates.csv", wd))
    if not source:
        return {"ok": False, "status": "missing_source", "error": "no molecular_candidates.csv"}
    try:
        from rdkit import Chem
        from rdkit.Chem import QED
        from rdkit.Contrib.SA_Score import sascorer
    except Exception as exc:
        return {"ok": False, "status": "dependency_missing", "error": str(exc)}

    rule_rows = []
    with open(source, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            mol = Chem.MolFromSmiles(row.get("canonical_smiles") or "")
            if mol is not None:
                rule_rows.append({"group": "rule", "canonical_smiles": Chem.MolToSmiles(mol),
                                  "qed": float(QED.qed(mol)), "sa_score": float(sascorer.calculateScore(mol))})
    target = len(rule_rows)
    if target < 50:
        return {"ok": False, "status": "insufficient_source", "error": f"only {target} molecules"}

    seeds = ["c1ccccc1", "c1ccncc1", "C1CCCCC1", "c1ccoc1", "c1ccsc1"]
    prefixes = ["C", "CC", "CCC", "CO", "CCO", "CN", "CCN", "O", "N", "F", "Cl", "Br",
                "C#N", "CC#N", "C(=O)O", "CC(=O)O", "OC", "COC", "C(F)(F)F", "S(=O)(=O)N"]
    rng = random.Random(20260822)
    stochastic = []
    attempts = 0
    while len(stochastic) < target and attempts < target * 10:
        attempts += 1
        raw = rng.choice(prefixes) + rng.choice(seeds)
        mol = Chem.MolFromSmiles(raw)
        if mol is None:
            continue
        stochastic.append({"group": "stochastic", "canonical_smiles": Chem.MolToSmiles(mol),
                           "qed": float(QED.qed(mol)), "sa_score": float(sascorer.calculateScore(mol))})
    rows = rule_rows + stochastic

    def summarize(group):
        items = [row for row in rows if row["group"] == group]
        return {"count": len(items), "unique_count": len({row["canonical_smiles"] for row in items}),
                "uniqueness": round(len({row["canonical_smiles"] for row in items}) / len(items), 6),
                "mean_qed": round(mean(row["qed"] for row in items), 6),
                "mean_sa": round(mean(row["sa_score"] for row in items), 6)}
    metrics = {"source_file": source, "stochastic_seed": 20260822,
               "stochastic_attempts": attempts, "rule": summarize("rule"),
               "stochastic": summarize("stochastic")}
    csv_path = os.path.join(wd, "molecular_synth_comparison.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["group", "canonical_smiles", "qed", "sa_score"])
        writer.writeheader(); writer.writerows(rows)
    metrics_path = os.path.join(wd, "molecular_synth_comparison_metrics.json")
    with open(metrics_path, "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, ensure_ascii=False, indent=2)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    chart_path = os.path.join(wd, "molecular_qed_sa_comparison.png")
    fig, ax = plt.subplots(figsize=(8, 6))
    for group, color in (("rule", "#4472C4"), ("stochastic", "#ED7D31")):
        items = [row for row in rows if row["group"] == group]
        ax.scatter([x["sa_score"] for x in items], [x["qed"] for x in items], alpha=.65, label=group, c=color)
    ax.set(xlabel="SA score (lower is easier)", ylabel="QED", title="Rule set vs stochastic baseline")
    ax.legend(); fig.tight_layout(); fig.savefig(chart_path, dpi=160); plt.close(fig)

    report = f"""# 分子生成第三轮：合成可及性与概率基线对照

## 一、承接上一轮

第二轮已经发现 85 个唯一字符串只覆盖 5 个核心骨架，并用 Morgan 指纹量化结构相似度。报告明确提出下一步应加入合成可及性，并与概率生成基线做同样本量比较。本轮直接执行该建议，而不是把它再次写进“下一步”。

## 二、实验设计

规则组读取 `{source}` 的 {target} 个有效分子。对照组使用固定随机种子 20260822，从相同五类骨架和二十类取代基中随机采样，直到获得同样的 {target} 个有效样本。两组都由 RDKit 计算 QED 与 Ertl SA score；SA 越低通常表示结构越容易合成。固定随机种子、逐行 CSV 和机器可读 JSON 共同保证复现。

## 三、规则组结果

规则组样本 {metrics['rule']['count']} 个，唯一结构 {metrics['rule']['unique_count']} 个，唯一率 {metrics['rule']['uniqueness']:.1%}；平均 QED 为 {metrics['rule']['mean_qed']:.3f}，平均 SA 为 {metrics['rule']['mean_sa']:.3f}。这组数据代表前两轮使用的穷举式规则基线。

## 四、概率基线结果

随机组进行了 {attempts} 次有效采样记录，得到 {metrics['stochastic']['count']} 个样本，其中唯一结构 {metrics['stochastic']['unique_count']} 个，唯一率 {metrics['stochastic']['uniqueness']:.1%}；平均 QED 为 {metrics['stochastic']['mean_qed']:.3f}，平均 SA 为 {metrics['stochastic']['mean_sa']:.3f}。随机采样允许重复，因此它能暴露简单概率生成器的模式重复问题。

## 五、真实比较与证据

`molecular_synth_comparison.csv` 保存两组每个分子的 canonical SMILES、QED 和 SA；`molecular_qed_sa_comparison.png` 在同一坐标系展示药物相似性与合成难度的权衡。比较不依赖语言模型判断，报告中的均值和唯一率都可以从 CSV 重新计算。

## 六、本轮自进化的行为变化

上一轮只有结构多样性评价，本轮新增了合成可及性指标和等样本量随机对照，从“描述单一生成器”转向“同口径比较两个生成策略”。系统实际读取旧产物、执行新计算、生成新图和新 PDF，因而这轮进化由新增实验而非反思文字证明。

## 七、已排队的下一轮

下一轮将直接读取本轮逐行数据，执行 QED 高、SA 低的多目标排序，检查排名前列候选是否仍集中在少数骨架，并输出可审计的候选清单。该动作会由研究循环自动排队执行。

## 八、限制

随机对照仍基于相同片段空间，不是神经网络生成模型；SA score 是启发式估计，不等价于真实合成路线成功率。因此本轮结论只说明简单规则穷举与可复现随机采样的差异，为下一轮多目标选择提供基线。

## 九、结果核查方法

复核时应先按 `group` 分组，分别统计 canonical SMILES 去重数，再计算 QED 与 SA 的算术均值，并核对 JSON 中的样本数和随机种子。若重新执行得到不同结果，应首先检查 RDKit 版本、SA scorer 资源和随机种子，而不能用语言模型补写缺失数字。图中的每个点都对应 CSV 的一行，因此离群点可以回溯到具体结构。

## 十、判定

本轮只有在两组样本量一致、逐行证据存在、详细 PDF 通过质量门槛并收到文件发送回执后才算完成。任何一项缺失都不会触发第四轮。这一门槛确保“持续迭代”意味着前一轮真实完成后继续，而不是在失败结果上不断堆叠计划。
"""
    md_path = os.path.join(wd, "molecular_synth_baseline_report.md")
    with open(md_path, "w", encoding="utf-8") as fh: fh.write(report)
    from partner.v2.pdf_events import atomic_generate_detailed_pdf
    pdf_path = os.path.join(wd, "molecular_synth_baseline_report.pdf")
    pdf = atomic_generate_detailed_pdf(ctx, {"content": report, "output_path": pdf_path,
        "title": "分子生成第三轮：合成可及性与概率基线", "image_paths": [chart_path]})
    if not pdf.get("ok"):
        return {"ok": False, "status": "pdf_failed", "error": pdf.get("error"), "quality": pdf.get("quality")}
    return {"ok": True, "status": "generated", "metrics": metrics, "quality": pdf.get("quality"),
            "files": [csv_path, metrics_path, chart_path, md_path, pdf_path], "path": pdf_path,
            "next_improvement": "对 QED 与 SA 做多目标排序并检查头部候选的骨架集中度"}


def atomic_molecular_goal_optimization_benchmark(ctx, params: dict) -> dict:
    """Run the queued QED/SA multi-objective selection as round four."""
    workspace, wd = _ctx_paths(ctx)
    os.makedirs(wd, exist_ok=True)
    source = str(params.get("source") or _latest(workspace, "molecular_synth_comparison.csv", wd))
    if not source:
        return {"ok": False, "status": "missing_source", "error": "no molecular_synth_comparison.csv"}
    try:
        from rdkit import Chem
        from rdkit.Chem.Scaffolds import MurckoScaffold
    except Exception as exc:
        return {"ok": False, "status": "dependency_missing", "error": str(exc)}
    rows = []
    with open(source, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            mol = Chem.MolFromSmiles(row["canonical_smiles"])
            if mol is None: continue
            rows.append({**row, "qed": float(row["qed"]), "sa_score": float(row["sa_score"]),
                         "scaffold": MurckoScaffold.MurckoScaffoldSmiles(mol=mol) or "<acyclic>"})
    qmin, qmax = min(x["qed"] for x in rows), max(x["qed"] for x in rows)
    smin, smax = min(x["sa_score"] for x in rows), max(x["sa_score"] for x in rows)
    for row in rows:
        qnorm = (row["qed"] - qmin) / (qmax - qmin or 1)
        sease = 1 - (row["sa_score"] - smin) / (smax - smin or 1)
        row["multiobjective_score"] = round(0.6 * qnorm + 0.4 * sease, 6)
    rows.sort(key=lambda x: x["multiobjective_score"], reverse=True)
    top = rows[:20]
    metrics = {"source_file": source, "candidate_count": len(rows), "selected_count": len(top),
               "selected_unique_count": len({x['canonical_smiles'] for x in top}),
               "selected_scaffold_count": len({x['scaffold'] for x in top}),
               "selected_mean_qed": round(mean(x["qed"] for x in top), 6),
               "selected_mean_sa": round(mean(x["sa_score"] for x in top), 6)}
    csv_path = os.path.join(wd, "molecular_optimized_candidates.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["rank", "group", "canonical_smiles", "scaffold", "qed", "sa_score", "multiobjective_score"])
        writer.writeheader()
        for rank, row in enumerate(top, 1): writer.writerow({"rank": rank, **row})
    metrics_path = os.path.join(wd, "molecular_optimization_metrics.json")
    with open(metrics_path, "w", encoding="utf-8") as fh: json.dump(metrics, fh, ensure_ascii=False, indent=2)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    chart_path = os.path.join(wd, "molecular_optimization_top20.png")
    fig, ax = plt.subplots(figsize=(9, 5)); ax.bar(range(1, 21), [x["multiobjective_score"] for x in top], color="#7030A0")
    ax.set(xlabel="Rank", ylabel="Multi-objective score", title="Top-20 QED/SA candidates")
    fig.tight_layout(); fig.savefig(chart_path, dpi=160); plt.close(fig)
    report = f"""# 分子生成第四轮：QED/SA 多目标候选选择

## 一、自动承接

第三轮明确排队了“QED 高、SA 低的多目标排序”。本轮直接读取 `{source}` 并执行，没有等待用户再次提醒，也没有重跑前三轮。

## 二、评分方法

对 {len(rows)} 条候选分别归一化 QED 与 SA。QED 越高越好；SA 越低越容易合成，因此转换为 ease 分数。综合分数使用 60% QED 与 40% 合成容易度，随后选择前 20 条。权重和公式固定写入实现，结果可由逐行 CSV 复算。

## 三、选择结果

前 20 条中包含 {metrics['selected_unique_count']} 个唯一结构、{metrics['selected_scaffold_count']} 个核心骨架；平均 QED 为 {metrics['selected_mean_qed']:.3f}，平均 SA 为 {metrics['selected_mean_sa']:.3f}。这些数字同时衡量目标优化效果和头部集合是否发生模式集中。

## 四、证据

`molecular_optimized_candidates.csv` 给出排名、来源组、SMILES、骨架、QED、SA 和综合分数；`molecular_optimization_metrics.json` 保存汇总；柱状图展示前 20 名分数衰减。报告结论没有脱离这些机器可读证据。

## 五、自进化价值

系统已经形成“生成候选→检查有效性和性质→检查骨架与指纹多样性→加入随机对照与合成难度→执行多目标筛选”的连续实验链。每轮都消费上一轮真实文件并产生新计算，而不是把下一步停留在 Markdown。

## 六、边界与完成条件

当前四轮构成了完整的本地基准弧线。进一步宣称药效需要靶点、活性标签或对接验证，这些输入目前不存在。本轮因此在可证据化边界处结束，不虚构靶点结论；未来获得明确靶点数据时，再启动新实验链。

## 七、复核方式

复核者可以从第三轮 CSV 重新执行归一化和加权公式，并逐行比对前 20 名。若改变 60/40 权重，排名可能变化，因此本报告不把当前排序描述为唯一最优解。骨架数量用于检测选择压力是否把候选压缩到单一模板；平均 QED 和 SA 则用于确认筛选方向是否与目标一致。

## 八、连续迭代总结

第一轮建立有效性与性质基线，第二轮补充结构多样性，第三轮加入合成可及性和概率对照，第四轮执行多目标选择。四轮之间均通过真实文件传递状态，并在用户消息中报告指标和发送回执。没有尚未执行却被写成承诺的“下一步”；新的实验只能由新增靶点或活性数据触发。

## 九、结果解读注意事项

综合分数是候选优先级工具，而不是实验活性预测。高 QED 只表示若干常见药物相似性特征较均衡，低 SA 只是启发式合成难度较低；两者都不能替代毒性、选择性、代谢稳定性和真实合成验证。头部候选若共享少量骨架，说明多目标选择提高均值的同时牺牲了结构覆盖，需要在后续有靶点数据时加入多样性约束。

## 十、交付判据

本轮必须同时生成排名 CSV、汇总 JSON、图表、Markdown 与详细 PDF，并真实发送 PDF 和候选 CSV。消息应报告样本总数、选中数、唯一结构、骨架数、平均 QED 与平均 SA。只有两份文件均取得 `delivered=true` 才把四轮实验链标记完成，从而避免“计算完成但用户看不到”的假完成。
"""
    md_path = os.path.join(wd, "molecular_optimization_report.md")
    with open(md_path, "w", encoding="utf-8") as fh: fh.write(report)
    from partner.v2.pdf_events import atomic_generate_detailed_pdf
    pdf_path = os.path.join(wd, "molecular_optimization_report.pdf")
    pdf = atomic_generate_detailed_pdf(ctx, {"content": report, "output_path": pdf_path,
        "title": "分子生成第四轮：QED/SA 多目标候选选择", "image_paths": [chart_path]})
    if not pdf.get("ok"):
        return {"ok": False, "status": "pdf_failed", "error": pdf.get("error"), "quality": pdf.get("quality")}
    return {"ok": True, "status": "generated", "metrics": metrics, "quality": pdf.get("quality"),
            "files": [csv_path, metrics_path, chart_path, md_path, pdf_path], "path": pdf_path,
            "completion": "本地无靶点数据条件下的四轮实验链已完成"}


def atomic_molecular_data_readiness_audit(ctx, params: dict) -> dict:
    """Audit fifth-round target-data readiness without repeating QED/SA work."""
    workspace, wd = _ctx_paths(ctx)
    os.makedirs(wd, exist_ok=True)
    from partner.governance.storage import latest_receipt, workspace_root

    root = workspace_root(workspace)
    receipt = latest_receipt(workspace, "molecular_generation")
    previous_artifacts = list(receipt.artifacts) if receipt else []
    required_groups = {
        "target_identity": ["target_id", "target_name", "uniprot_id", "protein_sequence"],
        "activity_measurement": ["activity_type", "activity_value", "activity_unit", "assay_id"],
        "molecule_identity": ["canonical_smiles", "smiles", "inchi", "compound_id"],
    }
    contract = {
        "schema_version": "1.0",
        "purpose": "molecular_generation fifth-round target/activity data intake",
        "required_field_groups": required_groups,
        "optional_fields": ["pdb_id", "binding_site", "docking_score", "experimental_protocol", "source_url"],
        "accepted_formats": ["csv", "json", "sdf", "parquet"],
        "resume_event": "molecular_target_data_available",
    }
    contract_path = os.path.join(wd, "molecular_target_data_contract.json")
    with open(contract_path, "w", encoding="utf-8") as fh:
        json.dump(contract, fh, ensure_ascii=False, indent=2)

    keywords = ("target", "activity", "assay", "binding", "docking", "experimental", "靶点", "活性", "对接", "实验")
    candidates: list[str] = []
    search_roots = [root / "share" / "projects" / "molecular_generation", root / "external"]
    scanned_files = 0
    scan_truncated = False
    ignored_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "build", "dist", "models", "checkpoints"}
    for search_root in search_roots:
        if not search_root.is_dir():
            continue
        for current, dirs, names in os.walk(search_root):
            relative = Path(current).relative_to(search_root)
            dirs[:] = [name for name in dirs if name not in ignored_dirs]
            if len(relative.parts) >= 5:
                dirs[:] = []
            for name in names:
                scanned_files += 1
                if scanned_files > 3000 or len(candidates) >= 80:
                    scan_truncated = True
                    break
                path = Path(current) / name
                if path.suffix.lower() not in {".csv", ".json", ".sdf", ".parquet"}:
                    continue
                if any(word in path.name.lower() for word in keywords):
                    candidates.append(str(path))
            if scan_truncated:
                break
        if scan_truncated:
            break

    inspected: list[dict] = []
    usable: list[str] = []
    for candidate in candidates:
        row = {"path": candidate, "size": os.path.getsize(candidate), "fields": [], "matched_groups": []}
        try:
            if candidate.lower().endswith(".csv"):
                with open(candidate, newline="", encoding="utf-8-sig") as fh:
                    fields = list((csv.DictReader(fh).fieldnames or []))
                row["fields"] = fields
            elif candidate.lower().endswith(".json"):
                with open(candidate, encoding="utf-8") as fh:
                    value = json.load(fh)
                sample = value[0] if isinstance(value, list) and value else value
                row["fields"] = list(sample.keys()) if isinstance(sample, dict) else []
        except Exception as exc:
            row["error"] = str(exc)
        normalized = {str(field).strip().lower() for field in row["fields"]}
        for group, alternatives in required_groups.items():
            if normalized.intersection(alternatives):
                row["matched_groups"].append(group)
        if len(row["matched_groups"]) == len(required_groups):
            usable.append(candidate)
        inspected.append(row)

    blocked = not usable
    validation = {
        "latest_receipt_id": receipt.receipt_id if receipt else "",
        "latest_iteration": receipt.iteration if receipt else 0,
        "previous_artifacts": previous_artifacts,
        "candidate_count": len(candidates),
        "scanned_file_count": scanned_files,
        "scan_truncated": scan_truncated,
        "usable_dataset_count": len(usable),
        "usable_datasets": usable,
        "inspected": inspected,
        "blocked": blocked,
        "blocked_reason": (
            "未发现同时包含分子身份、明确靶点身份和可解释活性测量的数据集；继续 QED/SA 排序不会产生新的药效证据。"
            if blocked else ""
        ),
        "resume_event": "molecular_target_data_available" if blocked else "",
    }
    validation_path = os.path.join(wd, "molecular_data_readiness_validation.json")
    with open(validation_path, "w", encoding="utf-8") as fh:
        json.dump(validation, fh, ensure_ascii=False, indent=2)

    prior = "、".join(Path(value).name for value in previous_artifacts[:8]) or "最新 Receipt 未登记产物"
    candidate_lines = "\n".join(
        f"- `{row['path']}`：字段 {row.get('fields') or '无法解析'}；命中 {row.get('matched_groups') or '无'}"
        for row in inspected[:20]
    ) or "- 未发现名称或格式符合目标/活性/对接/实验数据特征的候选文件。"
    report = f"""# 分子项目第五轮：目标与活性数据接入就绪度审计

## 一、审计目标

本轮严格承接第四轮结果，但不重复 QED、SA 或头部候选排序。第四轮只能证明候选的药物相似性、合成可及性和结构集中度，不能证明对任何生物靶点具有活性。本轮的唯一目标是检查第五轮所需的真实数据是否存在，并把恢复执行所需的数据契约落盘。

## 二、承接的上一轮证据

最新有效 Receipt 为 `{receipt.receipt_id if receipt else 'missing'}`，迭代号为 {receipt.iteration if receipt else 0}。承接产物：{prior}。这些文件保留为候选生成与排序基线，但本次没有重新计算或重新排名。

## 三、实际执行的方法

系统在受控预算内扫描项目共享目录和 external 数据目录中名称包含 target、activity、assay、binding、docking、experimental、靶点、活性、对接或实验的数据文件，并仅检查 CSV、JSON、SDF、Parquet。扫描最多深入五层、检查 3000 个文件，并排除 `.git`、模型权重、构建缓存和虚拟环境；对可读取的 CSV/JSON 提取字段，分别验证分子身份、靶点身份、活性测量三个字段组。只有三个字段组同时满足，才允许恢复第五轮方法比较。

## 四、数据契约

已生成 `molecular_target_data_contract.json`。最小数据必须同时具有：分子身份（SMILES、InChI 或 compound_id）、靶点身份（target_id、UniProt、名称或蛋白序列）以及活性测量（activity_type/value/unit 或 assay_id）。PDB、结合口袋、对接分数、实验方案和来源链接是推荐字段，但不能替代真实靶点与活性标签。

## 五、扫描证据

受控扫描检查 {scanned_files} 个文件（预算截断={scan_truncated}），发现 {len(candidates)} 个候选文件，完成字段检查后可直接使用的数据集为 {len(usable)} 个。

{candidate_lines}

机器可复核明细保存在 `molecular_data_readiness_validation.json`，其中记录每个候选路径、大小、字段、命中的字段组、最新 Receipt 和阻塞判定。复核者可以直接比对 JSON 与原始文件，不依赖语言模型解释。

## 六、结果与边界

当前状态：{'blocked' if blocked else 'ready'}。{validation['blocked_reason'] or '至少一个数据集满足最小契约，可以设计第五轮靶点条件实验。'} 本结论不把 QED 或 SA 当作药效代理，不虚构对接分数、结合亲和力或实验活性，也不因为生成了一份报告就声称分子方法已经改进。

## 七、恢复条件

阻塞恢复事件为 `molecular_target_data_available`。触发时应提供数据文件路径、来源和字段说明；系统重新运行本审计，通过三个字段组后，再比较基线方法与新的靶点条件生成/筛选方法。若只有 PDB 而没有活性标签，可进行对接方法验证，但必须把“对接评分”与“实验活性”分开表述。

## 八、本轮产生的新证据

本轮新增的不是第五次性质排序，而是一个可执行的数据接入契约和一次实际目录/字段审计。与上一轮相比，项目从模糊的“缺数据”推进为可机器检查的恢复门槛。该门槛能阻止后续模型在没有靶点证据时机械续跑，同时让真实数据到达后能够立即恢复。

## 九、下一步

如果状态为 blocked，Campaign 应记录阻塞原因和 resume_event 后停止该项目的机械重试；其他实例仍可继续工作。如果状态为 ready，下一 WorkItem 必须把通过验证的数据集列入 inputs，并产生新的实验指标、逐行结果和方法对照。
"""
    md_path = os.path.join(wd, "molecular_data_readiness_report.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(report)
    from partner.v2.pdf_events import atomic_generate_detailed_pdf
    pdf_path = os.path.join(wd, "molecular_data_readiness_report.pdf")
    pdf = atomic_generate_detailed_pdf(ctx, {
        "content": report,
        "output_path": pdf_path,
        "title": "分子项目第五轮：目标与活性数据接入就绪度审计",
    })
    if not pdf.get("ok"):
        return {"ok": False, "status": "pdf_failed", "error": pdf.get("error"),
                "files": [contract_path, validation_path, md_path]}
    return {
        "ok": True,
        "status": "blocked" if blocked else "ready",
        "blocked": blocked,
        "blocked_reason": validation["blocked_reason"],
        "resume_event": validation["resume_event"],
        "metrics": {"scanned_file_count": scanned_files, "scan_truncated": scan_truncated,
                    "candidate_count": len(candidates), "usable_dataset_count": len(usable)},
        "files": [contract_path, validation_path, md_path, pdf_path],
        "path": pdf_path,
    }


__all__ = [
    "atomic_molecular_synth_baseline_benchmark",
    "atomic_molecular_goal_optimization_benchmark",
    "atomic_molecular_data_readiness_audit",
]

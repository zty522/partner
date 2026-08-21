#!/usr/bin/env python3
"""Enrichment Agent Wrapper — 通路富集分析（ORA via Enrichr / gseapy）。

用法:
  python enrichment_wrapper.py --genes <基因列表文件或逗号分隔> --gene-set KEGG_2021_Human       --output <输出目录> [--top 20]

输入: 基因列表（文件一行一个基因，或逗号分隔字符串）
输出: enrichment_report.md（中文报告）+ enrichment_results.csv（全量结果）
运行环境: cytobridge conda env（已装 gseapy）
"""
import argparse
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="通路富集分析（ORA）")
    parser.add_argument("--genes", required=True, help="基因列表：文件路径或逗号分隔字符串")
    parser.add_argument("--gene-set", default="KEGG_2021_Human", help="Enrichr 基因集库（默认 KEGG_2021_Human）")
    parser.add_argument("--output", required=True, help="输出目录")
    parser.add_argument("--top", type=int, default=20, help="报告展示的 top 通路数")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    # 解析基因列表
    if os.path.exists(args.genes):
        with open(args.genes, encoding="utf-8") as f:
            genes = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    else:
        genes = [g.strip() for g in args.genes.replace("，", ",").split(",") if g.strip()]
    if not genes:
        print(json.dumps({"ok": False, "error": "空基因列表"}, ensure_ascii=False))
        return 1

    import gseapy as gp

    try:
        enr = gp.enrichr(gene_list=genes, gene_sets=[args.gene_set], organism="human")
        df = enr.results
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"enrichr 调用失败: {exc}"}, ensure_ascii=False))
        return 1

    total = len(df)
    sig = df[df["Adjusted P-value"] < 0.05] if total else df
    top = sig.head(args.top) if len(sig) else df.head(args.top)

    # CSV 全量
    csv_path = os.path.join(args.output, "enrichment_results.csv")
    df.to_csv(csv_path, index=False)

    # MD 报告
    report_path = os.path.join(args.output, "enrichment_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# 通路富集分析报告\n\n")
        f.write(f"- 基因集库: {args.gene_set}\n")
        f.write(f"- 输入基因数: {len(genes)}\n")
        f.write(f"- 命中通路数: {total}\n")
        f.write(f"- 显著通路数 (adjusted p < 0.05): {len(sig)}\n\n")
        if len(top):
            f.write("## Top 显著通路\n\n")
            f.write("| 通路 | 重叠 | P-value | Adjusted P-value | Odds Ratio | 基因 |\n")
            f.write("|------|------|---------|------------------|------------|------|\n")
            for _, row in top.iterrows():
                f.write(f"| {row['Term']} | {row['Overlap']} | {row['P-value']:.2e} | "
                        f"{row['Adjusted P-value']:.2e} | {row['Odds Ratio']:.1f} | {row['Genes']} |\n")
            f.write("\n## 说明\n\n- 全量结果见 `enrichment_results.csv`。\n")
            f.write("- 通路名以 Enrichr 基因为准，部分缩写为英文。\n")
        else:
            f.write("未发现显著富集通路（adjusted p < 0.05）。\n")

    result = {
        "ok": True,
        "genes": len(genes),
        "gene_set": args.gene_set,
        "total_terms": total,
        "significant": int(len(sig)),
        "report": report_path,
        "csv": csv_path,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

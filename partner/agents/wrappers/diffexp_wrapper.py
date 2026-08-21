#!/usr/bin/env python3
"""Differential Expression Agent Wrapper — 单细胞差异表达分析（scanpy rank_genes_groups, Wilcoxon）。

用法:
  python diffexp_wrapper.py --h5ad <input.h5ad> --groupby <obs列> --output <输出目录> [--reference <参考组>]

输入: h5ad 单细胞数据（obs 含分组列）
输出: differential_expression_report.md（中文报告）+ deg_results.csv（全量差异基因）
运行环境: cytobridge conda env（scanpy 1.11）
"""
import argparse
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="单细胞差异表达分析")
    parser.add_argument("--h5ad", required=True, help="h5ad 路径")
    parser.add_argument("--groupby", required=True, help="obs 分组列（如 cell_type / condition）")
    parser.add_argument("--output", required=True, help="输出目录")
    parser.add_argument("--reference", default="", help="参考组（默认每组独立对比所有其他组）")
    args = parser.parse_args()

    if not os.path.exists(args.h5ad):
        print(json.dumps({"ok": False, "error": f"h5ad 不存在: {args.h5ad}"}, ensure_ascii=False))
        return 1
    os.makedirs(args.output, exist_ok=True)

    import scanpy as sc
    import pandas as pd

    adata = sc.read_h5ad(args.h5ad)
    if args.groupby not in adata.obs.columns:
        print(json.dumps({"ok": False, "error": f"obs 中无分组列: {args.groupby}（可选: {list(adata.obs.columns)[:8]}）"}, ensure_ascii=False))
        return 1

    groups = adata.obs[args.groupby].astype(str).unique().tolist()
    sc.tl.rank_genes_groups(adata, groupby=args.groupby, method="wilcoxon", use_raw=False)

    # 收集所有组的结果
    frames = []
    for g in groups:
        try:
            df = sc.get.rank_genes_groups_df(adata, group=g)
            df["group"] = g
            frames.append(df)
        except Exception:
            continue
    if not frames:
        print(json.dumps({"ok": False, "error": "rank_genes_groups 未产生结果"}, ensure_ascii=False))
        return 1
    all_df = pd.concat(frames, ignore_index=True)
    csv_path = os.path.join(args.output, "deg_results.csv")
    all_df.to_csv(csv_path, index=False)

    sig = all_df[all_df["pvals_adj"] < 0.05]
    # 每组 top 基因
    report_path = os.path.join(args.output, "differential_expression_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# 差异表达分析报告\n\n")
        f.write(f"- 细胞数: {adata.n_obs}, 基因数: {adata.n_vars}\n")
        f.write(f"- 分组列: {args.groupby}（组: {', '.join(groups)}）\n")
        f.write(f"- 显著差异基因总数 (adj p < 0.05): {len(sig)}\n\n")
        for g in groups:
            sub = all_df[all_df["group"] == g].sort_values("pvals_adj")
            top = sub.head(15)
            f.write(f"## {g} 组 Top 差异基因\n\n")
            f.write("| 基因 | log2FC | p-value | adj p-value |\n")
            f.write("|------|--------|---------|-------------|\n")
            for _, row in top.iterrows():
                f.write(f"| {row['names']} | {row['logfoldchanges']:.2f} | {row['pvals']:.2e} | {row['pvals_adj']:.2e} |\n")
            f.write("\n")
        f.write("## 说明\n\n- Wilcoxon 秩和检验（scanpy rank_genes_groups）。\n")
        f.write("- 全量结果见 `deg_results.csv`。\n")

    print(json.dumps({"ok": True, "cells": int(adata.n_obs), "genes": int(adata.n_vars),
                      "groups": groups, "significant": int(len(sig)),
                      "report": report_path, "csv": csv_path}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

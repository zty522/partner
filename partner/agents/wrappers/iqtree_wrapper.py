#!/usr/bin/env python3
"""IQ-TREE Agent Wrapper — 系统发育树构建（最大似然法）。

用法:
  python iqtree_wrapper.py --fasta <多序列比对.fasta> --output <输出目录> [--bootstrap 1000]

输入: 多序列比对 FASTA
输出: phylogeny_report.md（中文报告）+ *.treefile（Newick 树）
运行环境: IQ-TREE 2.4.0 官方二进制（external/tools/iqtree/.../bin/iqtree2）
"""
import argparse
import glob
import json
import os
import subprocess
import sys

IQTREE_BIN = None
for cand in glob.glob("/mnt/e/work/partner_workspace/external/tools/iqtree/*/bin/iqtree2"):
    IQTREE_BIN = cand
    break
if not IQTREE_BIN:
    IQTREE_BIN = "/mnt/e/work/partner_workspace/external/tools/iqtree/iqtree-2.4.0-Linux-intel/bin/iqtree2"


def main() -> int:
    parser = argparse.ArgumentParser(description="最大似然系统发育树")
    parser.add_argument("--fasta", required=True, help="多序列比对 FASTA 路径")
    parser.add_argument("--output", required=True, help="输出目录")
    parser.add_argument("--bootstrap", type=int, default=1000, help="bootstrap 重抽样次数")
    parser.add_argument("--threads", type=int, default=4, help="线程数")
    args = parser.parse_args()

    if not os.path.exists(args.fasta):
        print(json.dumps({"ok": False, "error": f"fasta 不存在: {args.fasta}"}, ensure_ascii=False))
        return 1
    os.makedirs(args.output, exist_ok=True)

    cmd = [IQTREE_BIN, "-s", args.fasta, "-m", "MFP", "-B", str(args.bootstrap),
           "-T", str(args.threads), "--prefix", os.path.join(args.output, "tree")]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        print(json.dumps({"ok": False, "error": (r.stdout or r.stderr)[-500:]}, ensure_ascii=False))
        return 1

    treefile = os.path.join(args.output, "tree.treefile")
    logfile = os.path.join(args.output, "tree.log")

    # 从 log 提取最优模型与树信息
    best_model = ""
    if os.path.exists(logfile):
        for line in open(logfile, errors="replace"):
            if "Best-fit model" in line:
                best_model = line.strip()
                break

    report_path = os.path.join(args.output, "phylogeny_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 系统发育分析报告\n\n")
        f.write(f"- 输入: {os.path.basename(args.fasta)}\n")
        f.write(f"- bootstrap: {args.bootstrap}\n")
        f.write(f"- 最优模型: {best_model}\n")
        f.write(f"- 树文件: `tree.treefile`（Newick 格式）\n\n")
        f.write("## 说明\n\n- 用 IQ-TREE 2 最大似然法建树，ModelFinder (MFP) 自动选择最优替代模型。\n")
        f.write("- 支持后续用 FigTree / iTOL 可视化。\n")

    result = {"ok": True, "treefile": treefile, "report": report_path, "best_model": best_model}
    if os.path.exists(treefile):
        with open(treefile) as f:
            result["newick"] = f.read().strip()[:300]
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

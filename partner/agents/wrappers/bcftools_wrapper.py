#!/usr/bin/env python3
"""BCFtools Agent Wrapper — 变异位点分析（VCF 统计与过滤）。

用法:
  python bcftools_wrapper.py --vcf <input.vcf> --output <输出目录> [--min-qual 20] [--min-dp 10]

输入: VCF 文件（含样本基因型）
输出: variant_report.md（中文报告）+ filtered.vcf.gz（过滤后 VCF）
运行环境: bcftools 1.19（external/tools/bcftools/usr/bin/bcftools）
"""
import argparse
import json
import os
import subprocess
import sys

BCFTOOLS = "/mnt/e/work/partner_workspace/external/tools/bcftools/usr/bin/bcftools"
LIB_DIR = "/mnt/e/work/partner_workspace/external/tools/bcftools/usr/lib/x86_64-linux-gnu"


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = LIB_DIR + ":" + env.get("LD_LIBRARY_PATH", "")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)


def main() -> int:
    parser = argparse.ArgumentParser(description="VCF 变异位点统计与过滤")
    parser.add_argument("--vcf", required=True, help="输入 VCF 路径")
    parser.add_argument("--output", required=True, help="输出目录")
    parser.add_argument("--min-qual", type=float, default=20)
    parser.add_argument("--min-dp", type=int, default=10)
    args = parser.parse_args()

    if not os.path.exists(args.vcf):
        print(json.dumps({"ok": False, "error": f"VCF 不存在: {args.vcf}"}, ensure_ascii=False))
        return 1
    os.makedirs(args.output, exist_ok=True)

    # 1. 统计
    r = run([BCFTOOLS, "stats", args.vcf])
    stats_text = r.stdout if r.returncode == 0 else ""
    n_snps = n_indels = n_sites = n_samples = 0
    for line in stats_text.splitlines():
        if line.startswith("SN") and "number of SNPs" in line:
            n_snps = int(line.split(":")[-1].strip())
        elif line.startswith("SN") and "number of indels" in line:
            n_indels = int(line.split(":")[-1].strip())
        elif line.startswith("SN") and "number of records" in line:
            n_sites = int(line.split(":")[-1].strip())
        elif line.startswith("SN") and "number of samples" in line:
            n_samples = int(line.split(":")[-1].strip())

    # 2. 过滤
    filtered = os.path.join(args.output, "filtered.vcf.gz")
    fr = run([BCFTOOLS, "view", "-f", "PASS,.",
              "-i", f"QUAL>={args.min_qual} && INFO/DP>={args.min_dp}",
              "-Oz", "-o", filtered, args.vcf])
    if fr.returncode == 0:
        run([BCFTOOLS, "index", filtered])

    # 3. 报告
    report_path = os.path.join(args.output, "variant_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# 变异位点分析报告\n\n")
        f.write(f"- 样本数: {n_samples}\n")
        f.write(f"- 变异位点总数: {n_sites}\n")
        f.write(f"- SNP 数: {n_snps}\n")
        f.write(f"- Indel 数: {n_indels}\n")
        f.write(f"- 过滤条件: QUAL >= {args.min_qual}, DP >= {args.min_dp}\n")
        f.write(f"- 过滤后 VCF: `filtered.vcf.gz`\n\n")
        f.write("## 说明\n\n- 基于 bcftools 1.19（stats + view 过滤）。\n")
        f.write("- 若需完整变异调用（mpileup），提供 BAM + 参考序列后扩展。\n")

    print(json.dumps({"ok": True, "samples": n_samples, "sites": n_sites,
                      "snps": n_snps, "indels": n_indels, "report": report_path,
                      "filtered_vcf": filtered}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

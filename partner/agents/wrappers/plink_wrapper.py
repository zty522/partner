#!/usr/bin/env python3
"""PLINK Agent Wrapper — 全基因组关联分析（GWAS，病例对照关联检验）。

用法:
  python plink_wrapper.py --prefix <ped/map 或 bed/bim/fam 前缀> --output <输出目录>

输入: PLINK 格式数据（test.ped + test.map，或 test.bed/.bim/.fam）
输出: gwas_report.md（中文报告）+ gwas.assoc（全量关联表）
运行环境: PLINK 1.9 官方二进制（external/tools/plink/plink）
"""
import argparse
import json
import os
import subprocess
import sys

PLINK_BIN = "/mnt/e/work/partner_workspace/external/tools/plink/plink"


def main() -> int:
    parser = argparse.ArgumentParser(description="GWAS 病例对照关联分析")
    parser.add_argument("--prefix", required=True, help="PLINK 数据前缀（不含扩展名）")
    parser.add_argument("--output", required=True, help="输出目录")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)
    out_prefix = os.path.join(args.output, "gwas")

    cmd = [PLINK_BIN, "--file", args.prefix, "--assoc", "--adjust", "--out", out_prefix]
    # 若存在 bed/bim/fam 则用 bfile 模式
    if os.path.exists(args.prefix + ".bed"):
        cmd = [PLINK_BIN, "--bfile", args.prefix, "--assoc", "--adjust", "--out", out_prefix]

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        print(json.dumps({"ok": False, "error": (r.stdout or r.stderr)[-500:]}, ensure_ascii=False))
        return 1

    assoc_path = out_prefix + ".assoc"
    adj_path = out_prefix + ".assoc.adjusted"
    if not os.path.exists(assoc_path):
        print(json.dumps({"ok": False, "error": "未生成 assoc 结果"}, ensure_ascii=False))
        return 1

    # 解析结果
    rows = []
    with open(assoc_path) as f:
        header = f.readline().strip().split()
        for line in f:
            parts = line.strip().split()
            if len(parts) >= len(header):
                rows.append(dict(zip(header, parts)))
    sig = [row for row in rows if float(row.get("P", 1)) < 0.05]

    # 报告
    report_path = os.path.join(args.output, "gwas_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# GWAS 关联分析报告\n\n")
        f.write(f"- 变异数: {len(rows)}\n")
        f.write(f"- 显著位点数 (P < 0.05): {len(sig)}\n\n")
        if sig:
            f.write("## 显著位点\n\n")
            f.write("| SNP | CHR | BP | A1 | F_A | F_U | OR | P |\n")
            f.write("|-----|-----|----|----|-----|-----|----|---|\n")
            for row in sorted(sig, key=lambda x: float(x["P"]))[:30]:
                f.write(f"| {row.get('SNP','')} | {row.get('CHR','')} | {row.get('BP','')} | "
                        f"{row.get('A1','')} | {row.get('F_A','')} | {row.get('F_U','')} | "
                        f"{row.get('OR','')} | {row.get('P','')} |\n")
            f.write("\n## 说明\n\n- 全量结果见 `gwas.assoc`，校正后 P 值见 `gwas.assoc.adjusted`。\n")
        else:
            f.write("未发现显著关联位点（P < 0.05）。\n")

    print(json.dumps({"ok": True, "variants": len(rows), "significant": len(sig),
                      "report": report_path, "assoc": assoc_path}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""L3 生信 Agent 实测（P4）— 5 个 agent 真实数据跑通验证。

运行: python3 tests/integration/l3_agents.py
数据: 模拟数据即时生成（VCF/fasta/ped/h5ad/基因列表）
"""
import json
import os
import random
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WRAPPERS = os.path.join(REPO, "partner", "agents", "wrappers")
PY = sys.executable

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(cond)


def section(title):
    print(f"\n===== {title} =====")


def run_wrapper(wrapper, args, timeout=600):
    r = subprocess.run([PY, os.path.join(WRAPPERS, wrapper)] + args,
                       capture_output=True, text=True, timeout=timeout)
    out = ""
    for line in r.stdout.splitlines():
        try:
            out = json.loads(line)
            break
        except Exception:
            continue
    return r, out


def main():
    tmp = tempfile.mkdtemp(prefix="l3_")
    random.seed(7)

    # ── 1. enrichment（真实基因列表 → Enrichr API）───────────────
    section("1. enrichment 通路富集（真实 Enrichr API）")
    genes = ["TP53", "EGFR", "KRAS", "BRAF", "PIK3CA", "PTEN", "AKT1", "MTOR",
             "CDK4", "CDK6", "CCND1", "MYC", "MDM2", "RB1", "VEGFA"]
    gp = os.path.join(tmp, "genes.txt")
    open(gp, "w").write("\n".join(genes))
    out_dir = os.path.join(tmp, "enr_out")
    r, out = run_wrapper("enrichment_wrapper.py", ["--genes", gp, "--output", out_dir])
    check("wrapper 退出 0", r.returncode == 0, f"rc={r.returncode}")
    check("返回 ok", out.get("ok") is True, str(out.get("error", ""))[:80])
    check("命中通路 ≥ 100", out.get("total_terms", 0) >= 100, f"total={out.get('total_terms')}")
    check("显著通路 ≥ 1", out.get("significant", 0) >= 1, f"sig={out.get('significant')}")
    csv = os.path.join(out_dir, "enrichment_results.csv")
    md = os.path.join(out_dir, "enrichment_report.md")
    check("CSV 生成", os.path.exists(csv) and os.path.getsize(csv) > 200, f"{os.path.getsize(csv) if os.path.exists(csv) else 0}B")
    check("MD 报告生成", os.path.exists(md) and "通路" in open(md, encoding="utf-8").read()[:200])

    # ── 2. plink（模拟 GWAS：前 10 SNP 关联）─────────────────────
    section("2. plink GWAS（模拟数据，已知关联）")
    pdir = os.path.join(tmp, "plink_in")
    os.makedirs(pdir, exist_ok=True)
    with open(os.path.join(pdir, "test.ped"), "w") as f:
        for i in range(1, 31):
            pheno = 2 if i <= 15 else 1
            geno = []
            for s in range(20):
                # 前 10 SNP 与表型关联（case 更多杂合），后 10 随机
                if s < 10 and pheno == 2:
                    g = random.choice(["A C", "A C", "A A"])
                else:
                    g = random.choice(["A A", "A C", "C C"])
                geno.append(g)
            f.write(f"fam{i} ind{i} 0 0 1 {pheno} " + " ".join(geno) + "\n")
    with open(os.path.join(pdir, "test.map"), "w") as f:
        for s in range(20):
            f.write(f"1 rs{s:04d} 0 {1000 + s * 100}\n")
    out_dir = os.path.join(tmp, "plink_out")
    r, out = run_wrapper("plink_wrapper.py", ["--prefix", os.path.join(pdir, "test"), "--output", out_dir])
    check("wrapper 退出 0", r.returncode == 0, f"rc={r.returncode}")
    check("返回 ok", out.get("ok") is True, str(out.get("error", ""))[:80])
    check("变异数 = 20", out.get("variants") == 20, f"variants={out.get('variants')}")
    check("检出显著位点", out.get("significant", 0) >= 1, f"sig={out.get('significant')}")
    md = os.path.join(out_dir, "gwas_report.md")
    check("MD 报告生成", os.path.exists(md) and os.path.getsize(md) > 200)
    # 关联 SNP 应出现在显著列表（rs0000-rs0009 之一）
    if os.path.exists(md):
        content = open(md, encoding="utf-8").read()
        check("报告含关联 SNP 名", any(f"rs{i:04d}" in content for i in range(10)))

    # ── 3. iqtree（模拟 fasta 建树）──────────────────────────────
    section("3. iqtree 系统发育（模拟 fasta）")
    fasta = os.path.join(tmp, "seqs.fasta")
    seqs = {}
    random.seed(11)
    base = "".join(random.choice("ACGT") for _ in range(300))
    for i in range(8):
        s = list(base)
        for _ in range(i * 15):
            pos = random.randrange(300)
            s[pos] = random.choice("ACGT")
        seqs[f"sp{i}"] = "".join(s)
    with open(fasta, "w") as f:
        for name, s in seqs.items():
            f.write(f">{name}\n{s}\n")
    out_dir = os.path.join(tmp, "iq_out")
    r, out = run_wrapper("iqtree_wrapper.py", ["--fasta", fasta, "--output", out_dir], timeout=900)
    check("wrapper 退出 0", r.returncode == 0, f"rc={r.returncode}")
    check("返回 ok", out.get("ok") is True, str(out.get("error", ""))[:80])
    tree_path = out.get("treefile", "")
    check("Newick 树路径返回", bool(tree_path) and os.path.exists(tree_path), tree_path)
    if tree_path and os.path.exists(tree_path):
        t = open(tree_path, encoding="utf-8").read()
        check("树内容有效", "(" in t[:50], t[:40])
        check("树含 8 个物种", t.count("sp") >= 8)

    # ── 4. bcftools（模拟 VCF 统计+过滤）─────────────────────────
    section("4. bcftools 变体分析（模拟 VCF）")
    vcf = os.path.join(tmp, "sim.vcf")
    lines = ["##fileformat=VCFv4.2", "##contig=<ID=chr1,length=100000>",
             "##INFO=<ID=DP,Number=1,Type=Integer,Description='Depth'>",
             "#CHROM	POS	ID	REF	ALT	QUAL	FILTER	INFO	FORMAT	s1	s2"]
    random.seed(13)
    for i in range(10):
        qual = random.randint(10, 90)
        dp = random.randint(5, 40)
        lines.append(f"chr1	{1000+i*100}	rs{i:04d}	A	{'G' if i % 2 else 'T'}	{qual}	PASS	DP={dp}	GT:DP	0/1:{dp}	1/1:{dp}")
    open(vcf, "w").write("\n".join(lines) + "\n")
    out_dir = os.path.join(tmp, "bcf_out")
    r, out = run_wrapper("bcftools_wrapper.py", ["--vcf", vcf, "--output", out_dir])
    check("wrapper 退出 0", r.returncode == 0, f"rc={r.returncode}")
    check("返回 ok", out.get("ok") is True, str(out.get("error", ""))[:80])
    check("位点数 = 10", out.get("sites") == 10, f"sites={out.get('sites')}")
    check("样本数 = 2", out.get("samples") == 2, f"samples={out.get('samples')}")
    check("SNP 数 = 10", out.get("snps") == 10, f"snps={out.get('snps')}")
    filt = os.path.join(out_dir, "filtered.vcf.gz")
    md = os.path.join(out_dir, "variant_report.md")
    check("过滤 VCF 生成", os.path.exists(filt) and os.path.getsize(filt) > 0)
    check("MD 报告生成", os.path.exists(md) and os.path.getsize(md) > 200)

    # ── 5. diffexp（模拟 h5ad：2 群，前 50 基因差异）──────────────
    section("5. diffexp 差异表达（模拟 h5ad）")
    h5ad = os.path.join(tmp, "sim.h5ad")
    code = f"""
import numpy as np
import scanpy as sc
import pandas as pd
rng = np.random.default_rng(17)
n, g = 400, 800
X = rng.poisson(1.0, size=(n, g)).astype(float)
X[:200, :50] += rng.poisson(3.0, size=(200, 50))
adata = sc.AnnData(X=X, obs=pd.DataFrame({{"group": ["A"]*200 + ["B"]*200}}),
                   var=pd.DataFrame(index=[f"gene{{i}}" for i in range(g)]))
adata.write_h5ad("{h5ad}")
"""
    subprocess.run([PY, "-c", code], check=True, timeout=300)
    out_dir = os.path.join(tmp, "de_out")
    r, out = run_wrapper("diffexp_wrapper.py", ["--h5ad", h5ad, "--groupby", "group", "--output", out_dir])
    check("wrapper 退出 0", r.returncode == 0, f"rc={r.returncode}")
    check("返回 ok", out.get("ok") is True, str(out.get("error", ""))[:80])
    check("检出差异基因 ≥ 50", out.get("significant", 0) >= 50, f"sig={out.get('significant')}")
    csv = out.get("csv", "")
    md = out.get("report", "")
    check("CSV 生成", bool(csv) and os.path.exists(csv) and os.path.getsize(csv) > 200,
          os.path.basename(csv) if csv else "无")
    check("MD 报告生成", bool(md) and os.path.exists(md) and os.path.getsize(md) > 200,
          os.path.basename(md) if md else "无")

    # ── 汇总 ─────────────────────────────────────────────────────
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n===== 汇总: {passed}/{total} 通过 =====")
    failed = [n for n, ok, _ in RESULTS if not ok]
    if failed:
        print("失败项:", failed)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()

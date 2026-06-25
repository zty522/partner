"""CytoBridge wrapper — Partner's integration layer for single-cell trajectory inference.

This wrapper sets multiprocessing start_method to 'spawn' before launching
the cytobridge-agent CLI, avoiding OpenBLAS thread-pool inheritance issues
in Python 3.13+.

Installed as `cytobridge-wrapper` console_scripts entry point via pyproject.toml.
Called by Partner's AgentDispatcher according to partner/agents/manifests/cytobridge.json.

See also: https://github.com/JackkWangzh/CytoBridge-agent
"""

import argparse
import json
import os
import sys
import warnings
from datetime import datetime

warnings.filterwarnings("ignore")

# ── multiprocessing safeguard ──────────────────────────────────────
# The cytobridge-agent uses multiprocessing workers that fork from a
# parent where scanpy/numpy have imported OpenBLAS.  In Python 3.13,
# forking after OpenBLAS thread pool init causes feeder threads to
# fail.  Setting start_method to 'spawn' creates a fresh Python process
# for each worker, avoiding inherited OpenBLAS state entirely.
import multiprocessing

multiprocessing.set_start_method("spawn", force=True)

import numpy as np
import pandas as pd
import scanpy as sc
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ── Address space ──────────────────────────────────────────────────
# Raise RLIMIT_AS above Partner's default (2048 MB) — pancreas data
# densifying 8172×16400 needs more.
import resource as _res

try:
    _soft, _hard = _res.getrlimit(_res.RLIMIT_AS)
    if _soft < 4 * 1024 * 1024 * 1024:  # < 4 GB
        _res.setrlimit(_res.RLIMIT_AS, (8 * 1024 * 1024 * 1024, _hard))
except Exception:
    pass

# ── Constants ──────────────────────────────────────────────────────
N_TOP_GENES = 2000
N_PCS = 50
N_NEIGHBORS = 30
N_DCS = 15
HVG_FLAVOR = "seurat"
RANDOM_SEED = 42

# Memory safety: skip HVG on WSL with limited RAM, use all genes and
# let PCA handle dimensionality.
_SKIP_HVG = True
_MEMORY_SAFE = False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments matching the cytobridge-agent interface."""
    parser = argparse.ArgumentParser(
        description="Partner-integrated CytoBridge trajectory inference wrapper"
    )
    parser.add_argument("--input", "-i", required=True, help="Input h5ad file")
    parser.add_argument("--output", "-o", required=True, help="Output directory")
    parser.add_argument(
        "--question", "-q", default="", help="Analysis question (for logging)"
    )
    parser.add_argument("--device", default="cuda", help="Device (cuda/cpu)")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    return parser.parse_args(argv)


def analyze(args: argparse.Namespace) -> tuple[dict, list[str]]:
    """Run full single-cell trajectory analysis pipeline.

    Pipeline:
        1. Load h5ad (use raw.X without copy to avoid OOM)
        2. Normalize + log1p
        3. Use pre-computed PCA/UMAP if available, else compute in-house
        4. Neighbors + UMAP
        5. PAGA trajectory graph
        6. Diffusion Pseudotime (DPT)
        7. Trajectory-correlated genes
        8. Heatmap + figures
        9. Save processed h5ad + summary JSON
    """
    OUT = args.output
    os.makedirs(f"{OUT}/figures", exist_ok=True)
    os.makedirs(f"{OUT}/data", exist_ok=True)

    log: list[str] = []

    def info(msg: str) -> None:
        log.append(msg)
        if args.verbose:
            print(f"[cytobridge-wrapper] {msg}")

    # ── 1. Load ──
    info(f"Loading: {args.input}")
    adata = sc.read(args.input)
    info(f"Loaded: {adata.shape[0]} cells × {adata.shape[1]} genes")

    if adata.raw is not None:
        adata.X = adata.raw.X  # reference, no copy
        info("Using raw.X directly (no copy)")

    # ── 2. Preprocess ──
    info("Normalizing + log1p...")
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)

    # ── 3. Embeddings ──
    _has_embeddings = "X_pca" in adata.obsm and "X_umap" in adata.obsm
    if _has_embeddings:
        info("Using pre-computed X_pca and X_umap from h5ad")
        n_pcs = min(N_PCS, adata.obsm["X_pca"].shape[1])
        import gc as _gc

        _gc.collect()
    else:
        info("No pre-computed embeddings found, computing in-house...")
        import gc as _gc

        try:
            adata.X = (
                adata.X.toarray() if hasattr(adata.X, "toarray") else np.array(adata.X)
            )
            _gc.collect()
        except Exception as e:
            info(f"Densify failed: {e}")
            raise MemoryError(f"densify: {e}")
        n_pcs = min(20, N_PCS)
        sc.pp.highly_variable_genes(adata, n_top_genes=500, flavor="seurat")
        adata = adata[:, adata.var["highly_variable"]].copy()
        sc.pp.scale(adata, max_value=10)
        sc.tl.pca(adata, n_comps=n_pcs, svd_solver="arpack")
        sc.tl.umap(adata, min_dist=0.3, spread=1.0)

    adata_hvg = adata

    # Neighbors
    info("Computing neighbors...")
    sc.pp.neighbors(adata_hvg, n_neighbors=N_NEIGHBORS, n_pcs=min(30, n_pcs))
    sc.tl.umap(adata_hvg, min_dist=0.3, spread=1.0)

    adata.obsm["X_pca"] = adata_hvg.obsm["X_pca"]
    adata.obsm["X_umap"] = adata_hvg.obsm["X_umap"]
    adata.obsp["distances"] = adata_hvg.obsp["distances"]
    adata.obsp["connectivities"] = adata_hvg.obsp["connectivities"]

    # ── 4. Cell type key ──
    ct_key = None
    for candidate in [
        "cell_type",
        "free_annotation_v1",
        "cell_ontology_class_v1",
    ]:
        if candidate in adata.obs:
            ct_key = candidate
            break
    info(f"Using cluster key: {ct_key}")

    # ── 5. UMAP by cell type ──
    if ct_key:
        fig, ax = plt.subplots(figsize=(10, 8))
        sc.pl.umap(
            adata_hvg,
            color=ct_key,
            ax=ax,
            show=False,
            legend_loc="right margin",
            legend_fontsize=7,
            frameon=True,
            add_outline=True,
        )
        fig.savefig(f"{OUT}/figures/umap_celltype.png", bbox_inches="tight", dpi=150)
        plt.close()
        info("Saved: umap_celltype.png")

    # ── 6. PAGA ──
    info("Running PAGA...")
    sc.tl.paga(adata_hvg, groups=ct_key)
    sc.pl.paga(
        adata_hvg,
        threshold=0.05,
        layout="fr",
        node_size_scale=0.5,
        edge_width_scale=0.5,
        title="PAGA trajectory",
        show=False,
    )
    fig = plt.gcf()
    fig.savefig(f"{OUT}/figures/paga_trajectory.png", bbox_inches="tight", dpi=150)
    plt.close()
    info("Saved: paga_trajectory.png")

    # PAGA-initialized UMAP
    sc.tl.umap(adata_hvg, init_pos="paga", min_dist=0.3, spread=1.0)
    adata.obsm["X_umap_paga"] = adata_hvg.obsm["X_umap"]
    if ct_key:
        fig, ax = plt.subplots(figsize=(10, 8))
        sc.pl.umap(
            adata_hvg,
            color=ct_key,
            ax=ax,
            show=False,
            legend_loc="right margin",
            legend_fontsize=7,
            title="PAGA-initialized UMAP",
        )
        fig.savefig(f"{OUT}/figures/umap_paga_init.png", bbox_inches="tight", dpi=150)
        plt.close()
        info("Saved: umap_paga_init.png")

    # PAGA connectivity matrix
    paga_conn = adata_hvg.uns["paga"]["connectivities"].toarray()
    groups = list(adata_hvg.obs[ct_key].cat.categories) if ct_key else []
    if groups:
        paga_df = pd.DataFrame(paga_conn, index=groups, columns=groups)
        paga_df.to_csv(f"{OUT}/data/paga_connectivity_matrix.csv")

    # ── 7. Diffusion Pseudotime ──
    info("Running DPT...")
    root_keywords = [
        "ductal",
        "duct",
        "progenitor",
        "stem",
        "endocrine progenitor",
    ]
    root_idx = 0
    root_found = False
    if ct_key and ct_key in adata_hvg.obs:
        for kw in root_keywords:
            mask = adata_hvg.obs[ct_key].str.lower().str.contains(kw, na=False)
            if mask.sum() > 0:
                root_idx = np.where(mask.values)[0][0]
                root_found = True
                info(f"Root cell: {kw} cluster (index {root_idx})")
                break
        if not root_found:
            info("No ductal/progenitor found, using index 0 as root")

    adata_hvg.uns["iroot"] = root_idx
    sc.tl.dpt(adata_hvg, n_dcs=N_DCS)
    info(
        f"DPT range: {adata_hvg.obs['dpt_pseudotime'].min():.3f} - "
        f"{adata_hvg.obs['dpt_pseudotime'].max():.3f}"
    )
    adata.obs["dpt_pseudotime"] = adata_hvg.obs["dpt_pseudotime"]

    # DPT figures
    fig, axes = plt.subplots(1, 2, figsize=(16, 7))
    sc.pl.umap(
        adata_hvg,
        color="dpt_pseudotime",
        ax=axes[0],
        show=False,
        title="Diffusion Pseudotime",
        cmap="viridis",
    )
    sc.pl.umap(
        adata_hvg,
        color=ct_key,
        ax=axes[1],
        show=False,
        title="Cell Type",
        legend_loc="right margin",
        legend_fontsize=7,
    )
    fig.savefig(f"{OUT}/figures/dpt_pseudotime.png", bbox_inches="tight", dpi=150)
    plt.close()
    info("Saved: dpt_pseudotime.png")

    # DPT boxplot
    fig, ax = plt.subplots(figsize=(14, 5))
    order = (
        adata_hvg.obs.groupby(ct_key)["dpt_pseudotime"].median().sort_values().index
    )
    bp_data = [
        adata_hvg.obs.loc[adata_hvg.obs[ct_key] == ct, "dpt_pseudotime"].values
        for ct in order
    ]
    labels = [
        str(ct).replace("pancreatic ", "").replace(" cell", "")[:20] for ct in order
    ]
    bp = ax.boxplot(bp_data, labels=labels, patch_artist=True, showfliers=False)
    colors = plt.cm.viridis(np.linspace(0.2, 0.9, len(order)))
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
    ax.set_ylabel("Diffusion Pseudotime")
    ax.set_title(f"DPT by cell type (root index={root_idx})")
    plt.xticks(rotation=45, ha="right", fontsize=7)
    fig.tight_layout()
    fig.savefig(f"{OUT}/figures/dpt_by_celltype.png", bbox_inches="tight", dpi=150)
    plt.close()
    info("Saved: dpt_by_celltype.png")

    # ── 8. Trajectory-correlated genes ──
    info("Identifying trajectory-correlated genes...")
    dpt = adata_hvg.obs["dpt_pseudotime"].values
    X = (
        adata_hvg.X.toarray() if hasattr(adata_hvg.X, "toarray") else adata_hvg.X
    )
    correlations = []
    gene_names = adata_hvg.var_names.values
    for i in range(min(1000, X.shape[1])):
        corr = np.corrcoef(dpt, X[:, i])[0, 1]
        if not np.isnan(corr):
            correlations.append((gene_names[i], corr))
    correlations.sort(key=lambda x: abs(x[1]), reverse=True)

    top_genes_df = pd.DataFrame(
        correlations[:100], columns=["gene", "dpt_correlation"]
    )
    top_genes_df.to_csv(f"{OUT}/data/trajectory_correlated_genes.csv", index=False)

    # Heatmap
    top_n_genes = [g[0] for g in correlations[:30]]
    sc.tl.dendrogram(adata_hvg, groupby=ct_key, use_rep="X_pca")
    sc.pl.heatmap(
        adata_hvg,
        var_names=top_n_genes,
        groupby=ct_key,
        dendrogram=True,
        show=False,
        use_raw=False,
        figsize=(10, 8),
    )
    fig = plt.gcf()
    fig.savefig(
        f"{OUT}/figures/trajectory_gene_heatmap.png", bbox_inches="tight", dpi=150
    )
    plt.close()
    info("Saved: trajectory_gene_heatmap.png")

    # ── 9. Summary ──
    pt_by_ct = adata.obs.groupby(ct_key)["dpt_pseudotime"].mean().sort_values()
    info("Mean DPT by cell type (top 10):")
    for ct, val in pt_by_ct.head(10).items():
        info(f"  {str(ct)[:40]:40s} DPT={val:.3f}")

    # ── 10. Save processed h5ad ──
    adata_hvg.write(f"{OUT}/data/pancreas_trajectory_processed.h5ad")
    info("Saved processed h5ad")

    # ── 11. Summary JSON ──
    summary = {
        "n_cells": adata.shape[0],
        "n_genes": adata.shape[1],
        "n_clusters": len(groups),
        "clusters": [str(g) for g in groups],
        "paga_n_nodes": paga_conn.shape[0],
        "dpt_range": [
            float(adata.obs["dpt_pseudotime"].min()),
            float(adata.obs["dpt_pseudotime"].max()),
        ],
        "mean_dpt_by_cluster": {
            str(k): float(v) for k, v in pt_by_ct.items()
        },
        "root_cluster": (
            str(adata_hvg.obs[ct_key].iloc[root_idx]) if root_found else "index_0"
        ),
        "status": "completed",
        "timestamp": datetime.now().isoformat(),
    }
    with open(f"{OUT}/data/summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    info(f"Analysis complete. Results in: {OUT}")
    return summary, log


def main() -> int:
    """Entry point for ``cytobridge-wrapper`` console_scripts."""
    args = parse_args()
    try:
        summary, log = analyze(args)
        # stdout for Partner AgentDispatcher to parse
        print(f"[RESULT] Analysis completed: {args.output}")
        print(json.dumps(summary, default=str))
        # result.json for file-based consumption
        with open(f"{args.output}/result.json", "w") as f:
            json.dump(summary, f, indent=2, default=str)
        return 0
    except SystemExit:
        raise
    except BaseException as e:
        import traceback as _tb

        _tb.print_exc(file=sys.stderr)
        sys.stderr.write(f"[FATAL] {type(e).__name__}: {e}\n")
        sys.stderr.flush()
        print(f"[ERROR] {e}", flush=True)
        with open(f"{args.output}/error.log", "w") as f:
            f.write(f"{datetime.now().isoformat()} - {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())

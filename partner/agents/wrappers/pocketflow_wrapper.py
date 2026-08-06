#!/usr/bin/env python3
"""PocketFlow Molecular Generation — runs actual molecular generation from protein pocket PDB.

Usage:
    pocketflow-generate --pocket /path/to/pocket.pdb -n 100 -d cpu --output /tmp/out
"""

import argparse, json, os, subprocess, sys, time

POCKETFLOW_DIR = "/mnt/e/work/partner_workspace/external/PocketFlow"
DEFAULT_CKPT = os.path.join(POCKETFLOW_DIR, "ckpt", "ZINC-pretrained-255000.pt")

def _find_python():
    # Use CondaEnvManager to ensure pocketflow env is ready
    try:
        from partner.utils.conda_manager import CondaEnvManager
        mgr = CondaEnvManager()
        python = mgr.ensure("pocketflow", pip_packages=[
            "torch==2.5.0", "torch-scatter", "torch-geometric",
            "rdkit-pypi", "scipy", "pandas", "tqdm"
        ])
        if python:
            return python
    except Exception:
        pass
    # Fallback: check common paths
    for c in [
        os.path.expanduser("~/miniconda3/envs/pocketflow/bin/python"),
        os.path.expanduser("~/miniconda3/envs/cytobridge/bin/python"),
    ]:
        if os.path.isfile(c): return c
    return sys.executable

def main():
    parser = argparse.ArgumentParser(description="PocketFlow Molecular Generation")
    parser.add_argument("--pocket", "-pkt", required=True, help="PDB file of binding pocket")
    parser.add_argument("--ckpt", default=DEFAULT_CKPT, help="Model checkpoint")
    parser.add_argument("--num_gen", "-n", type=int, default=100)
    parser.add_argument("--device", "-d", default="cpu")
    parser.add_argument("--name", default="pocketflow_gen")
    parser.add_argument("--output", "-o", default="", help="Output directory")
    parser.add_argument("--format", default="json", choices=["json","text"])
    args = parser.parse_args()

    # Resolve relative paths against POCKETFLOW_DIR
    if not os.path.isabs(args.pocket) and not os.path.isfile(args.pocket):
        candidate = os.path.join(POCKETFLOW_DIR, args.pocket)
        if os.path.isfile(candidate):
            args.pocket = candidate

    if not os.path.isfile(args.pocket):
        print(json.dumps({"ok":False,"error":f"Pocket PDB not found: {args.pocket}"}))
        sys.exit(1)
    if not os.path.isfile(args.ckpt):
        print(json.dumps({"ok":False,"error":f"Checkpoint not found: {args.ckpt}"}))
        sys.exit(1)

    output_dir = args.output or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)
    gen_root = os.path.join(output_dir, "gen_results")
    os.makedirs(gen_root, exist_ok=True)

    # Write progress
    try:
        from agent_progress import ProgressWriter
        pw = ProgressWriter(output_dir)
        pw.update("setup", 5, "Initializing PocketFlow...", eta_seconds=args.num_gen*2)
    except ImportError:
        pw = None

    python_bin = _find_python()
    cmd = [python_bin, os.path.join(POCKETFLOW_DIR, "main_generate.py"),
           "-pkt", args.pocket, "--ckpt", args.ckpt,
           "-n", str(args.num_gen), "-d", args.device,
           "--root_path", gen_root, "--name", args.name,
           "--with_print", "True"]

    if pw: pw.update("generating", 15, f"Generating {args.num_gen} molecules...", eta_seconds=args.num_gen*2)

    start = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, cwd=POCKETFLOW_DIR)
    except subprocess.TimeoutExpired:
        if pw: pw.update("timeout", 50, "Timed out")
        print(json.dumps({"ok":False,"error":"Timed out after 1h"}))
        sys.exit(1)

    elapsed = time.time() - start

    if r.returncode != 0:
        if pw: pw.update("error", 50, f"Failed (exit {r.returncode})")
        print(json.dumps({"ok":False,"error":f"Exit {r.returncode}","stderr":r.stderr[-2000:]}))
        sys.exit(1)

    # Collect results
    output_files = []
    sdf_files = []
    for root, dirs, files in os.walk(gen_root):
        for fn in files:
            fp = os.path.join(root, fn)
            output_files.append(fp)
            if fn.endswith((".sdf",".mol",".mol2")): sdf_files.append(fp)

    # Also read generated.smi if it exists (more reliable than parsing stdout)
    smi_file = os.path.join(gen_root, args.name, "*", "generated.smi")
    import glob
    smi_candidates = glob.glob(smi_file)
    smiles = []
    if smi_candidates:
        with open(smi_candidates[0]) as sf:
            smiles = [l.strip() for l in sf if l.strip() and not l.startswith("#")]
    else:
        # Fallback: parse stdout
        for line in (r.stdout or "").split("\n"):
            line = line.strip()
            if line and len(line) > 3 and len(line) < 500 and any(c in line for c in "C=ON[]()"):
                smiles.append(line)

    count = len(smiles) or len(sdf_files)
    
    # Save SMILES to file (so downstream steps can read it)
    smi_output_path = os.path.join(output_dir, "generated_molecules.smi")
    try:
        with open(smi_output_path, "w") as sf:
            sf.write("\n".join(smiles))
        cmd_result["smi_file"] = smi_output_path
    except Exception:
        pass
    
    # Save CSV with count
    csv_path = os.path.join(output_dir, "generation_summary.csv")
    try:
        with open(csv_path, "w") as cf:
            cf.write(f"total_molecules,{count}\nelapsed_seconds,{elapsed:.0f}\n")
        cmd_result["csv_file"] = csv_path
    except Exception:
        pass
    
    out = f"[pocketflow] Generated {count} molecules in {elapsed:.0f}s\n"
    if smiles:
        out += "Molecules:\n" + "\n".join(smiles[:20])
        if len(smiles) > 20: out += f"\n... and {len(smiles)-20} more"
    else:
        out += "(no SMILES captured — check generated.smi in output dir)"
    out += f"\nValidity check: see metrics.dir in {gen_root}"
    if cmd_result.get("smi_file"):
        out += f"\nSMILES saved to: {cmd_result['smi_file']}"
        output_files.append(cmd_result["smi_file"])
    if cmd_result.get("csv_file"):
        output_files.append(cmd_result["csv_file"])


    # Generate molecular structure images using RDKit
    mol_images = []
    if smiles:
        try:
            from rdkit import Chem
            from rdkit.Chem import Draw, AllChem
            img_dir = os.path.join(output_dir, "mol_images")
            os.makedirs(img_dir, exist_ok=True)
            for i, smi in enumerate(smiles[:10], 1):
                try:
                    mol = Chem.MolFromSmiles(smi)
                    if mol:
                        AllChem.Compute2DCoords(mol)
                        img_path = os.path.join(img_dir, f"mol_{i:03d}.png")
                        Draw.MolToFile(mol, img_path, size=(400, 300))
                        mol_images.append(img_path)
                except Exception:
                    pass
            if mol_images:
                # Also copy images to output root for Harness artifact detection
                import shutil
                for img in mol_images:
                    try:
                        shutil.copy2(img, os.path.join(output_dir, os.path.basename(img)))
                    except Exception:
                        pass
                if pw: pw.update("images", 90, f"Generated {len(mol_images)} molecule images")
        except ImportError:
            if pw: pw.update("images", 90, "RDKit not available — skipping images")

    if pw: pw.done(f"Generated {count} molecules in {elapsed:.0f}s")

    print(json.dumps({"ok":True, "molecules": count, "output_dir": gen_root,
                       "sdf_files": sdf_files[:10], "smiles": smiles[:50],
                       "elapsed": round(elapsed,1), "content": out, "images": mol_images},
                     ensure_ascii=False))

if __name__ == "__main__":
    main()

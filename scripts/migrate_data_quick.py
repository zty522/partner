#!/usr/bin/env python3
"""Quick data migration - shallow copy of project files into workspace."""
import os, shutil, sys

workspace = sys.argv[1] if len(sys.argv) > 1 else "/mnt/e/work/partner_workspace"
proj = os.path.join(workspace, "projects")

mappings = [
    ("/mnt/e/work/年龄预测",    "age_prediction"),
    ("/mnt/e/work/鲍曼不动杆菌", "acinetobacter"),
    ("/mnt/e/work/配体设计",    "ligand_design"),
    ("/mnt/e/work/tom",         "tom"),
    ("/mnt/e/work/MOG",         "multi_omics_mog"),
]

DATA_EXTS = (".csv", ".tsv", ".xlsx", ".xls", ".txt", ".md", ".json", ".yaml", ".yml")
CODE_EXTS = (".py", ".r", ".R", ".ipynb")
SCRIPT_EXTS = (".sh", ".R", ".pl")

total = 0
for src_path, proj_name in mappings:
    if not os.path.exists(src_path):
        print(f"SKIP {proj_name}: source not found")
        continue
    dst = os.path.join(proj, proj_name)
    code_dir = os.path.join(dst, "code")
    data_dir = os.path.join(dst, "data")
    scripts_dir = os.path.join(dst, "scripts")
    for d in (code_dir, data_dir, scripts_dir):
        os.makedirs(d, exist_ok=True)

    count = 0
    for f in sorted(os.listdir(src_path)):
        fpath = os.path.join(src_path, f)
        if not os.path.isfile(fpath):
            continue
        size_mb = os.path.getsize(fpath) / (1024 * 1024)
        if size_mb > 5:
            continue  # skip files > 5MB

        if f.endswith(CODE_EXTS):
            shutil.copy2(fpath, os.path.join(code_dir, f))
            count += 1
        elif f.endswith(DATA_EXTS):
            shutil.copy2(fpath, os.path.join(data_dir, f))
            count += 1
        elif f.endswith(SCRIPT_EXTS):
            shutil.copy2(fpath, os.path.join(scripts_dir, f))
            count += 1

    # Also copy from code/ or data/ subdirs
    for sub in ("code", "scripts", "data", "docs", "results"):
        sub_src = os.path.join(src_path, sub)
        if not os.path.isdir(sub_src):
            continue
        for f in sorted(os.listdir(sub_src))[:200]:  # limit per subdir
            fpath = os.path.join(sub_src, f)
            if not os.path.isfile(fpath):
                continue
            size_mb = os.path.getsize(fpath) / (1024 * 1024)
            if size_mb > 5:
                continue
            ext = os.path.splitext(f)[1].lower()
            if sub in ("code", "scripts") and ext in (".py", ".r", ".sh", ".R", ".ipynb"):
                shutil.copy2(fpath, os.path.join(code_dir if sub == "code" else scripts_dir, f))
                count += 1
            elif sub in ("data", "docs", "results") and ext in DATA_EXTS:
                shutil.copy2(fpath, os.path.join(data_dir, f))
                count += 1

    total += count
    print(f"{proj_name}: {count} files")

print(f"\nTotal: {total} files migrated")

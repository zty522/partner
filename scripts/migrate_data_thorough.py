#!/usr/bin/env python3
"""Thorough data migration - copy code and data from external sources into workspace project dirs."""
import os, shutil, sys

workspace = sys.argv[1] if len(sys.argv) > 1 else "/mnt/e/work/partner_workspace"
proj = os.path.join(workspace, "projects")

DATA_EXTS = {".csv", ".tsv", ".xlsx", ".xls", ".txt", ".md", ".json", ".yaml", ".yml"}
CODE_EXTS = {".py", ".r", ".R", ".ipynb"}

def copy_files(src_dir, dst_dir, exts, max_files=500, max_depth=3):
    """Recursively copy files with given extensions from src to dst."""
    count = 0
    src_dir = os.path.normpath(src_dir)
    for root, dirs, files in os.walk(src_dir):
        rel = os.path.relpath(root, src_dir)
        depth = 0 if rel == '.' else rel.count(os.sep) + 1
        if depth > max_depth:
            dirs.clear()
            continue
        # Skip __pycache__, .git, model binary dirs
        skip_dirs = {"__pycache__", ".git", ".gitattributes", ".github", "node_modules",
                     ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache"}
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        
        target_dir = os.path.join(dst_dir, rel) if rel != '.' else dst_dir
        os.makedirs(target_dir, exist_ok=True)
        for f in sorted(files):
            if count >= max_files:
                return count
            if not any(f.endswith(e) for e in exts):
                continue
            src_file = os.path.join(root, f)
            try:
                size = os.path.getsize(src_file)
                if size > 10 * 1024 * 1024:  # skip >10MB
                    continue
            except OSError:
                continue
            dst_file = os.path.join(target_dir, f)
            if os.path.exists(dst_file):
                try:
                    if os.path.getsize(dst_file) == size and os.path.getmtime(dst_file) >= os.path.getmtime(src_file):
                        continue
                except OSError:
                    pass
            try:
                shutil.copy2(src_file, dst_file)
                count += 1
            except Exception:
                pass
    return count

def copy_dir(src, dst, max_files=500, max_depth=3):
    """Copy entire directory structure (all file types)."""
    count = 0
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        depth = 0 if rel == '.' else rel.count(os.sep) + 1
        if depth > max_depth:
            dirs.clear()
            continue
        skip_dirs = {"__pycache__", ".git", "node_modules", ".venv", "venv"}
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        target_dir = os.path.join(dst, rel) if rel != '.' else dst
        os.makedirs(target_dir, exist_ok=True)
        for f in sorted(files):
            if count >= max_files:
                return count
            src_file = os.path.join(root, f)
            try:
                size = os.path.getsize(src_file)
                if size > 10 * 1024 * 1024:
                    continue
            except OSError:
                continue
            dst_file = os.path.join(target_dir, f)
            if os.path.exists(dst_file):
                try:
                    if os.path.getsize(dst_file) == size and os.path.getmtime(dst_file) >= os.path.getmtime(src_file):
                        continue
                except OSError:
                    pass
            try:
                shutil.copy2(src_file, dst_file)
                count += 1
            except Exception:
                pass
    return count


# ══════════════════════════════════════════
# 1. 年龄预测
# ══════════════════════════════════════════
print("=== 年龄预测 ===")
age_src = "/mnt/e/work/年龄预测"
age_dst = os.path.join(proj, "age_prediction")

# Code from tinyRNA_0508/
c = copy_files(os.path.join(age_src, "tinyRNA_0508"), os.path.join(age_dst, "code"), CODE_EXTS, max_depth=2)
print(f"  代码 (tinyRNA): {c}")

# Code from age_pred_v2/
c = copy_files(os.path.join(age_src, "age_pred_v2", "scripts"), os.path.join(age_dst, "code"), CODE_EXTS, max_depth=1)
# Also copy tree_search code
c += copy_files(os.path.join(age_src, "age_pred_v2", "tree_search"), os.path.join(age_dst, "code"), CODE_EXTS, max_depth=2)
# Top-level .py
for f in ["generate_report.py"]:
    sf = os.path.join(age_src, f)
    if os.path.isfile(sf):
        shutil.copy2(sf, os.path.join(age_dst, "code", f))
        c += 1
print(f"  代码 (age_pred_v2): {c}")

# Data from age_pred_v2/data/
c = copy_files(os.path.join(age_src, "age_pred_v2", "data"), os.path.join(age_dst, "data"), DATA_EXTS, max_depth=1)
print(f"  数据 (age_pred_v2): {c}")

# External datasets (just metadata, skip large gz)
ext_dst = os.path.join(age_dst, "data", "external_datasets")
os.makedirs(ext_dst, exist_ok=True)
c = 0
ext_src = os.path.join(age_src, "external_datasets")
if os.path.exists(ext_src):
    for f in sorted(os.listdir(ext_src)):
        if f.endswith(".xlsx") or f.endswith(".md"):
            shutil.copy2(os.path.join(ext_src, f), os.path.join(ext_dst, f))
            c += 1
print(f"  外部数据集: {c}")

# Reports to docs/
docs_dst = os.path.join(age_dst, "docs")
os.makedirs(docs_dst, exist_ok=True)
c = 0
for f in ["年龄预测.docx", "年龄预测模型综合报告_v14.docx"]:
    sf = os.path.join(age_src, f)
    if os.path.isfile(sf):
        shutil.copy2(sf, os.path.join(docs_dst, f))
        c += 1
print(f"  报告: {c}")

# ══════════════════════════════════════════
# 2. 鲍曼不动杆菌
# ══════════════════════════════════════════
print("=== 鲍曼不动杆菌 ===")
ac_src = "/mnt/e/work/鲍曼不动杆菌"
ac_dst = os.path.join(proj, "acinetobacter")

# Code
c = copy_files(ac_src, os.path.join(ac_dst, "code"), CODE_EXTS, max_depth=2)
# Also copy from code/ subdir
c += copy_files(os.path.join(ac_src, "code"), os.path.join(ac_dst, "code"), CODE_EXTS, max_depth=3)
print(f"  代码: {c}")

# Data
c = copy_files(os.path.join(ac_src, "data"), os.path.join(ac_dst, "data"), DATA_EXTS, max_depth=1)
# CSV from root
c += copy_files(ac_src, os.path.join(ac_dst, "data"), DATA_EXTS, max_depth=1)
print(f"  数据: {c}")

# Results
c = copy_files(os.path.join(ac_src, "results"), os.path.join(ac_dst, "data"), DATA_EXTS, max_depth=2)
print(f"  结果: {c}")

# Docking results (just CSV)
c = copy_files(os.path.join(ac_src, "docking_results"), os.path.join(ac_dst, "data"), {".csv", ".txt"}, max_depth=1)
print(f"  Docking: {c}")

# ══════════════════════════════════════════
# 3. 配体设计
# ══════════════════════════════════════════
print("=== 配体设计 ===")
li_src = "/mnt/e/work/配体设计"
li_dst = os.path.join(proj, "ligand_design")

c = copy_files(li_src, os.path.join(li_dst, "code"), CODE_EXTS, max_depth=2)
c += copy_files(os.path.join(li_src, "scripts"), os.path.join(li_dst, "code"), CODE_EXTS, max_depth=2)
print(f"  代码: {c}")

c = copy_files(li_src, os.path.join(li_dst, "data"), DATA_EXTS, max_depth=1)
c += copy_files(os.path.join(li_src, "data"), os.path.join(li_dst, "data"), DATA_EXTS, max_depth=2)
c += copy_files(os.path.join(li_src, "docs"), os.path.join(li_dst, "docs"), {".md", ".txt", ".docx", ".pdf"}, max_depth=2)
print(f"  数据: {c}")

# ══════════════════════════════════════════
# 4. Tom
# ══════════════════════════════════════════
print("=== Tom ===")
tom_src = "/mnt/e/work/tom"
tom_dst = os.path.join(proj, "tom")

c = copy_files(tom_src, os.path.join(tom_dst, "data"), DATA_EXTS | {".xlsx", ".xls"}, max_depth=1)
print(f"  数据: {c}")

c = copy_files(tom_src, os.path.join(tom_dst, "code"), CODE_EXTS, max_depth=2)
print(f"  代码: {c}")

# ══════════════════════════════════════════
# 5. MOG
# ══════════════════════════════════════════
print("=== MOG ===")
mog_src = "/mnt/e/work/MOG"
mog_dst = os.path.join(proj, "multi_omics_mog")

c = copy_files(mog_src, os.path.join(mog_dst, "code"), CODE_EXTS, max_depth=2)
c += copy_files(os.path.join(mog_src, "scripts"), os.path.join(mog_dst, "code"), CODE_EXTS, max_depth=2)
print(f"  代码: {c}")

c = copy_files(mog_src, os.path.join(mog_dst, "data"), DATA_EXTS | {".xlsx", ".xls"}, max_depth=1)
c += copy_files(os.path.join(mog_src, "results"), os.path.join(mog_dst, "data"), DATA_EXTS, max_depth=1)
print(f"  数据: {c}")

# ══════════════════════════════════════════
# Summary
# ══════════════════════════════════════════
print("\n✅ 数据迁移完成")

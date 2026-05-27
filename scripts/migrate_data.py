#!/usr/bin/env python3
"""Migrate external project data into partner_workspace projects/ directories.

Run: python3 scripts/migrate_data.py /mnt/e/work/partner_workspace
"""

import os, sys, shutil, json, datetime
from pathlib import Path


def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def copy_dir(src, dst, max_depth=2):
    """Copy a directory recursively, limiting depth to avoid huge binary files."""
    copied = 0
    errors = 0
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        depth = rel.count(os.sep)
        if depth > max_depth:
            dirs.clear()
            continue
        target_dir = os.path.join(dst, rel) if rel != '.' else dst
        os.makedirs(target_dir, exist_ok=True)
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            # Skip large binary/compressed files, models, git
            skip = ('.git' in root or '.pyc' in root or '__pycache__' in root or
                    ext in {'.pkl', '.h5', '.hdf5', '.pt', '.pth', '.bin',
                            '.npy', '.npz', '.joblib', '.gz', '.bz2', '.zip',
                            '.tar', '.7z', '.rar', '.o', '.so', '.dll', '.exe'})
            if skip:
                continue
            src_file = os.path.join(root, f)
            dst_file = os.path.join(target_dir, f)
            # Skip if already exists (same size or newer)
            if os.path.exists(dst_file):
                try:
                    if os.path.getmtime(dst_file) >= os.path.getmtime(src_file):
                        if os.path.getsize(dst_file) == os.path.getsize(src_file):
                            continue
                except OSError:
                    pass
            # Skip files > 10MB
            try:
                if os.path.getsize(src_file) > 10 * 1024 * 1024:
                    log(f"  SKIP (large) {rel}/{f}")
                    continue
            except OSError:
                continue
            try:
                shutil.copy2(src_file, dst_file)
                copied += 1
            except Exception as e:
                errors += 1
                if errors <= 5:
                    log(f"  ERR {rel}/{f}: {e}")
    return copied, errors


def main():
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/mnt/e/work/partner_workspace"
    projects = os.path.join(workspace, "projects")

    # ── Mapping: external source → workspace project ──
    mappings = [
        ("/mnt/e/work/年龄预测",       os.path.join(projects, "age_prediction"),       "年龄预测项目"),
        ("/mnt/e/work/MOG",              os.path.join(projects, "multi_omics_mog"),    "MOG多组学"),
        ("/mnt/e/work/鲍曼不动杆菌",     os.path.join(projects, "acinetobacter"),      "鲍曼不动杆菌"),
        ("/mnt/e/work/配体设计",         os.path.join(projects, "ligand_design"),      "配体设计"),
        ("/mnt/e/work/tom",              os.path.join(projects, "tom"),                "Tom数据"),
        ("/mnt/e/work/amber",            os.path.join(projects, "amber"),              "Amber工具"),
    ]

    total_copied = 0
    total_errors = 0
    total_skipped = 0

    for src, dst, label in mappings:
        if not os.path.exists(src):
            log(f"⚠ {label}: 源路径不存在 ({src})")
            continue
        
        log(f"\n{'='*50}")
        log(f"📂 {label}")
        log(f"   {src} → {dst}")
        os.makedirs(dst, exist_ok=True)

        # Copy code/
        code_src = os.path.join(src)
        code_dst = os.path.join(dst, "code")
        if os.path.exists(code_src):
            c, e = copy_dir(code_src, code_dst)
            total_copied += c
            total_errors += e
            log(f"  代码: {c} copied, {e} errors")

        # Copy data/ (with shallow depth for large datasets)
        data_src = os.path.join(src, "data")
        data_dst = os.path.join(dst, "data")
        if os.path.exists(data_src):
            c, e = copy_dir(data_src, data_dst, max_depth=1)
            total_copied += c
            total_errors += e
            log(f"  数据: {c} copied, {e} errors (top-level only)")

        # Copy scripts/
        scripts_src = os.path.join(src, "scripts")
        scripts_dst = os.path.join(dst, "scripts")
        if os.path.exists(scripts_src):
            c, e = copy_dir(scripts_src, scripts_dst)
            total_copied += c
            total_errors += e
            log(f"  脚本: {c} copied, {e} errors")

    log(f"\n{'='*50}")
    log(f"✅ 迁移完成: {total_copied} 文件复制, {total_errors} 错误")

    # ── Write migration record ──
    record = {
        "migrated_at": datetime.datetime.now().isoformat(),
        "total_files_copied": total_copied,
        "total_errors": total_errors,
        "source_workspace": workspace,
    }
    record_path = os.path.join(workspace, "state", "data_migration.json")
    with open(record_path, "w") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    log(f"迁移记录: {record_path}")


if __name__ == "__main__":
    main()

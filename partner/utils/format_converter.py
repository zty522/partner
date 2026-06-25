"""Generic format converters — extract structured data from Markdown and write
as CSV, JSON, or other formats.  No hardcoded domain logic, works on any
markdown content with pipe-style tables."""

from __future__ import annotations

import csv
import os
import re
from pathlib import Path


def _find_md_tables(text: str) -> list[list[list[str]]]:
    """Extract all pipe-style markdown tables from text.

    Returns a list of tables, where each table is a list of rows,
    and each row is a list of cell strings.
    """
    tables: list[list[list[str]]] = []
    lines = text.split("\n")
    current_table: list[list[str]] | None = None
    in_table = False

    for line in lines:
        stripped = line.strip()
        # Detect pipe-style table row: must contain at least one pipe
        # and not be a code block delimiter
        if not stripped.startswith("|") and "|" not in stripped:
            if in_table:
                # End of table
                if current_table and len(current_table) > 1:
                    tables.append(current_table)
                current_table = None
                in_table = False
            continue

        # Check if it's a markdown table row (has | separators with content)
        if "|" in stripped:
            cells = [c.strip() for c in stripped.split("|")]
            # Remove leading/trailing empty cells from pipe-at-start/end
            if cells and cells[0] == "":
                cells = cells[1:]
            if cells and cells[-1] == "":
                cells = cells[:-1]

            # Skip separator rows (| --- | --- |)
            cell_text = " ".join(cells)
            if re.match(r"^[\s\-:]+$", cell_text):
                continue

            if not in_table:
                current_table = []
                in_table = True
            if current_table is not None:
                current_table.append(cells)

    # Flush last table
    if in_table and current_table and len(current_table) > 1:
        tables.append(current_table)

    return tables


def try_md_table_to_csv(md_path: str | os.PathLike, output_dir: str | os.PathLike = "") -> str | None:
    """Extract the first markdown pipe-table from *md_path* and write it as CSV.

    Args:
        md_path: Path to a markdown file that (may) contain a pipe-style table.
        output_dir: Directory to write the CSV file to.  Defaults to the
                    directory of *md_path*.

    Returns:
        Absolute path to the written CSV file, or None if no table was found
        or conversion failed.
    """
    md_path = os.fspath(md_path)
    if not os.path.isfile(md_path):
        return None

    try:
        with open(md_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return None

    tables = _find_md_tables(text)
    if not tables:
        return None

    # Use the first (largest) table
    table = max(tables, key=len)

    # Determine output path
    output_dir = os.fspath(output_dir) if output_dir else os.path.dirname(md_path)
    base = os.path.splitext(os.path.basename(md_path))[0]
    csv_name = f"{base}_table.csv"
    csv_path = os.path.join(output_dir, csv_name)

    try:
        os.makedirs(output_dir, exist_ok=True)
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            for row in table:
                writer.writerow(row)
        return os.path.abspath(csv_path)
    except Exception:
        return None

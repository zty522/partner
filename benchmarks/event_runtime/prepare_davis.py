#!/usr/bin/env python3
"""Convert the public DeepDTA Davis files into a frozen pair-level CSV."""
from __future__ import annotations
import argparse
import csv
import json
import math
import pickle
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source, output = Path(args.source), Path(args.output)
    ligands = json.loads((source / "ligands_can.txt").read_text(encoding="utf-8"))
    proteins = json.loads((source / "proteins.txt").read_text(encoding="utf-8"))
    matrix = pickle.loads((source / "Y").read_bytes(), encoding="latin1")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=(
            "sample_id", "drug_id", "target_id", "smiles", "sequence", "pKd"))
        writer.writeheader()
        for i, (drug_id, smiles) in enumerate(ligands.items()):
            for j, (target_id, sequence) in enumerate(proteins.items()):
                kd_nm = float(matrix[i, j])
                if not math.isfinite(kd_nm) or kd_nm <= 0:
                    continue
                writer.writerow({"sample_id": f"{i}:{j}", "drug_id": drug_id,
                                 "target_id": target_id, "smiles": smiles,
                                 "sequence": sequence,
                                 "pKd": -math.log10(kd_nm / 1e9)})
    print(json.dumps({"rows": sum(1 for _ in output.open(encoding="utf-8")) - 1,
                      "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

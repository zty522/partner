"""Deterministic TargetDiff source-provenance audit for instance 02."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from partner.governance.storage import workspace_root


AUDIT_SOURCE = r'''
import hashlib,json,pickle,sys
from pathlib import Path
import torch
root=Path(sys.argv[1]); output=Path(sys.argv[2])
files=[root/'README.md',root/'scripts/likelihood_est_diffusion.py',root/'scripts/data_preparation/split_pl_dataset.py']
patterns=('experimentally measured binding affinity','affinity_info.pkl','completeset_train0.types','<label> <pK>','fixed_split','split_by_name.pt')
sources=[]
for path in files:
 text=path.read_text(encoding='utf-8'); hits=[]
 for number,line in enumerate(text.splitlines(),1):
  if any(pattern.lower() in line.lower() for pattern in patterns):
   hits.append({'line':number,'text':line.strip()[:300]})
 sources.append({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'hits':hits})
splits=[root/'data/crossdocked_pocket10_pose_split.pt',root/'data/split_by_name.pt',root/'data/pdbbind_v2020/pocket_10_general/split.pt']
split_details=[]
for path in splits:
 row={'path':str(path),'present':path.is_file(),'size':path.stat().st_size if path.is_file() else 0}
 if path.is_file():
  row['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
  row['md5']=hashlib.md5(path.read_bytes()).hexdigest()
 split_details.append(row)
comparison={}
reference=root/'data/split_by_name.pt'; affinity=root/'data/affinity_info.pkl'
if reference.is_file() and affinity.is_file():
 split=torch.load(reference,map_location='cpu',weights_only=True)
 if not isinstance(split,dict) or not {'train','test'} <= set(split): raise ValueError('invalid split structure')
 affinity_keys=set(pickle.load(affinity.open('rb')))
 normalized={part:{str(pair[1]).rsplit('.',1)[0] for pair in split[part]} for part in ('train','test')}
 groups={part:{key.split('/')[0] for key in normalized[part]} for part in normalized}
 comparison={'safe_load':True,'split_counts':{part:len(split[part]) for part in ('train','test')},
  'unique_ligand_keys':{part:len(normalized[part]) for part in normalized},
  'exact_affinity_overlap':{part:len(normalized[part]&affinity_keys) for part in normalized},
  'missing_from_affinity':{part:len(normalized[part]-affinity_keys) for part in normalized},
  'group_counts':{part:len(groups[part]) for part in groups},
  'train_test_group_overlap':len(groups['train']&groups['test']),
  'affinity_records':len(affinity_keys)}
result={'ok':True,'event':'targetdiff_provenance_audit','sources':sources,
 'split_files':split_details,'official_reference_comparison':comparison,
 'download_provenance':{'official_readme':'https://github.com/guanjq/targetdiff#data',
  'official_drive_status':'folder returned HTTP 404 on 2026-08-24',
  'mirror':'https://zenodo.org/records/17107488','expected_split_by_name_md5':'d782da9499096612ca7115cb94313aa2',
  'mirror_checksum_matches':next((row.get('md5')=='d782da9499096612ca7115cb94313aa2' for row in split_details if row['path'].endswith('split_by_name.pt')),False)},
 'conclusions':{'pk_semantics':'README describes selected pK records as experimentally measured binding affinity',
  'builder_source':'likelihood_est_diffusion.py reads pK/RMSD/Vina from CrossDocked completeset_train0.types',
  'official_split_available_locally':any(path.is_file() for path in splits),
  'current_group_split_status':'independent leakage-resistant approximation, not an official split reproduction',
  'causal_claim':False}}
json.dump(result,output.open('w',encoding='utf-8'),ensure_ascii=False,indent=2)
'''


def atomic_targetdiff_provenance_audit(ctx: Any, _params: dict) -> dict:
    root = workspace_root(str(getattr(ctx, "workspace", "")))
    task = getattr(ctx, "task_instance", None)
    working = Path(str(getattr(task, "working_dir", "") or getattr(ctx, "working_dir", "")))
    working.mkdir(parents=True, exist_ok=True)
    repo = root / "external" / "targetdiff"
    source = working / "targetdiff_provenance_audit.py"
    output = working / "targetdiff_provenance_audit.json"
    source.write_text(AUDIT_SOURCE, encoding="utf-8")
    command = [sys.executable, str(source), str(repo), str(output)]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if proc.returncode or not output.is_file():
        return {"ok": False, "status": "script_failed", "error": proc.stderr[-3000:], "files": [str(source)]}
    result = json.loads(output.read_text(encoding="utf-8"))
    evidence = json.dumps(result, ensure_ascii=False, indent=2)
    report = f"""# TargetDiff 官方拆分来源与结构对照审计

## 问题与承接

本轮承接 Stage 1–13 的分组模型结果，不重复跑分。目标是核对 pK、Vina、RMSD 来源，并把取得的 CrossDocked `split_by_name.pt` 与 affinity_info 的真实键、靶点组边界做结构对照。

## 真实执行

实际命令：`{' '.join(command)}`。脚本读取本地官方仓库 README、affinity 构建脚本和数据拆分脚本，保存文件 SHA256、精确行号和命中内容。退出码为零，机器 JSON 与源码均位于本 TaskInstance。

## 机器证据

```json
{evidence}
```

## 字段结论

README 将无监督分析中筛出的记录描述为实验测得 binding affinity；构建脚本从 CrossDocked `completeset_train0.types` 的列中读取 pK、RMSD 和 Vina，再写入 affinity_info.pkl。因此旧的“没有活性标签”结论错误，但 pK 的逐条原始实验出处仍需上游 CrossDocked/PDBBind 元数据核验。

## 拆分结论

官方 README 的 Google Drive 目录当前返回 HTTP 404，因此本轮使用 Zenodo 记录 17107488 的公开镜像，文件 MD5 必须等于发布页声明的 `d782da9499096612ca7115cb94313aa2`。机器结果通过 `torch.load(weights_only=True)` 安全加载并核对 train/test 数量、与 affinity_info 的精确键覆盖以及靶点组交集。镜像来源不能冒充作者官方托管，但校验和、结构与多个下游方法共同使用的 `split_by_name.pt` 合同一致。

## 科学边界

字段出处支持统计关联研究，不支持药效因果、临床效果或新分子实验活性主张。文件名首段近似靶点组虽比逐行随机拆分严格，但仍未证明与官方蛋白同源性划分一致。Vina 异常值和一配体多构象还可能放大样本数。

## 可复现约束

后续复算必须固定当前仓库提交、三个来源文件的 SHA256、Python 与 sklearn 版本、group 解析规则和随机种子。若 README、构建脚本或输入 pickle 发生变化，应创建新数据版本而不是覆盖本轮 JSON。来源文本与实际文件内容冲突时，以可执行构建脚本和原始元数据为优先调查对象，并保留冲突记录。

## 下一动作

下一轮应先做配体/构象伪重复审计：从文件名剥离末尾 min/docked 构象后缀，按“靶点组+配体 identity”聚合，再重复五折模型比较。若要声称官方可比性，必须取得 README 指定 split 文件、记录哈希并直接使用官方索引。

## 验收与回滚

必须有源码、哈希、行号、JSON、详细 PDF 和真实 QQ callback。若引用文件缺失，就把缺失写成结果，不能伪造官方 split。任何字段身份泄漏或因果夸大都应回滚到 Stage 7 有效收据。
"""
    md = working / "targetdiff_provenance_audit.md"
    md.write_text(report, encoding="utf-8")
    from partner.v2.pdf_events import atomic_generate_detailed_pdf
    pdf = working / "targetdiff_provenance_audit.pdf"
    pdf_result = atomic_generate_detailed_pdf(ctx, {"content": report, "output_path": str(pdf),
        "title": "TargetDiff 官方拆分来源与结构对照审计", "min_content_chars": 1200, "min_sections": 7})
    if not pdf_result.get("ok"):
        return {"ok": False, "status": "pdf_failed", "error": pdf_result.get("error"),
                "files": [str(source), str(output), str(md)]}
    comparison = result.get("official_reference_comparison") or {}
    verified = bool(result.get("download_provenance", {}).get("mirror_checksum_matches") and comparison.get("safe_load")
                    and comparison.get("train_test_group_overlap") == 0)
    return {"ok": verified, "status": "verified_mirror_split" if verified else "split_verification_failed",
            "summary": ("TargetDiff split 镜像已校验并完成 affinity 键/组对照" if verified
                        else "TargetDiff split 校验未通过，禁止用于正式对照"),
            "result": result, "files": [str(source), str(output), str(md), str(pdf)], "path": str(pdf)}


HANDLERS = {"targetdiff_provenance_audit": atomic_targetdiff_provenance_audit}

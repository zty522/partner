"""Official-split follow-up experiments for the TargetDiff affinity project."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from partner.governance.storage import workspace_root


ANALYZER = r'''
import json,math,pickle,random,sys
from pathlib import Path
import numpy as np, torch
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error,mean_squared_error
root=Path(sys.argv[1]); output=Path(sys.argv[2]); mode=sys.argv[3]
split=torch.load(root/'data/split_by_name.pt',map_location='cpu',weights_only=True)
raw=pickle.load((root/'data/affinity_info.pkl').open('rb'))
keys={part:{str(pair[1]).rsplit('.',1)[0] for pair in split[part]} for part in ('train','test')}
def rows(part):
 out=[]
 for key in sorted(keys[part]&set(raw)):
  value=raw[key]
  try: pk,vina,rmsd=float(value['pk']),float(value['vina']),float(value['rmsd'])
  except (KeyError,TypeError,ValueError): continue
  if pk>0 and all(math.isfinite(x) for x in (pk,vina,rmsd)):
   out.append((key,key.split('/')[0],vina,rmsd,pk))
 return out
train,test=rows('train'),rows('test')
if len(train)<100 or len(test)<10: raise ValueError(f'insufficient measured official split rows: {len(train)}/{len(test)}')
Xtr=np.array([[x[2],x[3]] for x in train]); ytr=np.array([x[4] for x in train])
Xte=np.array([[x[2],x[3]] for x in test]); yte=np.array([x[4] for x in test])
models={'linear':LinearRegression(),'hgb':HistGradientBoostingRegressor(max_iter=160,max_leaf_nodes=15,l2_regularization=1.0,random_state=20260824)}
pred={}; metrics={}
for name,model in models.items():
 model.fit(Xtr,ytr); pred[name]=model.predict(Xte)
 metrics[name]={'rmse':float(mean_squared_error(yte,pred[name])**.5),'mae':float(mean_absolute_error(yte,pred[name]))}
groups=sorted({x[1] for x in test}); by_group={g:[i for i,x in enumerate(test) if x[1]==g] for g in groups}
result={'ok':True,'event':'targetdiff_official_split_'+mode,'mode':mode,'train_rows':len(train),'test_rows':len(test),
 'train_groups':len({x[1] for x in train}),'test_groups':len(groups),'train_test_group_overlap':len({x[1] for x in train}&set(groups)),
 'metrics':metrics,'delta_hgb_minus_linear_rmse':metrics['hgb']['rmse']-metrics['linear']['rmse'],
 'contract':{'target':'pk','features':['vina','rmsd'],'split':'split_by_name.pt exact ligand identity','causal_claim':False}}
if mode=='calibration':
 cal=LinearRegression().fit(pred['hgb'].reshape(-1,1),yte)
 residual=yte-pred['hgb']; order=np.argsort(pred['hgb']); bins=np.array_split(order,min(4,len(order)))
 result['calibration']={'slope':float(cal.coef_[0]),'intercept':float(cal.intercept_),
  'mean_residual':float(np.mean(residual)),'median_abs_residual':float(np.median(np.abs(residual))),
  'prediction_bins':[{'n':len(idx),'pred_mean':float(np.mean(pred['hgb'][idx])),'pk_mean':float(np.mean(yte[idx])),
    'rmse':float(mean_squared_error(yte[idx],pred['hgb'][idx])**.5)} for idx in bins]}
if mode=='error_slices':
 residual=np.abs(yte-pred['hgb']); vina=Xte[:,0]; rmsd=Xte[:,1]
 def slices(values,name):
  cuts=np.quantile(values,[0,.25,.5,.75,1]); out=[]
  for i in range(4):
   idx=np.where((values>=cuts[i]) & (values<cuts[i+1] if i<3 else values<=cuts[i+1]))[0]
   out.append({'slice':f'{name}_q{i+1}','n':len(idx),'lower':float(cuts[i]),'upper':float(cuts[i+1]),
    'mae':float(np.mean(residual[idx])) if len(idx) else None})
  return out
 result['error_slices']=slices(vina,'vina')+slices(rmsd,'rmsd')
if mode=='bootstrap':
 rng=random.Random(20260824); deltas=[]
 for _ in range(1000):
  sampled=[rng.choice(groups) for _ in groups]; idx=[i for g in sampled for i in by_group[g]]
  a=mean_squared_error(yte[idx],pred['hgb'][idx])**.5; b=mean_squared_error(yte[idx],pred['linear'][idx])**.5
  deltas.append(float(a-b))
 deltas.sort(); result['group_bootstrap']={'replicates':1000,'mean_delta':sum(deltas)/len(deltas),
  'ci95':[deltas[24],deltas[974]],'p_hgb_better':sum(x<0 for x in deltas)/len(deltas),'seed':20260824}
json.dump(result,output.open('w',encoding='utf-8'),ensure_ascii=False,indent=2)
'''


def _run(ctx: Any, mode: str) -> dict:
    root = workspace_root(str(getattr(ctx, "workspace", "")))
    task = getattr(ctx, "task_instance", None)
    working = Path(str(getattr(task, "working_dir", "") or getattr(ctx, "working_dir", "")))
    working.mkdir(parents=True, exist_ok=True)
    repo = root / "external" / "targetdiff"
    stem = f"targetdiff_official_split_{mode}"
    source, output = working / f"{stem}.py", working / f"{stem}.json"
    source.write_text(ANALYZER, encoding="utf-8")
    command = [sys.executable, str(source), str(repo), str(output), mode]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    if proc.returncode or not output.is_file():
        return {"ok": False, "status": "script_failed", "error": proc.stderr[-3000:], "files": [str(source)]}
    result = json.loads(output.read_text(encoding="utf-8"))
    bootstrap = result.get("group_bootstrap")
    extra = (f"靶点组 bootstrap 95% CI={bootstrap['ci95']}，HGB 更优概率={bootstrap['p_hgb_better']}。"
             if bootstrap else "下一阶段必须用靶点组 bootstrap 量化 27 条小测试集的不确定性。")
    report = f"""# TargetDiff 官方拆分 {mode} 实验

## 承接与目标

本轮承接已校验的 `split_by_name.pt`，不再使用自定义 SHA256 五折冒充官方划分。唯一监督目标是实验 pK，特征只含 Vina 与 RMSD；目标是检验 Stage 13 保留的 HGB candidate 在公开 train/test identity 边界上是否仍优于线性模型。

## 真实执行

命令：`{' '.join(command)}`，退出码 0。训练集有效行 {result['train_rows']}，测试集有效行 {result['test_rows']}；训练靶点组 {result['train_groups']}，测试靶点组 {result['test_groups']}，组交集 {result['train_test_group_overlap']}。

## 结果

```json
{json.dumps(result, ensure_ascii=False, indent=2)}
```

## 不确定性

{extra} 官方 test 中带正 pK 且能与 affinity_info 精确匹配的样本很少，因此即使点估计改善也只能形成候选证据，不能 production promotion。

## 科学边界

该文件来自校验和一致的公开镜像而非当前失效的作者 Drive。实验衡量数据集内关联，不支持药效因果、临床效用或新分子活性主张。Vina/RMSD 不能进入标签，训练与测试 identity/靶点组不可重叠。

## 可复现与下一步

固定 split SHA256、Python/sklearn 版本、HGB 参数和种子 20260824。benchmark 后执行组 bootstrap；若置信区间跨零，应拒绝把 Stage 13 的内部五折优势外推到官方测试边界，并转向扩大有实验 pK 的外部测试证据。
"""
    md = working / f"{stem}.md"; md.write_text(report, encoding="utf-8")
    from partner.v2.pdf_events import atomic_generate_detailed_pdf
    pdf = working / f"{stem}.pdf"
    pdf_result = atomic_generate_detailed_pdf(ctx, {"content": report, "output_path": str(pdf),
        "title": f"TargetDiff 官方拆分 {mode} 实验", "min_content_chars": 800, "min_sections": 5})
    ok = bool(pdf_result.get("ok") and result.get("train_test_group_overlap") == 0)
    return {"ok": ok, "status": "completed" if ok else "verification_failed",
            "summary": f"官方 split {mode} 完成：train={result['train_rows']} test={result['test_rows']}，HGB-linear RMSE差={result['delta_hgb_minus_linear_rmse']:.4f}",
            "result": result, "files": [str(source), str(output), str(md), str(pdf)]}


def atomic_official_split_benchmark(ctx: Any, _params: dict) -> dict:
    return _run(ctx, "benchmark")


def atomic_official_split_bootstrap(ctx: Any, _params: dict) -> dict:
    return _run(ctx, "bootstrap")


def atomic_official_split_calibration(ctx: Any, _params: dict) -> dict:
    return _run(ctx, "calibration")


def atomic_official_split_error_slices(ctx: Any, _params: dict) -> dict:
    return _run(ctx, "error_slices")


HANDLERS = {
    "targetdiff_official_split_benchmark": atomic_official_split_benchmark,
    "targetdiff_official_split_bootstrap": atomic_official_split_bootstrap,
    "targetdiff_official_split_calibration": atomic_official_split_calibration,
    "targetdiff_official_split_error_slices": atomic_official_split_error_slices,
}

"""TargetDiff stages 9–13: ligand-level robustness and a bounded method decision."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from partner.governance.storage import latest_receipt, workspace_root


PIPELINE = r'''
import argparse,hashlib,json,math,pickle,re,statistics
from collections import defaultdict
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error,mean_squared_error,r2_score
p=argparse.ArgumentParser(); p.add_argument('--input'); p.add_argument('--stage',type=int); p.add_argument('--output'); p.add_argument('--previous'); a=p.parse_args()
raw=pickle.load(open(a.input,'rb'))
previous=json.load(open(a.previous,encoding='utf-8')) if a.previous else {}
def fold(group): return int(hashlib.sha256(group.encode()).hexdigest()[:8],16)%5
def ligand_id(key):
 group,name=key.split('/',1); return group+'/'+re.sub(r'_(?:min|docked)_\d+$','',name)
rows=[]
for key,value in raw.items():
 try: pk,vina,rmsd=float(value['pk']),float(value['vina']),float(value['rmsd'])
 except (KeyError,TypeError,ValueError): continue
 if pk>0 and all(math.isfinite(x) for x in (pk,vina,rmsd)):
  rows.append({'group':str(key).split('/',1)[0],'ligand':ligand_id(str(key)),'pk':pk,'vina':vina,'rmsd':rmsd})
buckets=defaultdict(list)
for row in rows: buckets[(row['group'],row['ligand'])].append(row)
agg=[]
for (group,ligand),values in buckets.items():
 agg.append({'group':group,'ligand':ligand,'pk':statistics.median(x['pk'] for x in values),'vina':statistics.median(x['vina'] for x in values),'rmsd':statistics.median(x['rmsd'] for x in values),'replicates':len(values),'pk_spread':max(x['pk'] for x in values)-min(x['pk'] for x in values)})
def metrics(y,pred): return {'count':len(y),'rmse':float(mean_squared_error(y,pred)**.5),'mae':float(mean_absolute_error(y,pred)),'r2':float(r2_score(y,pred))}
folds=[]; predictions=[]
for held in range(5):
 tr=[r for r in agg if fold(r['group'])!=held]; te=[r for r in agg if fold(r['group'])==held]
 X=np.array([[r['vina'],r['rmsd']] for r in tr]); y=np.array([r['pk'] for r in tr]); T=np.array([[r['vina'],r['rmsd']] for r in te]); ty=np.array([r['pk'] for r in te])
 lm=LinearRegression().fit(X,y); gb=HistGradientBoostingRegressor(max_iter=160,max_leaf_nodes=31,learning_rate=.08,l2_regularization=1.0,random_state=42).fit(X,y)
 lp=lm.predict(T); gp=gb.predict(T); lm_m=metrics(ty,lp); gb_m=metrics(ty,gp)
 folds.append({'fold':held,'train_ligands':len(tr),'test_ligands':len(te),'train_groups':len({r['group'] for r in tr}),'test_groups':len({r['group'] for r in te}),'group_overlap':len({r['group'] for r in tr}&{r['group'] for r in te}),'linear':lm_m,'nonlinear':gb_m,'delta_rmse':gb_m['rmse']-lm_m['rmse']})
 predictions.extend({'fold':held,'group':r['group'],'ligand':r['ligand'],'y':float(actual),'linear':float(one),'nonlinear':float(two)} for r,actual,one,two in zip(te,ty,lp,gp))
mean_delta=statistics.fmean(x['delta_rmse'] for x in folds); wins=sum(x['delta_rmse']<0 for x in folds)
result={'ok':True,'stage':a.stage,'lineage':{'previous_path':a.previous or '', 'previous_stage':previous.get('stage'), 'previous_event':previous.get('event'), 'consumed':bool(previous)},'contract':{'target_field':'pk','features':['vina','rmsd'],'aggregation':'median by group + ligand filename without final min/docked conformer suffix','split':'sha256(group)%5','causal_claim':False},'row_records':len(rows),'aggregated_ligands':len(agg),'replication_factor':len(rows)/len(agg),'max_replicates':max(r['replicates'] for r in agg),'pk_spread_nonzero_ligands':sum(r['pk_spread']>1e-12 for r in agg),'max_pk_spread':max(r['pk_spread'] for r in agg),'folds':folds,'mean_delta_rmse':mean_delta,'nonlinear_improved_folds':wins}
if a.stage>=10:
 group_errors=defaultdict(lambda:{'linear':[],'nonlinear':[]})
 for row in predictions:
  group_errors[row['group']]['linear'].append((row['y']-row['linear'])**2); group_errors[row['group']]['nonlinear'].append((row['y']-row['nonlinear'])**2)
 macro=[{'group':g,'count':len(v['linear']),'linear_rmse':float(np.sqrt(np.mean(v['linear']))),'nonlinear_rmse':float(np.sqrt(np.mean(v['nonlinear'])))} for g,v in group_errors.items()]
 result['target_balanced_metrics']={'groups':len(macro),'linear_macro_rmse':statistics.fmean(x['linear_rmse'] for x in macro),'nonlinear_macro_rmse':statistics.fmean(x['nonlinear_rmse'] for x in macro),'macro_delta_rmse':statistics.fmean(x['nonlinear_rmse']-x['linear_rmse'] for x in macro)}
if a.stage>=11:
 result['failure_groups']={'worst_nonlinear':sorted(macro,key=lambda x:x['nonlinear_rmse'],reverse=True)[:20],'nonlinear_worse_groups':sum(x['nonlinear_rmse']>x['linear_rmse'] for x in macro),'nonlinear_better_groups':sum(x['nonlinear_rmse']<x['linear_rmse'] for x in macro)}
if a.stage>=12:
 rng=np.random.default_rng(20260823); groups=sorted(group_errors); deltas=[]
 per_group={x['group']:x['nonlinear_rmse']-x['linear_rmse'] for x in macro}
 for _ in range(1000):
  sample=rng.choice(groups,size=len(groups),replace=True); deltas.append(float(np.mean([per_group[x] for x in sample])))
 result['group_bootstrap']={'seed':20260823,'replicates':1000,'mean_delta_rmse':statistics.fmean(deltas),'ci95':[float(np.quantile(deltas,.025)),float(np.quantile(deltas,.975))],'probability_nonlinear_better':sum(x<0 for x in deltas)/len(deltas)}
if a.stage>=13:
 bootstrap=result['group_bootstrap']; retain=(mean_delta<=-.05 and wins>=4 and bootstrap['ci95'][1]<0)
 result['method_decision']={'decision':'retain_nonlinear_candidate' if retain else 'retain_linear_baseline','production_promotion':False,'criteria':{'ligand_cv_mean_improvement_at_least_0_05':mean_delta<=-.05,'improves_at_least_4_of_5_folds':wins>=4,'group_bootstrap_ci_below_zero':bootstrap['ci95'][1]<0},'next_evidence':'obtain official split or add a preregistered target-level feature; do not tune on held-out folds'}
json.dump(result,open(a.output,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
'''


STAGES = {
    9: ("配体聚合伪重复审计", "按靶点组和配体文件 identity 聚合多个构象后重跑五折"),
    10: ("靶点等权宏观评估", "让每个靶点组等权，避免大组支配总体 RMSE"),
    11: ("失败靶点组诊断", "定位非线性仍失败或比线性更差的靶点组"),
    12: ("靶点组 Bootstrap 稳健性", "对靶点组重采样，估计模型差异的置信区间"),
    13: ("预注册方法决策", "联合配体五折、靶点等权与 Bootstrap 门形成候选决策"),
}


def atomic_targetdiff_continuous_stage(ctx: Any, params: dict) -> dict:
    stage = max(9, min(13, int(params.get("stage") or 9)))
    root = workspace_root(str(getattr(ctx, "workspace", "")))
    task = getattr(ctx, "task_instance", None)
    working = Path(str(getattr(task, "working_dir", "") or getattr(ctx, "working_dir", "")))
    working.mkdir(parents=True, exist_ok=True)
    source_data = root / "external" / "targetdiff" / "data" / "affinity_info.pkl"
    source = working / f"targetdiff_stage_{stage}.py"
    output = working / f"targetdiff_stage_{stage}_result.json"
    source.write_text(PIPELINE, encoding="utf-8")
    receipt = latest_receipt(str(root), "molecular_generation")
    previous = next((Path(path) for path in reversed(receipt.artifacts if receipt else [])
                     if str(path).endswith(".json") and Path(path).is_file()), None)
    if stage > 9 and not previous:
        return {"ok": False, "status": "missing_previous_result", "error": "latest receipt has no JSON artifact"}
    command = [sys.executable, str(source), "--input", str(source_data), "--stage", str(stage), "--output", str(output)]
    if previous:
        command.extend(["--previous", str(previous)])
    proc = subprocess.run(command, capture_output=True, text=True, timeout=300)
    if proc.returncode or not output.is_file():
        return {"ok": False, "status": "script_failed", "error": proc.stderr[-3000:], "files": [str(source)]}
    result = json.loads(output.read_text(encoding="utf-8"))
    if stage > 9 and not (result.get("lineage") or {}).get("consumed"):
        return {"ok": False, "status": "handoff_not_consumed", "result": result,
                "files": [str(source), str(output)]}
    if any(row.get("group_overlap") for row in result.get("folds", [])):
        return {"ok": False, "status": "group_leakage", "result": result, "files": [str(source), str(output)]}
    title, objective = STAGES[stage]
    excerpt = json.dumps(result, ensure_ascii=False, indent=2)[:14000]
    report = f"""# TargetDiff Stage {stage}：{title}

## 本轮目标

{objective}。本轮承接 Stage {stage-1} 的有效收据和同一字段合同；pK 是唯一监督目标，Vina/RMSD 只作特征。

## 真实执行

实际命令：`{' '.join(command)}`。源码由确定性事件写入并在独立子进程运行，退出码为零，机器 JSON 与报告位于同一 TaskInstance。

## 机器结果

```json
{excerpt}
```

## 结果解释

逐行记录并非独立样本：同一配体可能包含多个 min/docked 构象。当前聚合规则先去除文件名末尾构象后缀，再在靶点组和配体 identity 内取中位数。靶点组始终完整留在同一折，训练和测试 overlap 必须为零。

## 决策边界

非线性模型不能只凭逐行总体 RMSE 晋升。必须同时检查配体聚合五折、靶点等权宏观误差和靶点 Bootstrap 置信区间。即使 Stage 13 保留 candidate，也不自动 production promotion，更不构成药效因果结论。

## 新证据与承接

本阶段机器 JSON 是下一阶段的强制输入。Controller 只能沿 Stage 9–13 声明图补充任务；每个里程碑后由 05 摄取完成、失败、重试、产物和 QQ 回执。自由 batch_plan 不得插入这条主线。

## 限制

配体 identity 来自文件名解析，尚未用 RDKit InChIKey 或官方索引确认。官方 split 本地缺失，因此结果仍是独立防泄漏评估。未下载大型数据、未启动 GPU 训练，也未调节 HGB 超参数来迎合测试折。

## 统计层级

报告同时区分 row、ligand 和 target 三个层级。row 数量用于描述原始构象记录，不能直接当作独立实验样本数；ligand 聚合用于降低同一配体多构象的重复权重；target 等权和 target bootstrap 用于防止样本量最大的靶点主导总体结论。任何模型改善都必须说明发生在哪个层级，不能把逐行显著性替代跨靶点泛化。

## 运行治理

Controller 只在本阶段收据、产物和真实交付全部终态后生成下一 WorkItem。若队列暂时为空，它应等待新证据，而不是用泛化提示词制造工作。05 只在声明的里程碑运行，其候选策略不能直接改生产事件；晋升仍需独立 canary、最少样本数、回归测试和可执行回滚。

## 下一步

{('Stage '+str(stage+1)+'：'+STAGES[stage+1][0]) if stage < 13 else '若决策门通过，仅保留候选；下一新实验必须取得官方 split 或预注册新特征，否则进入 waiting_for_evidence'}。

## 验收与回滚

源码、退出码、JSON、详细 PDF、QQ 文件和摘要回执缺一不可。出现组泄漏、标签身份泄漏、重复签名或下一阶段未消费本结果时，本阶段不得视为项目进展，并回滚到上一有效收据。
"""
    md = working / f"targetdiff_stage_{stage}_report.md"
    md.write_text(report, encoding="utf-8")
    from partner.v2.pdf_events import atomic_generate_detailed_pdf
    pdf = working / f"targetdiff_stage_{stage}_report.pdf"
    pdf_result = atomic_generate_detailed_pdf(ctx, {"content": report, "output_path": str(pdf),
        "title": f"TargetDiff Stage {stage}：{title}", "min_content_chars": 1200, "min_sections": 8})
    if not pdf_result.get("ok"):
        return {"ok": False, "status": "pdf_failed", "error": pdf_result.get("error"),
                "files": [str(source), str(output), str(md)]}
    return {"ok": True, "status": "completed", "stage": stage,
            "summary": f"TargetDiff Stage {stage} 已真实运行：rows={result['row_records']}，ligands={result['aggregated_ligands']}，group_overlap=0",
            "result": result, "files": [str(source), str(output), str(md), str(pdf)], "path": str(pdf)}


def _handler(stage: int):
    return lambda ctx, params: atomic_targetdiff_continuous_stage(ctx, {**params, "stage": stage})


HANDLERS = {
    "targetdiff_ligand_aggregation_cv": _handler(9),
    "targetdiff_target_balanced_metrics": _handler(10),
    "targetdiff_failure_group_diagnostics": _handler(11),
    "targetdiff_group_bootstrap": _handler(12),
    "targetdiff_method_decision": _handler(13),
}

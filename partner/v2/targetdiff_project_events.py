"""Deterministic, leakage-resistant TargetDiff project milestones for instance 02."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from partner.governance.storage import workspace_root


PIPELINE_SOURCE = r'''
import argparse, hashlib, json, math, pickle, statistics
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

p=argparse.ArgumentParser(); p.add_argument('--input'); p.add_argument('--stage',type=int); p.add_argument('--output'); a=p.parse_args()
source=Path(a.input); raw=pickle.load(source.open('rb')); rows=[]
for key,value in raw.items():
 if not isinstance(value,dict): continue
 try: rmsd,pk,vina=float(value['rmsd']),float(value['pk']),float(value['vina'])
 except (KeyError,TypeError,ValueError): continue
 rows.append({'key':str(key),'group':str(key).split('/',1)[0],'rmsd':rmsd,'pk':pk,'vina':vina})
measured=[r for r in rows if math.isfinite(r['pk']) and r['pk']>0 and math.isfinite(r['vina']) and math.isfinite(r['rmsd'])]
def fold(group): return int(hashlib.sha256(group.encode()).hexdigest()[:8],16)%5
train=[r for r in measured if fold(r['group'])!=0]; test=[r for r in measured if fold(r['group'])==0]
def summary(xs):
 xs=sorted(float(x) for x in xs); n=len(xs)
 return {'count':n,'mean':statistics.fmean(xs),'median':statistics.median(xs),'min':xs[0],'max':xs[-1],
  'q10':xs[int(.1*(n-1))],'q90':xs[int(.9*(n-1))]}
def metrics(y,pred):
 return {'count':len(y),'rmse':float(mean_squared_error(y,pred)**.5),'mae':float(mean_absolute_error(y,pred)),
  'r2':float(r2_score(y,pred)) if len(y)>1 else None}
result={'ok':True,'stage':a.stage,'contract':{'target_field':'pk','feature_fields':['vina','rmsd'],
 'valid_target_rule':'pk > 0 and finite','group_field':'key prefix before first slash','split_rule':'sha256(group) modulo 5; fold 0 test',
 'causal_claim':False},'source':str(source),'source_size':source.stat().st_size,'records':len(rows),
 'measured_records':len(measured),'zero_or_invalid_pk':len(rows)-len(measured),'groups':len({r['group'] for r in rows}),
 'measured_groups':len({r['group'] for r in measured}),'train_count':len(train),'test_count':len(test),
 'train_groups':len({r['group'] for r in train}),'test_groups':len({r['group'] for r in test}),
 'group_overlap':len({r['group'] for r in train}&{r['group'] for r in test}),
 'summaries':{'pk':summary([r['pk'] for r in measured]),'vina':summary([r['vina'] for r in measured]),'rmsd':summary([r['rmsd'] for r in measured])}}
if a.stage>=2:
 X1=np.array([[r['vina']] for r in train]); X2=np.array([[r['vina'],r['rmsd']] for r in train]); y=np.array([r['pk'] for r in train])
 T1=np.array([[r['vina']] for r in test]); T2=np.array([[r['vina'],r['rmsd']] for r in test]); ty=np.array([r['pk'] for r in test])
 mean_pred=np.full(len(ty),float(y.mean())); linear=LinearRegression().fit(X1,y); linear2=LinearRegression().fit(X2,y)
 result['baselines']={'train_mean':metrics(ty,mean_pred),'vina_linear':metrics(ty,linear.predict(T1)),
  'vina_rmsd_linear':metrics(ty,linear2.predict(T2))}
 result['model_parameters']={'vina_linear':{'coef':linear.coef_.tolist(),'intercept':float(linear.intercept_)},
  'vina_rmsd_linear':{'coef':linear2.coef_.tolist(),'intercept':float(linear2.intercept_)}}
 result['identity_leakage_check']={'passed':not (result['baselines']['vina_linear']['rmse']==0 and abs(float(linear.coef_[0])-1)<1e-12),
  'feature_is_target':False,'target_field':'pk'}
if a.stage>=3:
 model=HistGradientBoostingRegressor(max_iter=160,max_leaf_nodes=31,learning_rate=.08,l2_regularization=1.0,random_state=42).fit(X2,y)
 hp=model.predict(T2); result['nonlinear']={'model':'HistGradientBoostingRegressor','params':model.get_params(),
  'test':metrics(ty,hp),'delta_rmse_vs_vina_linear':metrics(ty,hp)['rmse']-result['baselines']['vina_linear']['rmse']}
if a.stage>=4:
 pred=linear2.predict(T2); residual=ty-pred; strata={}
 for label,mask in {'rmsd_lt_0_5':T2[:,1]<.5,'rmsd_0_5_1':(T2[:,1]>=.5)&(T2[:,1]<1),'rmsd_ge_1':T2[:,1]>=1}.items():
  strata[label]=metrics(ty[mask],pred[mask]) if int(mask.sum()) else {'count':0}
 groups=defaultdict(list)
 for row,err in zip(test,residual): groups[row['group']].append(float(err))
 worst=[]
 for group,errs in groups.items():
  if len(errs)>=20: worst.append({'group':group,'count':len(errs),'rmse':float(np.sqrt(np.mean(np.square(errs)))),'bias':float(np.mean(errs))})
 result['residual_analysis']={'overall_bias':float(np.mean(residual)),'residual_std':float(np.std(residual)),
  'rmsd_strata':strata,'worst_groups':sorted(worst,key=lambda x:x['rmse'],reverse=True)[:15]}
if a.stage>=5:
 folds=[]
 for held in range(5):
  tr=[r for r in measured if fold(r['group'])!=held]; te=[r for r in measured if fold(r['group'])==held]
  tx=np.array([[r['vina'],r['rmsd']] for r in tr]); ty0=np.array([r['pk'] for r in tr]); ex=np.array([[r['vina'],r['rmsd']] for r in te]); ey=np.array([r['pk'] for r in te])
  lm=LinearRegression().fit(tx,ty0); folds.append({'fold':held,'train':len(tr),'test':len(te),**metrics(ey,lm.predict(ex))})
 result['group_cross_validation']={'folds':folds,'mean_rmse':statistics.fmean(x['rmse'] for x in folds),
  'std_rmse':statistics.pstdev(x['rmse'] for x in folds),'mean_mae':statistics.fmean(x['mae'] for x in folds)}
if a.stage>=6:
 sensitivity=[]
 for held in range(5):
  tr=[r for r in measured if fold(r['group'])!=held]; te=[r for r in measured if fold(r['group'])==held]
  tx=np.array([[r['vina'],r['rmsd']] for r in tr]); ty0=np.array([r['pk'] for r in tr]); ex=np.array([[r['vina'],r['rmsd']] for r in te]); ey=np.array([r['pk'] for r in te])
  lo,hi=np.quantile(tx[:,0],[.005,.995]); raw_lm=LinearRegression().fit(tx,ty0); clipped_tx=tx.copy(); clipped_ex=ex.copy()
  clipped_tx[:,0]=np.clip(clipped_tx[:,0],lo,hi); clipped_ex[:,0]=np.clip(clipped_ex[:,0],lo,hi)
  clipped_lm=LinearRegression().fit(clipped_tx,ty0); raw_m=metrics(ey,raw_lm.predict(ex)); clipped_m=metrics(ey,clipped_lm.predict(clipped_ex))
  sensitivity.append({'fold':held,'train_vina_bounds':[float(lo),float(hi)],'train_outliers':int(((tx[:,0]<lo)|(tx[:,0]>hi)).sum()),
   'test_outliers':int(((ex[:,0]<lo)|(ex[:,0]>hi)).sum()),'raw':raw_m,'clipped':clipped_m,'delta_rmse':clipped_m['rmse']-raw_m['rmse']})
 result['outlier_sensitivity']={'rule':'Vina clipped to training-fold 0.5%/99.5% quantiles only', 'folds':sensitivity,
  'mean_delta_rmse':statistics.fmean(x['delta_rmse'] for x in sensitivity),
  'improved_folds':sum(x['delta_rmse']<0 for x in sensitivity)}
if a.stage>=7:
 comparisons=[]
 for held in range(5):
  tr=[r for r in measured if fold(r['group'])!=held]; te=[r for r in measured if fold(r['group'])==held]
  tx=np.array([[r['vina'],r['rmsd']] for r in tr]); ty0=np.array([r['pk'] for r in tr]); ex=np.array([[r['vina'],r['rmsd']] for r in te]); ey=np.array([r['pk'] for r in te])
  lm=LinearRegression().fit(tx,ty0); gb=HistGradientBoostingRegressor(max_iter=160,max_leaf_nodes=31,learning_rate=.08,l2_regularization=1.0,random_state=42).fit(tx,ty0)
  linear_m=metrics(ey,lm.predict(ex)); nonlinear_m=metrics(ey,gb.predict(ex)); comparisons.append({'fold':held,'linear':linear_m,'nonlinear':nonlinear_m,'delta_rmse':nonlinear_m['rmse']-linear_m['rmse']})
 mean_delta=statistics.fmean(x['delta_rmse'] for x in comparisons); improved=sum(x['delta_rmse']<0 for x in comparisons)
 result['nonlinear_group_cross_validation']={'folds':comparisons,'mean_delta_rmse':mean_delta,'improved_folds':improved,
  'decision':'retain_nonlinear_candidate' if mean_delta<=-.05 and improved>=4 else 'retain_linear_baseline',
  'decision_rule':'nonlinear mean RMSE improvement >= 0.05 and improves at least 4/5 folds'}
json.dump(result,open(a.output,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
'''


STAGE_META = {
    1: ("数据字段合同", "确认 pK 目标、Vina/RMSD 特征、有效样本和按靶点组拆分规则"),
    2: ("分组防泄漏基线", "在完全不重叠的靶点组上比较训练均值、Vina 线性和 Vina+RMSD 线性模型"),
    3: ("非线性候选比较", "加入固定参数的直方图梯度提升，并与线性基线做同测试集比较"),
    4: ("残差与失败组分析", "按 RMSD 分层并定位样本充分但误差最大的靶点组"),
    5: ("五折分组稳健性", "对五个确定性靶点折逐一留出，报告均值和波动而非单次拆分"),
    6: ("Vina 异常值敏感性", "只用每个训练折的分位数裁剪 Vina，并比较五折原始与稳健线性基线"),
    7: ("非线性五折方法决策", "在五个相同留出组逐折比较线性与非线性模型，用预声明门决定是否保留复杂模型"),
}


def _paths(ctx: Any) -> tuple[Path, Path]:
    root = workspace_root(str(getattr(ctx, "workspace", "")))
    task = getattr(ctx, "task_instance", None)
    working = Path(str(getattr(task, "working_dir", "") or getattr(ctx, "working_dir", "")))
    working.mkdir(parents=True, exist_ok=True)
    return root, working


def _report(stage: int, result: dict, command: list[str]) -> str:
    title, objective = STAGE_META[stage]
    excerpt = json.dumps(result, ensure_ascii=False, indent=2)[:9000]
    previous = "无；本阶段建立主线合同" if stage == 1 else f"承接 Stage {stage-1} 的同一数据合同和机器结果"
    return f"""# TargetDiff 单项目主线 Stage {stage}：{title}

## 本轮目标

{objective}。本轮只把 `pk` 作为监督目标，把 `vina/rmsd` 作为特征；不会再出现把 Vina 同时作为 X 与 y 的身份回归。

## 承接关系

{previous}。所有阶段使用同一个 `affinity_info.pkl`，按 key 第一个斜杠前的靶点组做 SHA256 固定分折，训练和测试组重叠必须为零。

## 真实执行

实际命令：`{' '.join(command)}`。源码、退出码和 JSON 位于当前 TaskInstance；结果来自子进程，不以设计文档或语言模型自评代替运行。

## 机器结果

```json
{excerpt}
```

## 科学解释

该数据支持数据集内的 pK 统计预测比较，但不支持药效因果、临床效果或新分子实验活性的主张。分组拆分比逐行随机拆分更严格，因为同一靶点的多个构象或配体记录不会跨越训练和测试。若非线性模型改善很小或跨折波动较大，应保留简单模型，而不是用复杂度冒充创新。

## 字段与单位解释

`pk` 是本项目当前唯一监督标签，只有有限且严格大于零的记录进入拟合；零值按缺测处理，绝不能擅自解释为无活性。`vina` 是已有对接评分，只作为候选解释变量；它和实验亲和力并不等价。`rmsd` 描述已有构象偏差，同样只能作为特征。记录 key 的首段暂作靶点/复合物组标识，目的是阻止同源记录跨越训练和测试。报告同时保留原始总记录数、有效记录数、组数和分折规模，后续接手者可据此复算。

## 模型决策规则

训练均值是无特征下限，Vina 线性模型是可解释基线，Vina+RMSD 用来检验构象信息是否增加测试集价值。非线性候选必须在完全相同的留出组上降低 RMSE，且不能以明显的跨折不稳定为代价；否则维持线性模型。残差分析只用于提出下一项可证伪实验，例如针对误差最大且样本充足的靶点组补充特征，不能反向挑选有利测试集。五折结果用于判断改善是否可重复，不以最好单折代替总体证据。

## 审计与回滚

每个结论都能追溯到本 TaskInstance 中的 Python 源码、输入绝对路径、固定随机参数和 JSON 输出。如果标签误用、组泄漏、输入变化、执行非零退出或 PDF 未真实送达，本阶段收据必须拒绝，项目继续停留在当前里程碑。旧的错误结论通过追加 correction 失效而不删除历史；回滚时保留最后一个通过语义门和交付门的阶段结果。

## 验收门

必须同时满足：有效 `pk>0` 样本非零、`target_field=pk`、训练/测试靶点组重叠为零、脚本退出码为零、JSON 可解析、详细 PDF 质量通过并获得真实 QQ 回执。任何一个条件失败都不得进入下一阶段。

## 下一里程碑

{('Stage '+str(stage+1)+'：'+STAGE_META[stage+1][0]) if stage < 7 else '形成方法选择收据，并核对官方数据字典/split 或针对最差靶点组设计新特征实验'}。下一阶段必须消费本阶段结果，不能回到旧的 QED/SA 排序。

## 可复现限制

当前组键来自 TargetDiff 记录路径前缀，是可审计的工程分组近似；后续应与数据集官方 target/complex split 对照。pK 为数据中的现有数值，零值被视为缺测而不是负样本。模型比较只在同一分组测试集上有效。
"""


def atomic_targetdiff_project_slice(ctx: Any, params: dict) -> dict:
    root, working = _paths(ctx)
    stage = max(1, min(7, int(params.get("stage") or 1)))
    source = root / "external" / "targetdiff" / "data" / "affinity_info.pkl"
    if not source.is_file():
        return {"ok": False, "status": "missing_targetdiff_affinity", "error": str(source)}
    script = working / f"targetdiff_stage_{stage}.py"
    output = working / f"targetdiff_stage_{stage}_result.json"
    script.write_text(PIPELINE_SOURCE, encoding="utf-8")
    command = [sys.executable, str(script), "--input", str(source), "--stage", str(stage), "--output", str(output)]
    proc = subprocess.run(command, text=True, capture_output=True, timeout=300, check=False)
    if proc.returncode or not output.is_file():
        return {"ok": False, "status": "script_failed", "exit_code": proc.returncode,
                "stdout": proc.stdout[-3000:], "stderr": proc.stderr[-3000:], "files": [str(script)]}
    result = json.loads(output.read_text(encoding="utf-8"))
    contract = result.get("contract") or {}
    if contract.get("target_field") != "pk" or result.get("group_overlap") != 0:
        return {"ok": False, "status": "semantic_gate_failed", "result": result, "files": [str(script), str(output)]}
    if stage >= 2 and not (result.get("identity_leakage_check") or {}).get("passed"):
        return {"ok": False, "status": "identity_leakage", "result": result, "files": [str(script), str(output)]}
    report = _report(stage, result, command)
    md = working / f"targetdiff_stage_{stage}_report.md"
    md.write_text(report, encoding="utf-8")
    from partner.v2.pdf_events import atomic_generate_detailed_pdf
    pdf = working / f"targetdiff_stage_{stage}_report.pdf"
    pdf_result = atomic_generate_detailed_pdf(ctx, {"content": report, "output_path": str(pdf),
        "title": f"TargetDiff Stage {stage}: {STAGE_META[stage][0]}", "quality_profile": "detailed",
        "min_content_chars": 1200, "min_sections": 6})
    if not pdf_result.get("ok"):
        return {"ok": False, "status": "pdf_failed", "error": pdf_result.get("error"),
                "quality": pdf_result.get("quality"), "files": [str(script), str(output), str(md)]}
    return {"ok": True, "status": "completed", "stage": stage, "result": result,
            "summary": f"TargetDiff Stage {stage} 已真实运行：measured={result['measured_records']}，group_overlap=0",
            "files": [str(script), str(output), str(md), str(pdf)], "path": str(pdf),
            "quality": pdf_result.get("quality")}


def _stage_handler(stage: int):
    def handler(ctx: Any, params: dict) -> dict:
        return atomic_targetdiff_project_slice(ctx, {**params, "stage": stage})
    return handler


HANDLERS = {
    "targetdiff_project_slice": atomic_targetdiff_project_slice,
    "targetdiff_data_contract": _stage_handler(1),
    "targetdiff_group_baseline": _stage_handler(2),
    "targetdiff_nonlinear_compare": _stage_handler(3),
    "targetdiff_residual_analysis": _stage_handler(4),
    "targetdiff_group_cv": _stage_handler(5),
    "targetdiff_outlier_sensitivity": _stage_handler(6),
    "targetdiff_nonlinear_group_cv": _stage_handler(7),
}

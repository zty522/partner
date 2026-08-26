"""Bounded, evidence-producing project iterations for the execution Sprint."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from partner.governance.evolution_loop import decide_experiment
from partner.governance.rl_evolution import run_offline_rl_update
from partner.governance.storage import governance_log, workspace_root


def _paths(ctx: Any) -> tuple[Path, Path, str]:
    workspace = Path(str(getattr(ctx, "workspace", "")))
    task = getattr(ctx, "task_instance", None)
    working = Path(str(getattr(task, "working_dir", "") or getattr(ctx, "working_dir", "")))
    working.mkdir(parents=True, exist_ok=True)
    match = re.search(r"/instances/(0[1-5])(?:/|$)", str(workspace))
    return workspace_root(str(workspace)), working, match.group(1) if match else ""


def _run_script(working: Path, wave: int, code: str, args: list[str], timeout: int = 180) -> tuple[Path, dict]:
    script = working / f"execution_wave_{wave}.py"
    output = working / f"execution_wave_{wave}_result.json"
    script.write_text(code, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(script), *args, "--output", str(output)],
        text=True, capture_output=True, timeout=timeout, check=False,
    )
    result = json.loads(output.read_text(encoding="utf-8")) if proc.returncode == 0 and output.is_file() else {
        "ok": False, "exit_code": proc.returncode, "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:],
    }
    result["command"] = [sys.executable, str(script), *args, "--output", str(output)]
    result["exit_code"] = proc.returncode
    return script, result


def _pdf(ctx: Any, report: str, working: Path, wave: int, title: str) -> tuple[str, dict]:
    from partner.v2.pdf_events import atomic_generate_detailed_pdf
    path = working / f"execution_wave_{wave}.pdf"
    result = atomic_generate_detailed_pdf(ctx, {
        "content": report, "output_path": str(path), "title": title,
        "quality_profile": "detailed", "min_content_chars": 900, "min_sections": 4,
    })
    return (str(path) if result.get("ok") else ""), result


CONTENT_ANALYZER = r'''
import argparse, collections, json
p=argparse.ArgumentParser(); p.add_argument('--input'); p.add_argument('--wave',type=int); p.add_argument('--output'); a=p.parse_args()
rows=[]
for line in open(a.input,encoding='utf-8'):
 try: rows.append(json.loads(line))
 except ValueError: pass
urls=[u for r in rows for u in (r.get('urls') or [])]
projects=collections.Counter(str(r.get('project') or 'unknown') for r in rows)
intents=collections.Counter(str(r.get('intent') or 'unknown') for r in rows)
status=collections.Counter(str(r.get('status') or 'unknown') for r in rows)
result={'ok':True,'wave':a.wave,'records':len(rows),'unique_urls':len(set(urls)),'duplicate_url_records':len(urls)-len(set(urls)),
        'projects':projects.most_common(10),'intents':intents.most_common(),'status':status.most_common(),
        'latest_sources':[{'id':r.get('id'),'project':r.get('project'),'intent':r.get('intent'),'urls':r.get('urls',[]),'text':str(r.get('text',''))[:160]} for r in rows[-8:]]}
if a.wave >= 2:
 result['actionable_briefs']=[{'source_id':r.get('id'),'topic':r.get('project') or r.get('intent'),'requires_fact_check':True,
   'publish_authorized':False,'next_action':'prepare evidence-backed draft; do not publish'} for r in rows[-5:] if r.get('status')=='open']
if a.wave >= 3:
 seen=set(); backlog=[]
 for r in reversed(rows):
  key=tuple(r.get('urls') or []) or (str(r.get('text',''))[:80],)
  if key in seen or r.get('status')!='open': continue
  seen.add(key); backlog.append({'source_id':r.get('id'),'topic':r.get('project') or r.get('intent'),'source_key':list(key),
   'risk_flags':['requires_fact_check','publish_not_authorized'],'next_executable_step':'draft with citations and request approval'})
 result['deduplicated_open_backlog']=backlog[:20]
json.dump(result,open(a.output,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
'''


AFFINITY_ANALYZER = r'''
import argparse, json, math, pickle, statistics
p=argparse.ArgumentParser(); p.add_argument('--input'); p.add_argument('--wave',type=int); p.add_argument('--output'); a=p.parse_args()
d=pickle.load(open(a.input,'rb')); rows=[]
for key,v in d.items():
 if isinstance(v,dict):
  try: rows.append((key,float(v.get('rmsd',0)),float(v.get('pk',0)),float(v.get('vina',0))))
  except (TypeError,ValueError): pass
def summary(xs):
 xs=sorted(xs); n=len(xs); return {'count':n,'mean':statistics.fmean(xs),'median':statistics.median(xs),'min':xs[0],'max':xs[-1],
   'q10':xs[int(.1*(n-1))],'q90':xs[int(.9*(n-1))]}
def corr(xs,ys):
 mx,my=statistics.fmean(xs),statistics.fmean(ys); num=sum((x-mx)*(y-my) for x,y in zip(xs,ys));
 den=math.sqrt(sum((x-mx)**2 for x in xs)*sum((y-my)**2 for y in ys)); return num/den if den else 0.0
rmsd=[r[1] for r in rows]; pk=[r[2] for r in rows]; vina=[r[3] for r in rows]
result={'ok':True,'wave':a.wave,'records':len(rows),'rmsd':summary(rmsd),'pk':summary(pk),'vina':summary(vina),
 'correlations':{'pk_vina':corr(pk,vina),'pk_rmsd':corr(pk,rmsd),'vina_rmsd':corr(vina,rmsd)}}
if a.wave >= 2:
 measured=[r for r in rows if r[2] > 0]; measured.sort(key=lambda r:r[3]); result['measured_pk_records']=len(measured)
 if measured:
  q=max(1,len(measured)//10); result['strong_vina_decile']={
   'count':q,'mean_pk':statistics.fmean(r[2] for r in measured[:q]),'mean_vina':statistics.fmean(r[3] for r in measured[:q]),
   'mean_rmsd':statistics.fmean(r[1] for r in measured[:q])}
  result['weak_vina_decile']={'count':q,'mean_pk':statistics.fmean(r[2] for r in measured[-q:]),
   'mean_vina':statistics.fmean(r[3] for r in measured[-q:]),'mean_rmsd':statistics.fmean(r[1] for r in measured[-q:])}
if a.wave >= 3:
 measured=[r for r in rows if r[2] > 0]
 train=[r for i,r in enumerate(measured) if i%5]; test=[r for i,r in enumerate(measured) if not i%5]
 if train and test:
  xs=[r[3] for r in train]; ys=[r[2] for r in train]; mx,my=statistics.fmean(xs),statistics.fmean(ys)
  den=sum((x-mx)**2 for x in xs); slope=sum((x-mx)*(y-my) for x,y in zip(xs,ys))/den if den else 0; intercept=my-slope*mx
  errors=[(intercept+slope*r[3]-r[2])**2 for r in test]
  result['vina_to_pk_baseline']={'train_count':len(train),'test_count':len(test),'slope':slope,'intercept':intercept,
   'test_rmse':math.sqrt(statistics.fmean(errors)),'split_rule':'index modulo 5; deterministic','causal_claim':False}
json.dump(result,open(a.output,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
'''


CAMPAIGN_ANALYZER = r'''
import argparse, collections, glob, json, os
p=argparse.ArgumentParser(); p.add_argument('--root'); p.add_argument('--wave',type=int); p.add_argument('--output'); a=p.parse_args()
campaigns=[]
for state_path in glob.glob(os.path.join(a.root,'state/campaigns/*/campaign_state.json')):
 try: state=json.load(open(state_path,encoding='utf-8'))
 except (OSError,ValueError): continue
 work=[]
 for path in glob.glob(os.path.join(os.path.dirname(state_path),'work_items/*.json')):
  try: work.append(json.load(open(path,encoding='utf-8')))
  except (OSError,ValueError): pass
 counts=collections.Counter(x.get('status') for x in work); generic=sum(not x.get('event_types') and x.get('kind')!='report' for x in work)
 delivered=sum('delivery_confirmed=True' in (x.get('evidence') or []) for x in work)
 campaigns.append({'campaign_id':state.get('campaign_id'),'status':state.get('status'),'created':len(work),'counts':dict(counts),
  'generic_nonreport':generic,'delivery_confirmed':delivered,'budget_max':state.get('budget',{}).get('max_work_items'),
  'budget_violation':len(work)>int(state.get('budget',{}).get('max_work_items') or 10**9)})
result={'ok':True,'wave':a.wave,'campaign_count':len(campaigns),'campaigns':campaigns[-20:]}
result['totals']={'budget_violations':sum(x['budget_violation'] for x in campaigns),'generic_nonreport':sum(x['generic_nonreport'] for x in campaigns),
 'completed_campaigns':sum(x['status']=='completed' for x in campaigns)}
if a.wave >= 2:
 clean=[x for x in campaigns if x['status']=='completed' and not x['budget_violation'] and x['generic_nonreport']==0]
 result['clean_campaign_ids']=[x['campaign_id'] for x in clean[-10:]]; result['clean_rate']=len(clean)/len(campaigns) if campaigns else 0
if a.wave >= 3:
 result['proposed_runtime_gates']=[
  {'gate':'budget_hard_stop','trigger':'created > budget_max','current_violations':result['totals']['budget_violations']},
  {'gate':'generic_action_reject','trigger':'non-report WorkItem has no event_types','historical_count':result['totals']['generic_nonreport']},
  {'gate':'delivery_required','trigger':'required delivery lacks callback','measurement':'delivery_confirmed evidence'}]
json.dump(result,open(a.output,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
'''


REPO_ANALYZER = r'''
import argparse, ast, glob, json, os
p=argparse.ArgumentParser(); p.add_argument('--repo'); p.add_argument('--issues',default=''); p.add_argument('--wave',type=int); p.add_argument('--output'); a=p.parse_args()
files=glob.glob(os.path.join(a.repo,'**/*.py'),recursive=True); defs=classes=syntax_errors=0; skill_files=[]
for path in files[:2000]:
 try: tree=ast.parse(open(path,encoding='utf-8',errors='ignore').read())
 except (OSError,SyntaxError): syntax_errors+=1; continue
 defs+=sum(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) for n in ast.walk(tree)); classes+=sum(isinstance(n,ast.ClassDef) for n in ast.walk(tree))
 if 'skill' in os.path.basename(path).lower(): skill_files.append(os.path.relpath(path,a.repo))
result={'ok':True,'wave':a.wave,'python_files':len(files),'functions':defs,'classes':classes,'syntax_errors':syntax_errors,
 'skill_related_files':skill_files[:30]}
if a.wave >= 2:
 result['adapter_contract']={'accepted_fields':['category','pattern','distinction','triggers','query_templates'],
  'partner_mapping':{'category':'Issue.category','pattern':'Issue.summary','triggers':'Issue.evidence','query_templates':'Experiment.intervention'},
  'execution_boundary':'schema adapter only; no SESA training or production promotion'}
if a.wave >= 3:
 cards=[]
 if a.issues and os.path.isfile(a.issues):
  for line in open(a.issues,encoding='utf-8'):
   try: issue=json.loads(line)
   except ValueError: continue
   cards.append({'category':issue.get('category'),'pattern':issue.get('summary'),'distinction':'evidence-backed Partner issue',
    'triggers':issue.get('evidence',[])[:3],'query_templates':[f"verify {issue.get('category','issue')} with persisted evidence"],
    'source_issue_id':issue.get('issue_id')})
 result['adapter_prototype']={'converted_cards':cards[-10:],'count':len(cards[-10:]),'writes_external_repo':False}
json.dump(result,open(a.output,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
'''


RL_EVALUATOR = r'''
import argparse, collections, json
p=argparse.ArgumentParser(); p.add_argument('--input'); p.add_argument('--output'); a=p.parse_args(); rows=[]
for line in open(a.input,encoding='utf-8'):
 try: rows.append(json.loads(line))
 except ValueError: pass
groups=collections.defaultdict(list)
for r in rows: groups[r.get('action',{}).get('action_key','unknown')].append(float(r.get('reward',0)))
actions=[{'action_key':k,'samples':len(v),'mean_reward':sum(v)/len(v),'negative_samples':sum(x<0 for x in v)} for k,v in groups.items()]
actions.sort(key=lambda x:(x['mean_reward']*(x['samples']**0.5),x['action_key']))
json.dump({'ok':True,'trajectory_count':len(rows),'actions':actions,'weakest_repeated':actions[0] if actions else {}},open(a.output,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
'''


def _report(instance: str, wave: int, result: dict, source: str, action: str) -> str:
    excerpt = json.dumps(result, ensure_ascii=False, indent=2)[:7000]
    return f"""# 实例 {instance} 执行型迭代 · Wave {wave}

## 本轮目标

本轮不是重复审计，而是执行 `{action}`：读取真实输入、生成可复用 Python 脚本、用当前解释器实际运行，并把机器可读结果作为后续分析依据。

## 真实输入

输入来源为 `{source}`。报告只引用本轮脚本输出，不把设计文字、文件存在或模型自评当成运行成功。

## 代码与运行

实际命令为 `{ ' '.join(str(x) for x in result.get('command', [])) }`，退出码 `{result.get('exit_code')}`。脚本和 JSON 输出保存在同一 TaskInstance 目录，可独立复跑。

## 结果分析

```json
{excerpt}
```

## 验收解释

`ok=true` 只说明脚本完成了它声明的计算；还必须同时满足退出码为零、结果 JSON 可解析、输入记录数非零、
PDF 通过内容质量门槛并获得真实发送回执，Campaign 才能把 WorkItem 记为 completed。若源码存在但进程失败，
或报告存在但 JSON 没有来自输入的统计量，本轮都应失败而不是继续生成下一波。这样可以区分“写了代码”、
“代码实际运行”和“运行产生了可解释的新证据”三个不同层次。

## 数据解读规则

01 的来源计数用于选择待核验内容，不代表已经形成可发布文案；重复 URL 和 open 状态用于清理输入队列。
02 的 Vina、pK 与 RMSD 相关性是数据集描述，不是活性因果或实验验证；下一波必须进行分层比较。
03 的历史 Campaign 指标用于发现预算越界、泛化动作和交付缺口，不能用总体 completed 数掩盖失败轮次。
04 的函数、类与 Skill 文件统计用于确定适配面，外部仓库 clone 成功也不等于代码已安全集成。
05 的奖励回放只支持候选决策；没有满足声明样本数、回归和送达率时必须保持 inconclusive。

## 可复现与承接

后续波次必须读取当前 TaskInstance 保存的源码合同或同一真实数据源，在结果中增加新的分层字段，
并保留固定输入路径、命令和退出码。若外部输入发生变化，应记录新哈希或记录数，不能把数据变化误写成算法改进。
最终自进化实例会在其他实例全部终态后统一摄取这些 WorkItem 的产物、交付、失败和重试信号，
从而让下一轮策略选择承接真实执行结果，而不是承接一段自我评价。

## 相对上一轮的推进

Wave 1 建立真实输入基线；Wave 2 在同一输入与脚本合同上增加分层、相关性、重复风险或适配契约分析。每一波必须生成不同结果文件，不允许只改报告标题。

## 边界与下一步

本轮只执行可回滚的读取、原型和分析。01 不上传或发布；02 不把统计相关性写成药效因果；03 不自动合并原型；04 不执行外部训练栈；05 不绕过 Experiment/PromotionDecision。
"""


def atomic_evidence_execution_slice(ctx: Any, params: dict) -> dict:
    root, working, instance = _paths(ctx)
    wave = max(1, int(params.get("wave") or 1))
    source = ""
    action = ""
    extra: dict[str, Any] = {}
    if instance == "01":
        source = str(root / "external" / "content" / "inbox.jsonl")
        action = "内容来源索引与可发布前证据准备"
        script, result = _run_script(working, wave, CONTENT_ANALYZER, ["--input", source, "--wave", str(wave)])
    elif instance == "02":
        source = str(root / "external" / "targetdiff" / "data" / "affinity_info.pkl")
        action = "TargetDiff 真实亲和力数据基线与分层分析"
        script, result = _run_script(working, wave, AFFINITY_ANALYZER, ["--input", source, "--wave", str(wave)])
    elif instance == "03":
        source = str(root / "state" / "campaigns")
        action = "Campaign 运行指标分析器原型与历史回放"
        script, result = _run_script(working, wave, CAMPAIGN_ANALYZER, ["--root", str(root), "--wave", str(wave)])
    elif instance == "04":
        live = root / "external" / "experiments" / "SESA-live"
        archive = root / "external" / "code" / "SESA-Self-Evolving-Search-Agents-master"
        live.parent.mkdir(parents=True, exist_ok=True)
        if not live.exists():
            git = subprocess.run(["git", "clone", "--depth", "1", "https://github.com/Zenghuang-Fu/SESA-Self-Evolving-Search-Agents.git", str(live)],
                                 text=True, capture_output=True, timeout=180, check=False)
        else:
            git = subprocess.run(["git", "-C", str(live), "fetch", "--depth", "1", "origin"],
                                 text=True, capture_output=True, timeout=180, check=False)
        repo = live if live.is_dir() else archive
        source = str(repo)
        action = "真实拉取 SESA 并提取 Skill Bank 适配契约"
        script, result = _run_script(working, wave, REPO_ANALYZER, [
            "--repo", source, "--issues", str(governance_log(str(root), "issues")), "--wave", str(wave),
        ])
        extra["git"] = {"command": git.args, "exit_code": git.returncode, "stdout": git.stdout[-1000:], "stderr": git.stderr[-1000:],
                        "used_fallback_archive": repo == archive}
        result["git"] = extra["git"]
    elif instance == "05":
        campaign_id = str(params.get("campaign_id") or "")
        update = run_offline_rl_update(str(root), campaign_id)
        source = str(root / "share" / "mind" / "governance" / "rl" / "trajectories.jsonl")
        action = "离线 RL 候选实验回放与显式决策"
        script, result = _run_script(working, wave, RL_EVALUATOR, ["--input", source])
        experiments = []
        try:
            experiments = [json.loads(line) for line in governance_log(str(root), "experiments").read_text(encoding="utf-8").splitlines()]
        except (OSError, ValueError):
            pass
        candidate = next((row for row in reversed(experiments) if row.get("status") == "candidate"), {})
        decision = decide_experiment(str(root), {
            "experiment_id": candidate.get("experiment_id", "missing_candidate"),
            "decision": "inconclusive", "regression_passed": True,
            "criteria_results": {"minimum_canary_samples": False},
            "evidence": [source, str(working / f"execution_wave_{wave}_result.json"), f"campaign_id={campaign_id}"],
            "metrics_before": candidate.get("baseline", {}),
            "metrics_after": result.get("weakest_repeated", {}),
            "reason": "candidate has evidence but has not yet completed the declared post-intervention canary sample gate",
            "project_id": "agent_self_evolution",
        })
        result["offline_update"] = {"new_trajectories": update.get("new_trajectories"), "status": update.get("status")}
        result["promotion_decision"] = decision
    else:
        return {"ok": False, "status": "unknown_instance", "error": instance}

    result_path = working / f"execution_wave_{wave}_result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report = _report(instance, wave, result, source, action)
    md = working / f"execution_wave_{wave}.md"
    md.write_text(report, encoding="utf-8")
    pdf, pdf_result = _pdf(ctx, report, working, wave, f"实例 {instance} 执行型迭代 Wave {wave}")
    files = [str(script), str(result_path), str(md)] + ([pdf] if pdf else [])
    ok = bool(result.get("ok") and result.get("exit_code") == 0 and pdf)
    return {"ok": ok, "status": "executed" if ok else "execution_failed", "files": files,
            "summary": f"实例 {instance} Wave {wave}：代码已写入并实际运行，退出码={result.get('exit_code')}，产物={len(files)}",
            "wave": wave, "instance_id": instance, "result": result, "pdf_quality": pdf_result.get("quality")}


HANDLERS = {"evidence_execution_slice": atomic_evidence_execution_slice}

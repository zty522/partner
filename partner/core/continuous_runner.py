"""
Continuous Task Engine — keeps Partner running without stopping.
"""
from __future__ import annotations
import json, logging, os, time
from datetime import datetime
from typing import Callable, Optional

logger = logging.getLogger(__name__)

class ContinuationLoop:
    def __init__(self, workspace: str, project_dir: str = "projects/molgen_exploration",
                 max_rounds: int = 0, on_new_task: Optional[Callable] = None):
        self.workspace = workspace
        self.project_dir = os.path.join(workspace, project_dir)
        self.max_rounds = max_rounds
        self.on_new_task = on_new_task
        for d in ["data/pockets", "data/references", "data/generated", "scripts", "results", "reports"]:
            os.makedirs(os.path.join(self.project_dir, d), exist_ok=True)

    def _state_path(self): return os.path.join(self.project_dir, "state.json")

    def _load_state(self):
        p = self._state_path()
        if os.path.exists(p):
            try:
                with open(p) as f: return json.load(f)
            except: pass
        return {"current_round": 0, "completed_rounds": [], "project": "molecular_generation"}

    def _save_state(self, s):
        os.makedirs(os.path.dirname(self._state_path()), exist_ok=True)
        with open(self._state_path(), 'w') as f: json.dump(s, f, indent=2, ensure_ascii=False)

    def record_completion(self, rnd, summary, artifacts=None):
        s = self._load_state()
        s["current_round"] = rnd
        s["completed_rounds"].append({"round": rnd, "summary": summary[:500], "artifacts": artifacts or []})
        self._save_state(s)

    def should_continue(self):
        s = self._load_state()
        return self.max_rounds <= 0 or s.get("current_round", 0) < self.max_rounds

    def get_next_round(self):
        s = self._load_state()
        n = s.get("current_round", 0) + 1
        phases = [
            ("baseline", f"【分子生成 R{n}】PocketFlow ZINC 50分子, CPU, ompa口袋。RDKit分析QED/LogP/MW/HBA/HBD/TPSA分布, 直方图。results/round_{n:03d}/。PDF报告+QQ通知。"),
            ("compare", f"【分子生成 R{n}】PubChem模型50分子。对比ZINC: 多样性/环系/官能团/性质分布差异。箱线图+重叠直方图。results/round_{n:03d}/。"),
            ("temperature", f"【分子生成 R{n}】5温度(0.3/0.5/1.0/1.5/2.0)各20分子ZINC。Validity/Uniqueness/Novelty vs 温度折线图。results/round_{n:03d}/。"),
            ("filter", f"【分子生成 R{n}】RDKit筛选: QED>0.4, SA<4, LogP1-5, MW200-500。top20。复用脚本scripts/filter_pipeline.py。results/round_{n:03d}/。"),
            ("visualize", f"【分子生成 R{n}】ECFP4 PCA化学空间散点图。模型/温度/筛选前后分布对比。results/round_{n:03d}/。"),
            ("energy", f"【分子生成 R{n}】top10选5, AmberTools能量最小化(source amber.sh&&antechamber)。fallback RDKit MMFF94。results/round_{n:03d}/。"),
            ("synthesis", f"【分子生成 R{n}】综合分析。提出>=1创新方案(两阶段/退火/投票)。实现+测试vs基线。reports/final_synthesis.pdf。QQ通知。"),
        ]
        if n <= len(phases):
            title, task = phases[n-1]
            return {"round": n, "title": title, "text": task, "auto_generated": False}
        completed = s.get("completed_rounds", [])
        lasts = [c.get("summary","") for c in completed[-3:]]
        return {"round": n, "title": f"auto_{n}", "text": f"【分子生成 R{n}自主】已{len(completed)}批。自提新方向。历史:{'; '.join(lasts[:2])}", "auto_generated": True}

    def inject_next_task(self):
        if not self.should_continue(): return False
        nr = self.get_next_round()
        if not nr: return False
        import uuid as _u
        msg = {"id": str(_u.uuid4()), "message_id": f"auto_{nr['round']}", "source": "continuous_engine", "text": nr["text"], "ts": time.time(), "sender_name": "Engine"}
        inbox = os.path.join(self.workspace, "state", "desktop_inbox.jsonl")
        with open(inbox, 'a') as f: f.write(json.dumps(msg, ensure_ascii=False) + '\n')
        logger.info("[CONTINUOUS] round %d injected: %s", nr["round"], nr["title"])
        return True

_global_loop = None

def get_loop(workspace):
    global _global_loop
    if _global_loop is None: _global_loop = ContinuationLoop(workspace=workspace, max_rounds=0)
    return _global_loop

def check_and_continue(workspace):
    loop = get_loop(workspace)
    ip = os.path.join(workspace, "state", "desktop_inbox.jsonl")
    try:
        if os.path.exists(ip):
            with open(ip) as f:
                if len([l for l in f if l.strip()]) > 0: return False
    except: pass
    return loop.inject_next_task()

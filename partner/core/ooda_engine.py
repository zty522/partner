"""
OODA Engine v4 - LLM-driven perpetual research loop with circuit breaker.

v4 changes (2026-08-06):
- CircuitBreaker: LLM-analyzes failures, decides pause/skip/retry (no hardcoded keywords)
- _build_research_task: LLM generates plan dynamically (not template-driven)
- Fixed round directory naming: round_NNN_YYYYMMDD_HHMMSS (not LLM output text)
- _ensure_round_dir: reuses existing round dirs instead of creating duplicates
"""

from __future__ import annotations
import json, logging, os, re, time, sqlite3
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Tracks consecutive failures, delegates pause/skip decisions to LLM."""

    def __init__(self, max_failures: int = 5, reset_timeout: int = 1800):
        self.max_failures = max_failures
        self.reset_timeout = reset_timeout
        self.failure_count: dict[str, int] = {}
        self.failure_history: dict[str, list[dict]] = {}
        self.last_attempt: dict[str, float] = {}

    def record_failure(self, task_key: str, error_context: dict) -> int:
        self.failure_count[task_key] = self.failure_count.get(task_key, 0) + 1
        self.last_attempt[task_key] = time.time()
        if task_key not in self.failure_history:
            self.failure_history[task_key] = []
        self.failure_history[task_key].append({
            "timestamp": time.time(),
            "error": error_context.get("error_message", ""),
            "step": error_context.get("step", ""),
            "artifacts": error_context.get("artifacts", []),
        })
        self.failure_history[task_key] = self.failure_history[task_key][-10:]
        count = self.failure_count[task_key]
        logger.info("[Breaker] %s failure %d/%d", task_key, count, self.max_failures)
        return count

    def is_open(self, task_key: str) -> bool:
        if task_key not in self.failure_count:
            return False
        if time.time() - self.last_attempt.get(task_key, 0) > self.reset_timeout:
            self.reset(task_key)
            return False
        return self.failure_count[task_key] > self.max_failures

    def reset(self, task_key: str) -> None:
        self.failure_count[task_key] = 0
        self.failure_history[task_key] = []
        self.last_attempt.pop(task_key, None)
        logger.info("[Breaker] reset %s", task_key)

    def get_failure_context(self, task_key: str) -> dict:
        return {
            "failure_count": self.failure_count.get(task_key, 0),
            "history": self.failure_history.get(task_key, []),
        }

class OODAEngine:
    """Perpetual research engine - completely domain-agnostic."""

    def __init__(self, workspace: str, project: str = "molgen_exploration",
                 instance_id: str = "", adapter=None):
        self.workspace = workspace
        self.instance_id = instance_id
        self.adapter = adapter
        ws_root = os.path.dirname(os.path.dirname(workspace))
        self.project_dir = os.path.join(ws_root, "shared_projects", project)
        self.data_dir = os.path.join(self.project_dir, "data")
        self.breaker = CircuitBreaker()
        self._ensure_dirs()
        self._fix_truncated_paths()
        self._init_rl_db()
        self._config = self._load_config()
        self._knowledge_docs = self._load_knowledge_files()
        logger.info("[OODA-v4] Project: %s, docs: %d, instance: %s",
                    self._config.get('name', '?'), len(self._knowledge_docs), instance_id)

    def _load_config(self) -> dict:
        config_path = os.path.join(self.project_dir, "config.yaml")
        if os.path.exists(config_path):
            try:
                import yaml
                with open(config_path) as f:
                    return yaml.safe_load(f) or {}
            except Exception:
                pass
        return {"name": os.path.basename(self.project_dir)}

    def _load_knowledge_files(self) -> dict[str, str]:
        docs = {}
        if os.path.isdir(self.data_dir):
            for fname in sorted(os.listdir(self.data_dir)):
                if fname.endswith('.txt'):
                    try:
                        with open(os.path.join(self.data_dir, fname), encoding='utf-8') as f:
                            docs[fname] = f.read()
                    except Exception:
                        pass
        return docs

    def _fix_truncated_paths(self):
        import glob as _fg
        for d in [self.project_dir]:
            parent = os.path.dirname(d)
            name = os.path.basename(d)
            for i in range(len(name) - 2, max(3, len(name) - 6), -1):
                truncated = os.path.join(parent, name[:i])
                if not os.path.exists(truncated):
                    candidates = _fg.glob(truncated + '*')
                    if len(candidates) == 1:
                        try:
                            os.symlink(candidates[0], truncated)
                            logger.info("[OODA] symlink: %s -> %s", truncated, candidates[0])
                        except OSError:
                            pass

    def _ensure_dirs(self):
        for d in ["rounds", "hypotheses", "reports"]:
            os.makedirs(os.path.join(self.project_dir, d), exist_ok=True)

    def _get_round_dir(self, round_n: int) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(self.project_dir, "rounds",
                            f"round_{round_n:03d}_{timestamp}")

    def _ensure_round_dir(self, round_n: int) -> str:
        rounds_dir = os.path.join(self.project_dir, "rounds")
        os.makedirs(rounds_dir, exist_ok=True)
        pattern = f"round_{round_n:03d}_"
        existing = [d for d in os.listdir(rounds_dir)
                    if d.startswith(pattern) and os.path.isdir(os.path.join(rounds_dir, d))]
        if existing:
            return os.path.join(rounds_dir, existing[0])
        new_dir = self._get_round_dir(round_n)
        os.makedirs(new_dir, exist_ok=True)
        return new_dir

    def _init_rl_db(self):
        db_dir = os.path.join(self.workspace, "ooda_data")
        os.makedirs(db_dir, exist_ok=True)
        db_path = os.path.join(db_dir, "learning.db")
        self._rl_conn = sqlite3.connect(db_path)
        self._rl_conn.execute("""CREATE TABLE IF NOT EXISTS research_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, phase TEXT,
            action TEXT, outcome TEXT, reward REAL, artifacts TEXT,
            hypothesis TEXT, reference TEXT)""")
        self._rl_conn.commit()

    def record_outcome(self, phase: str, action: str, outcome: str,
                        reward: float, artifacts: list[str] | None = None,
                        hypothesis: str = "", reference: str = ""):
        db_dir = os.path.join(self.workspace, "ooda_data")
        db_path = os.path.join(db_dir, "learning.db")
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO research_log (timestamp,phase,action,outcome,reward,artifacts,hypothesis,reference) VALUES (?,?,?,?,?,?,?,?)",
                (datetime.now().isoformat(), phase, action, outcome, reward,
                 json.dumps(artifacts or []), hypothesis, reference))
            conn.commit()
        finally:
            conn.close()

    def get_completed_phases(self) -> set:
        rows = self._rl_conn.execute(
            "SELECT DISTINCT phase FROM research_log WHERE outcome='success'").fetchall()
        return {r[0] for r in rows}

    def get_recent_log(self, n: int = 10) -> list[dict]:
        try:
            rows = self._rl_conn.execute(
                "SELECT * FROM research_log ORDER BY id DESC LIMIT ?", (n,)).fetchall()
            return [{"ts": r[1], "phase": r[2], "action": r[3], "outcome": r[4],
                     "reward": r[5], "hypothesis": r[7],
                     "reference": r[8] if len(r) > 8 else ""} for r in rows]
        except Exception:
            return []

    def get_failure_log(self, n: int = 20) -> list[dict]:
        try:
            rows = self._rl_conn.execute(
                "SELECT * FROM research_log WHERE outcome != 'success' "
                "ORDER BY id DESC LIMIT ?", (n,)).fetchall()
            return [{"ts": r[1], "phase": r[2], "action": r[3], "outcome": r[4],
                     "reward": r[5], "hypothesis": r[7]} for r in rows]
        except Exception:
            return []

    def _read_round_results(self, round_num: int) -> dict:
        results = {"round": round_num, "files_found": [], "metrics": {}, "summary": ""}
        rounds_dir = os.path.join(self.project_dir, "rounds")
        for dname in os.listdir(rounds_dir) if os.path.isdir(rounds_dir) else []:
            if dname.startswith(f"round_{round_num:03d}"):
                round_dir = os.path.join(rounds_dir, dname)
                for root, dirs, files in os.walk(round_dir):
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        results["files_found"].append(fpath)
                        if fname.endswith(".smi"):
                            try:
                                with open(fpath) as f:
                                    lines = [l.strip() for l in f if l.strip()]
                                results["metrics"]["mol_count"] = len(lines)
                            except Exception:
                                pass
                        if fname.endswith(".md") or fname.endswith(".txt"):
                            try:
                                with open(fpath) as f:
                                    results["summary"] += f.read()[:500]
                            except Exception:
                                pass
                break
        return results

    def _get_all_round_results(self) -> dict:
        all_r = {}
        rounds_dir = os.path.join(self.project_dir, "rounds")
        if os.path.isdir(rounds_dir):
            for dname in os.listdir(rounds_dir):
                m = re.match(r'round_(\d{3})_', dname)
                if m:
                    rn = int(m.group(1))
                    all_r[rn] = self._read_round_results(rn)
        return all_r

    def _build_context(self) -> str:
        parts = []
        cfg = self._config
        parts.append(f"项目: {cfg.get('name','?')}")
        parts.append(f"目标: {cfg.get('goal','?')}")
        parts.append(f"描述: {cfg.get('description','?')}")
        parts.append(f"工具: {', '.join(cfg.get('tools',[]))}")
        
        # Previous round results with details
        all_r = self._get_all_round_results()
        recent_rounds = sorted(all_r.keys())[-3:]  # last 3 rounds
        for rn in recent_rounds:
            r = all_r[rn]
            m = r.get("metrics", {})
            s = r.get("summary", "")
            files = r.get("files_found", [])
            key_files = [os.path.basename(f) for f in files if f.endswith(('.md','.pdf','.png','.py'))][:5]
            
            parts.append(f"\n第{rn}轮成果:")
            if m:
                parts.append(f"  数据: " + ", ".join(f"{k}={v}" for k, v in sorted(m.items())))
            if key_files:
                parts.append(f"  产物: {', '.join(key_files)}")
            if s:
                parts.append(f"  摘要: {s[:300]}")
        
        # Recent research log (successes and failures)
        recent_log = self.get_recent_log(8)
        if recent_log:
            parts.append("\n最近执行记录:")
            for entry in recent_log[-5:]:
                phase = entry.get("phase", "?")
                outcome = entry.get("outcome", "?")
                hypothesis = entry.get("hypothesis", "")[:100]
                status = "OK" if outcome == "success" else "FAIL"
                parts.append(f"  [{status}] {phase}: {hypothesis}")
        
        completed = self.get_completed_phases()
        if completed:
            parts.append(f"\n已完成: {len(completed)} 轮")
        
        # Read evolution journal for self-awareness
        journal = self._read_evolution_journal()
        if journal:
            # Extract known issues for context
            known_issues = []
            in_issues = False
            for line in journal.split('\n'):
                if '已知待解决问题' in line:
                    in_issues = True
                    continue
                if in_issues and line.startswith('##'):
                    break
                if in_issues and line.strip().startswith(('1.', '2.', '3.', '4.', '5.', '6.')):
                    known_issues.append(line.strip())
            if known_issues:
                parts.append("\n当前已知待解决问题:")
                for issue in known_issues[:5]:
                    parts.append(f"  {issue}")
        
        return "\n".join(parts)

    def _search_knowledge(self, query: str = "") -> str:
        snippets = []
        ql = (query or "").lower()
        keywords = [w for w in ql.split() if len(w) > 2] if ql else []
        for doc_name, content in self._knowledge_docs.items():
            if not keywords:
                snippets.append(f"[{doc_name}] {content[:300]}")
            else:
                for kw in keywords:
                    idx = content.lower().find(kw)
                    if idx >= 0:
                        start = max(0, idx - 150)
                        end = min(len(content), idx + 150)
                        snippets.append(f"[{doc_name}] ...{content[start:end]}...")
                        break
        return "\n".join(snippets[:5]) if snippets else ""

    @staticmethod
    def _safe_truncate(text: str, max_chars: int = 2000) -> str:
        if len(text) <= max_chars:
            return text
        cut = text.rfind('\n', 0, max_chars)
        if cut > max_chars // 2:
            return text[:cut] + '\n...(truncated)'
        return text[:max_chars] + '...(truncated)'

    # ---- v4: LLM-driven failure analysis (Defect 1) ----

    def _format_failure_history(self) -> str:
        failures = self.get_failure_log(15)
        if not failures:
            return "(无失败记录)"
        lines = []
        for f in failures:
            ts = str(f.get("ts", ""))[:19]
            phase = f.get("phase", "?")
            action = f.get("action", "?")
            outcome = f.get("outcome", "?")
            lines.append(f"[{ts}] phase={phase} action={action} outcome={outcome}")
        return "\n".join(lines)

    def _analyze_failures_and_decide(self, task_key: str, phase: str) -> dict:
        """Call LLM to analyze failure patterns. Returns decision dict."""
        ctx = self.breaker.get_failure_context(task_key)
        history = ctx.get("history", [])
        recent_errors = []
        for h in history[-8:]:
            err = h.get("error", "")[:300]
            step = h.get("step", "")
            recent_errors.append(f"- [{step}] {err}")

        if not recent_errors:
            return {"should_pause": False, "suggested_action": "retry",
                    "analysis": "no recent failures", "message_to_user": ""}

        prompt = f"""你是一个系统诊断专家。以下是一个科研任务连续失败 {ctx['failure_count']} 次的记录。

【任务阶段】{phase}

【最近失败记录】
{chr(10).join(recent_errors)}

【请分析并决策】
1. 分析失败的根本原因是什么？
2. 这些失败是否属于同一类问题？
3. 继续重试是否有意义？还是需要：
   a) 暂停等待外部输入（如缺少数据文件）
   b) 换一种策略（如改用不同的工具或方法）
   c) 跳过此步骤继续（如果失败不阻塞后续）
   d) 直接重试（如果失败是暂时性的）

请以 JSON 格式输出决策（只输出 JSON，不要其他内容）：
{{"analysis": "根本原因分析(中文,1-2句话)", "should_pause": true/false, "pause_reason": "暂停原因", "suggested_action": "skip/change_strategy/retry/wait_for_input", "message_to_user": "通知用户的消息(中文)，不需要则为空字符串"}}"""

        if self.adapter:
            try:
                response = self.adapter.chat(prompt, purpose="ooda_analyze_failure")
                if response:
                    json_match = re.search(r'\{[^}]+\}', response, re.DOTALL)
                    if json_match:
                        return json.loads(json_match.group())
            except Exception as e:
                logger.warning("[OODA-v4] LLM failure analysis failed: %s", e)

        return {
            "analysis": f"连续 {ctx['failure_count']} 次失败，LLM 分析不可用，采用保守策略暂停",
            "should_pause": True,
            "pause_reason": "多次连续失败，建议人工检查",
            "suggested_action": "wait_for_input",
            "message_to_user": f"任务「{phase}」连续失败 {ctx['failure_count']} 次，已自动暂停。请检查环境和数据后回复「继续」。",
        }

    # ---- v4: LLM-generated plan (Defect 2) ----

    def _build_research_task(self, round_n: int, ctx: str, knowledge: str) -> dict:
        """Generate next research task using LLM - not a hardcoded template."""
        cfg = self._config
        tools = ', '.join(cfg.get('tools', []))
        kfiles = ', '.join(cfg.get('knowledge_files', []))
        failure_history = self._format_failure_history()

        plan_prompt = f"""你是一个自主科研 Agent。请基于以下信息规划下一轮研究工作。

【项目信息】
- 项目名称：{cfg.get('name', '未命名项目')}
- 项目描述：{cfg.get('description', '')}
- 研究目标：{cfg.get('goal', '探索性研究')}
- 可用工具：{tools}
- 知识库文件：{kfiles}
- 工作目录：{os.path.basename(self.project_dir)}（位于 shared_projects 下）

【当前状态】
{self._safe_truncate(ctx) if ctx else '暂无数据'}

【知识库摘要】
{self._safe_truncate(knowledge) if knowledge else '(暂无)'}

【历史失败记录】
{failure_history}

【你的任务】
基于以上所有信息，生成下一轮具体的研究计划。要求：
1. 如果历史有失败记录，本轮必须有明确的「与之前不同的策略」
2. 如果缺少关键资源（数据文件、API key等），明确指出需要的资源，而不是继续重试
3. 计划要具体：包含假设、实验步骤、参数、验证方法
4. 如果已有知识库包含足够信息，优先利用现有知识，减少不必要的搜索

请直接输出研究计划文本。第一行用【第{round_n}轮：具体标题】开头。

⚠️ 关键：本轮必须基于上一轮的成果继续推进，而不是从头开始。
⚠️ 禁止调用 PocketFlow 或 call_agent_skill。所有分子生成和性质计算必须用 execute_code 写 Python 脚本完成。
⚠️ 如果上一轮已经生成了分子、完成了搜索或写出了代码，本轮应该在此基础上深化，不要重复。"""

        plan_text = plan_prompt
        if self.adapter:
            try:
                response = self.adapter.chat(plan_prompt, purpose="ooda_plan")
                if response and len(response.strip()) > 100:
                    plan_text = response.strip()
                else:
                    logger.warning("[OODA-v4] LLM plan too short (%d chars), using template",
                                   len(response.strip()) if response else 0)
            except Exception as e:
                logger.warning("[OODA-v4] LLM plan generation failed: %s", e)

        title = f"自主研究 第{round_n}轮"
        first_line = plan_text.split('\n')[0].strip()
        if first_line.startswith('【') and '】' in first_line:
            title = first_line.strip('【】')

        return {
            "phase": f"research_round_{round_n}",
            "title": title,
            "strategy": "llm_driven",
            "round_num": round_n,
            "raw_task": plan_text,
        }

    def _build_tool_test_task(self, round_n: int, ctx: str) -> dict:
        tools = self._config.get('tools', [])
        completed = self.get_completed_phases()
        tested = {p.replace('tool_test_', '').split('_')[0] for p in completed}
        untested = [t for t in tools if t not in tested]
        next_tool = untested[0] if untested else "autonomous"

        prompt = f"""你是一个工具集成测试工程师。

【当前状态】
{ctx}

【可用工具】
{', '.join(tools)}

【已测试】
{', '.join(sorted(tested)) if tested else '无'}

【本次测试目标】
{next_tool}

【你的任务】
1. 调用 {next_tool} 的 wrapper/agent
2. 检查参数传递是否正确、输出文件是否完整
3. 记录实际执行时间、预估 ETA 准确度
4. 检查是否有 progress.jsonl、ETA 字段
5. 输出结构化测试报告（通过/失败、问题、ETA偏差分析）

直接执行测试，完成后输出报告。"""

        return {
            "phase": f"tool_test_{next_tool}_{round_n}",
            "title": f"工具测试: {next_tool}",
            "strategy": f"test_{next_tool}",
            "round_num": round_n,
            "raw_task": prompt,
        }

    # ---- OODA Cycle ----

    def observe(self) -> dict:
        return {
            "completed_phases": self.get_completed_phases(),
            "recent_actions": self.get_recent_log(5),
        }

    def orient(self, observation: dict) -> dict:
        mode = "tool_test" if self.instance_id == "05" else "research"
        return {"mode": mode, "completed": observation.get("completed_phases", set())}

    def decide(self, orientation: dict) -> dict | None:
        mode = orientation["mode"]
        completed = orientation["completed"]
        round_n = len(completed) + 1
        ctx = self._build_context()
        knowledge = self._search_knowledge()

        current_phase = f"research_round_{round_n}"

        # v4: Check circuit breaker before generating new plan
        if self.breaker.is_open(current_phase):
            logger.warning("[OODA-v4] Breaker OPEN for %s - calling LLM analysis", current_phase)
            decision = self._analyze_failures_and_decide(current_phase, current_phase)

            if decision.get("should_pause"):
                msg = (
                    f"任务暂停\n\n"
                    f"{decision.get('message_to_user', '需要人工干预')}\n\n"
                    f"分析: {decision.get('analysis', '')}\n"
                    f"失败次数: {self.breaker.failure_count.get(current_phase, 0)}"
                )
                self._send_qq_notification(msg)
                self._save_paused_state(current_phase, decision)
                return None

            action = decision.get("suggested_action", "retry")
            if action == "skip":
                logger.info("[OODA-v4] LLM decided SKIP %s", current_phase)
                self.record_outcome(phase=current_phase, action="llm_skip",
                                    outcome="success", reward=-0.1,
                                    reference=decision.get("analysis", ""))
                self.breaker.reset(current_phase)
                round_n = len(self.get_completed_phases()) + 1
                ctx = self._build_context()

            elif action == "change_strategy":
                logger.info("[OODA-v4] LLM decided CHANGE_STRATEGY for %s", current_phase)
                self.breaker.reset(current_phase)

        if mode == "tool_test":
            return self._build_tool_test_task(round_n, ctx)
        else:
            return self._build_research_task(round_n, ctx, knowledge)

    def _send_qq_notification(self, message: str) -> None:
        """Write a notification to QQ chat history."""
        try:
            qq_path = os.path.join(self.workspace, "state", "qq_chat_history.jsonl")
            entry = {
                "role": "assistant",
                "content": message,
                "ts": datetime.now().isoformat(),
                "source": "ooda_circuit_breaker",
            }
            with open(qq_path, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            logger.info("[OODA-v4] QQ notification sent")
        except Exception as e:
            logger.warning("[OODA-v4] QQ notification failed: %s", e)

    def _save_paused_state(self, phase: str, decision: dict) -> None:
        """Save paused state for recovery."""
        try:
            state_path = os.path.join(self.project_dir, "paused_state.json")
            state = {
                "phase": phase,
                "paused_at": datetime.now().isoformat(),
                "analysis": decision.get("analysis", ""),
                "message": decision.get("message_to_user", ""),
                "failure_count": self.breaker.failure_count.get(phase, 0),
            }
            with open(state_path, "w") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            logger.info("[OODA-v4] Paused state saved to %s", state_path)
        except Exception as e:
            logger.warning("[OODA-v4] Failed to save paused state: %s", e)

    def record_failure(self, phase: str, error_message: str, step: str = "",
                       artifacts: list[str] | None = None) -> int:
        """Record a task failure through the circuit breaker. Returns failure count."""
        task_key = phase
        count = self.breaker.record_failure(task_key, {
            "error_message": error_message,
            "step": step,
            "artifacts": artifacts or [],
        })
        self.record_outcome(phase=phase, action=step or "execution",
                            outcome="failed", reward=-0.5,
                            hypothesis=error_message[:200])
        return count

    def inject_task(self, decision: dict) -> bool:
        text = decision.get("raw_task", "")
        phase = decision.get("phase", "unknown")
        round_n = decision.get("round_num", 0)
        
        # Try direct enqueue first (bypasses inbox for immediate processing)
        try:
            from partner.mind.executor import enqueue_user_message
            enqueue_user_message(
                text=text,
                sender_id=f"ooda_{self.instance_id}",
                sender_name=f"OODA-{self.instance_id}",
                source="ooda_engine",
                message_id=f"ooda_{self.instance_id}_round_{round_n}_{int(time.time())}",
            )
            logger.info("[OODA-v4] direct-enqueued phase=%s round=%d", phase, round_n)
            return True
        except ImportError:
            pass  # fall through to inbox method
        phase = decision.get("phase", "unknown")
        round_n = decision.get("round_num", 0)

        if round_n > 0:
            round_dir = self._ensure_round_dir(round_n)
            try:
                plan_path = os.path.join(round_dir, "plan.md")
                with open(plan_path, "w") as f:
                    f.write(f"# {decision.get('title', 'Plan')}\n\n")
                    f.write(f"Phase: {phase}\n")
                    f.write(f"Strategy: {decision.get('strategy', '?')}\n")
                    f.write(f"Generated: {datetime.now().isoformat()}\n\n")
                    f.write(text)
            except Exception as e:
                logger.warning("[OODA-v4] Failed to save plan: %s", e)

        # v4: write to ROOT workspace inbox (poller reads from root, not instance)
        inbox = os.path.join(self.workspace, "state", "desktop_inbox.jsonl")
        try:
            import uuid
            # Wrap as task instruction so routing classifier sends it to batch_plan
            task_title = decision.get("title", "研究任务")
            wrapped_text = f"【任务指令 — 第{round_n}轮研究】\n\n{task_title}\n\n{text}"
            msg = {
                "id": str(uuid.uuid4()),
                "message_id": f"ooda_{phase}_{int(time.time())}",
                "source": "ooda_engine",
                "text": wrapped_text,
                "ts": time.time(),
                "sender_name": "OODA Research Engine",
            }
            with open(inbox, 'a') as f:
                f.write(json.dumps(msg, ensure_ascii=False) + '\n')
            logger.info("[OODA-v4] injected phase=%s round=%s strategy=%s",
                        phase, round_n, decision.get("strategy", "?"))
            return True
        except Exception as e:
            logger.error("[OODA-v4] inject failed: %s", e)
            return False

    def cycle(self) -> dict | None:
        obs = self.observe()
        ort = self.orient(obs)
        dec = self.decide(ort)

        if dec is None:
            return None

        if dec:
            phase = dec.get("phase", "unknown")
            if not hasattr(self, '_phase_inject_count'):
                self._phase_inject_count = {}
            self._phase_inject_count[phase] = self._phase_inject_count.get(phase, 0) + 1
            count = self._phase_inject_count[phase]

            if count >= 4:
                logger.warning("[OODA-v4] phase %s stuck after %d injections - force-advancing",
                               phase, count)
                self.breaker.record_failure(phase, {
                    "error_message": f"Phase stuck after {count} injections, force-advanced",
                    "step": "force_advance",
                })
                self.record_outcome(phase=phase, action=f"force_advance:{phase}",
                                    outcome="success", reward=-0.3)
                self._phase_inject_count[phase] = 0

                if self.breaker.is_open(phase):
                    logger.warning("[OODA-v4] Breaker OPEN even after force-advance - pausing")
                    decision = self._analyze_failures_and_decide(phase, phase)
                    if decision.get("should_pause"):
                        self._send_qq_notification(
                            f"任务暂停\n\n{decision.get('message_to_user', '')}"
                        )
                        self._save_paused_state(phase, decision)
                        return None

                obs2 = self.observe()
                ort2 = self.orient(obs2)
                dec = self.decide(ort2)
                if dec:
                    logger.info("[OODA-v4] force-advanced %s -> %s",
                                phase, dec.get("phase", "?"))
                    self.inject_task(dec)
                return dec

            self.inject_task(dec)
        return dec


# ---- Per-instance engines ----

_ooda_engines: dict = {}

def get_ooda(workspace: str, instance_id: str = "", adapter=None) -> OODAEngine:
    global _ooda_engines
    key = workspace
    if key not in _ooda_engines:
        _ooda_engines[key] = OODAEngine(workspace, instance_id=instance_id, adapter=adapter)
    elif adapter is not None:
        _ooda_engines[key].adapter = adapter
    return _ooda_engines[key]


def ooda_continue(workspace: str, instance_id: str = "") -> bool:
    if instance_id == "05":
        return False
    engine = get_ooda(workspace, instance_id=instance_id)
    import logging as _logging
    _logging.getLogger("partner.ooda").info("[OODA-CONTINUE] called for instance %s", instance_id)
    ip = os.path.join(workspace, "state", "desktop_inbox.jsonl")
    try:
        if os.path.exists(ip):
            with open(ip) as f:
                pending = [l for l in f if l.strip()]
            user_msgs = [l for l in pending if '"source": "ooda_engine"' not in l
                         and '"sender_name": "OODA' not in l]
            if user_msgs:
                return False
    except:
        pass
    dec = engine.cycle()
    _logging.getLogger("partner.ooda").info("[OODA-CONTINUE] cycle result: %s", "task" if dec else "None")
    return dec is not None

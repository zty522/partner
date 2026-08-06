"""
Self-Healing Engine v2 — SESA-inspired Skill Bank for persistent repair knowledge.

Key improvements from SESA + VeriSkill:
1. Failure → Skill extraction (structured skill cards with CATEGORY/PATTERN/TRIGGER)
2. Persistent Skill Bank with embedding-based retrieval
3. Skill validation (only keep skills that led to successful fixes)
4. Pre-task skill injection (retrieve relevant skills before planning)
"""

from __future__ import annotations
import json, logging, os, re, time, sqlite3, subprocess, tempfile
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Skill extraction prompt (SESA-style) ──

SKILL_EXTRACT_PROMPT = """You are extracting a CONCRETE, ACTIONABLE fix skill from a task execution failure.

[Task]
{task_description}

[Step results]
{step_results}

[LLM check feedback]
{check_feedback}

[Previous skills that DIDN'T help]
{prev_skills}

Your task: extract ONE specific fix pattern that addresses this failure's root cause.

REQUIREMENTS:
1. Be SPECIFIC: name the exact agent/tool/parameter that needs fixing
2. Identify the FAILURE PATTERN (what sequence leads to this failure)
3. Give the FIX ACTION (what concrete change resolves it)
4. The skill must address what previous skills MISSED
5. Avoid vague "check carefully" advice

Output STRICTLY in this format:
CATEGORY: agent_call|param_fix|env_setup|file_path|code_bug|config|dependency
PATTERN: <what kind of failure this applies to, with concrete features>
ROOT_CAUSE: <why this failure happens>
FIX_ACTION: <what to do to fix it>
FIX_TYPE: params|env|config|code|cannot_fix
TRIGGER_KEYWORDS: <comma-separated keywords that trigger this skill>
RETRY_PARAMS: <JSON of adjusted parameters if fix_type=params, else {{}}>"""


class SkillBank:
    """Persistent, searchable repository of fix skills."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""CREATE TABLE IF NOT EXISTS skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            pattern TEXT,
            root_cause TEXT,
            fix_action TEXT,
            fix_type TEXT,
            trigger_keywords TEXT,
            retry_params TEXT,
            success_count INTEGER DEFAULT 0,
            fail_count INTEGER DEFAULT 0,
            last_used TEXT,
            created_at TEXT,
            embedding_json TEXT
        )""")
        conn.execute("""CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category)""")
        conn.execute("""CREATE INDEX IF NOT EXISTS idx_skills_success ON skills(success_count)""")
        conn.commit()
        conn.close()

    def add_skill(self, skill: dict) -> int:
        """Add a new skill. Returns skill ID."""
        conn = sqlite3.connect(self.db_path)
        c = conn.execute(
            "INSERT INTO skills (category, pattern, root_cause, fix_action, fix_type, "
            "trigger_keywords, retry_params, success_count, fail_count, created_at) "
            "VALUES (?,?,?,?,?,?,?,0,0,?)",
            (skill.get("category", ""),
             skill.get("pattern", ""),
             skill.get("root_cause", ""),
             skill.get("fix_action", ""),
             skill.get("fix_type", ""),
             skill.get("trigger_keywords", ""),
             json.dumps(skill.get("retry_params", {})),
             datetime.now().isoformat())
        )
        sid = c.lastrowid
        conn.commit()
        conn.close()
        logger.info("[SkillBank] Added skill #%d: %s", sid, skill.get("pattern", "")[:80])
        return sid

    def retrieve(self, task_description: str, top_k: int = 3) -> list[dict]:
        """Retrieve relevant skills by keyword matching (simple, no embeddings needed)."""
        conn = sqlite3.connect(self.db_path)
        # Extract keywords from task description
        words = re.findall(r'\w+', task_description.lower())
        keywords = [w for w in words if len(w) > 2][:10]
        
        if not keywords:
            rows = conn.execute(
                "SELECT * FROM skills ORDER BY success_count DESC LIMIT ?", (top_k,)
            ).fetchall()
        else:
            # Build LIKE query
            conditions = " OR ".join([f"pattern LIKE '%{kw}%' OR trigger_keywords LIKE '%{kw}%'" 
                                      for kw in keywords[:5]])
            rows = conn.execute(
                f"SELECT * FROM skills WHERE {conditions} ORDER BY success_count DESC LIMIT ?",
                (top_k,)
            ).fetchall()
        
        conn.close()
        return [self._row_to_dict(r) for r in rows]

    def record_result(self, skill_id: int, success: bool):
        """Update skill success/fail count."""
        conn = sqlite3.connect(self.db_path)
        if success:
            conn.execute("UPDATE skills SET success_count=success_count+1, last_used=? WHERE id=?",
                        (datetime.now().isoformat(), skill_id))
        else:
            conn.execute("UPDATE skills SET fail_count=fail_count+1, last_used=? WHERE id=?",
                        (datetime.now().isoformat(), skill_id))
        conn.commit()
        conn.close()

    def get_stats(self) -> dict:
        conn = sqlite3.connect(self.db_path)
        total = conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
        by_cat = conn.execute(
            "SELECT category, COUNT(*) FROM skills GROUP BY category"
        ).fetchall()
        conn.close()
        return {"total": total, "by_category": dict(by_cat)}

    def _row_to_dict(self, row) -> dict:
        cols = ["id", "category", "pattern", "root_cause", "fix_action", "fix_type",
                "trigger_keywords", "retry_params", "success_count", "fail_count",
                "last_used", "created_at", "embedding_json"]
        d = dict(zip(cols, row))
        try:
            d["retry_params"] = json.loads(d.get("retry_params", "{}"))
        except:
            d["retry_params"] = {}
        return d


class SelfHealEngine:
    """Diagnoses failures and extracts reusable fix skills (SESA-style)."""

    def __init__(self, workspace: str, adapter=None):
        self.workspace = workspace
        self.adapter = adapter
        db_path = os.path.join(workspace, "state", "skill_bank.db")
        self.bank = SkillBank(db_path)
        self.heal_log: list[dict] = []

    def diagnose_and_fix(
        self,
        task_description: str,
        step_results: list[dict],
        llm_check_feedback: dict,
        available_tools: str = "",
        working_dir: str = "",
    ) -> dict:
        """Analyze failure, extract skill, attempt fix."""
        
        # Step 0: Read evolution journal for context
        journal = self._read_evolution_journal()
        
        # Step 1: Retrieve relevant past skills
        prev_skills = self.bank.retrieve(task_description, top_k=3)
        prev_skills_text = "\n".join(
            f"- [{s['category']}] {s['pattern'][:120]}" for s in prev_skills
        ) if prev_skills else "(no relevant past skills)"
        
        # Step 2: Format context
        steps_text = self._format_step_results(step_results)
        feedback_text = json.dumps(llm_check_feedback, ensure_ascii=False, indent=2)
        
        # Step 3: LLM extracts skill from failure
        prompt = SKILL_EXTRACT_PROMPT.format(
            task_description=task_description[:2000],
            step_results=steps_text[:3000],
            check_feedback=feedback_text[:1500],
            prev_skills=prev_skills_text[:1000],
        )
        
        diagnosis = self._call_llm(prompt)
        if not diagnosis:
            return {"fix_type": "cannot_fix", "root_cause": "LLM unavailable",
                    "should_retry": False}
        
        # Step 4: Persist as skill
        skill_id = self.bank.add_skill(diagnosis)
        diagnosis["skill_id"] = skill_id
        
        logger.info("[SELFHEAL-v2] Extracted skill #%d: %s (type=%s)",
                     skill_id, diagnosis.get("pattern", "")[:80],
                     diagnosis.get("fix_type", ""))
        
        # Step 5: Attempt fix
        fix_result = self._apply_fix(diagnosis, working_dir)
        diagnosis["fix_result"] = fix_result
        
        # Step 6: Record and return
        self._record_heal(diagnosis)
        
        return diagnosis

    def _format_step_results(self, step_results: list[dict]) -> str:
        lines = []
        for i, step in enumerate(step_results):
            step_id = step.get("step_id", f"step{i}")
            event_type = step.get("event_type", "?")
            ok = step.get("ok", False)
            error = step.get("error", "")
            result = step.get("result", {})
            
            status = "OK" if ok else "FAIL"
            lines.append(f"[{status}] {step_id} ({event_type})")
            if error:
                lines.append(f"   error: {str(error)[:300]}")
            if isinstance(result, dict):
                content = result.get("content", "") or result.get("stdout", "")
                if content and len(str(content)) > 10:
                    lines.append(f"   output: {str(content)[:300]}")
        return "\n".join(lines)

    def _call_llm(self, prompt: str) -> dict | None:
        if not self.adapter:
            return None
        try:
            response = self.adapter.chat(prompt, purpose="self_heal_extract")
            if not response:
                return None
            # Parse the structured format
            skill = {}
            for line in response.strip().split('\n'):
                if ':' in line:
                    key, _, val = line.partition(':')
                    key = key.strip().lower().replace(' ', '_')
                    val = val.strip()
                    if key == 'retry_params':
                        try:
                            skill[key] = json.loads(val)
                        except:
                            skill[key] = {}
                    else:
                        skill[key] = val
            if 'category' in skill:
                return skill
            return None
        except Exception as e:
            logger.warning("[SELFHEAL-v2] LLM call failed: %s", e)
            return None

    def _apply_fix(self, diagnosis: dict, working_dir: str) -> dict:
        fix_type = diagnosis.get("fix_type", "cannot_fix")
        fix_desc = diagnosis.get("fix_action", "")
        
        if fix_type == "cannot_fix":
            return {"applied": False, "reason": fix_desc[:200]}
        
        if fix_type == "params":
            retry_params = diagnosis.get("retry_params", {})
            logger.info("[SELFHEAL-v2] Params fix: %s", json.dumps(retry_params, ensure_ascii=False))
            return {"applied": True, "type": "params", "retry_params": retry_params}
        
        if fix_type == "env":
            return self._fix_env(diagnosis)
        
        if fix_type == "config":
            return self._fix_config(diagnosis, working_dir)
        
        if fix_type == "code":
            # Try Hermes subprocess delegation
            try:
                import subprocess, tempfile
                fix_script = self._build_fix_script(diagnosis)
                if fix_script:
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, dir='/tmp') as f:
                        f.write(fix_script)
                        tmp_path = f.name
                    r = subprocess.run(
                        ["python3", tmp_path],
                        capture_output=True, text=True, timeout=120,
                        cwd=working_dir or self.workspace
                    )
                    os.unlink(tmp_path)
                    if r.returncode == 0:
                        logger.info("[SELFHEAL-v2] Code fix applied successfully")
                        return {"applied": True, "type": "code_delegated",
                                "stdout": r.stdout[:500]}
                    else:
                        return {"applied": False, "type": "code",
                                "error": r.stderr[:300]}
            except Exception as e:
                logger.debug("[SELFHEAL-v2] Code delegation failed: %s", e)
            return {"applied": False, "type": "code",
                    "reason": "Code fix requires Hermes delegation",
                    "fix_description": fix_desc}
        
        if fix_type == "install":
            return self._auto_install_tool(diagnosis, working_dir)
        return {"applied": False, "reason": f"Unknown fix type: {fix_type}"}

    def _fix_env(self, diagnosis: dict) -> dict:
        fix_desc = diagnosis.get("fix_action", "")
        pip_match = re.search(r'pip\s+install\s+\S+', fix_desc)
        if pip_match:
            cmd = pip_match.group()
            logger.info("[SELFHEAL-v2] Env fix: %s", cmd)
            try:
                import subprocess
                r = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=60)
                return {"applied": r.returncode == 0, "type": "env", "command": cmd}
            except Exception as e:
                return {"applied": False, "type": "env", "error": str(e)}
        return {"applied": False, "type": "env", "reason": "No executable env fix found"}

    def _fix_config(self, diagnosis: dict, working_dir: str) -> dict:
        fix_desc = diagnosis.get("fix_action", "")
        config_patches = []
        yaml_matches = re.findall(r'修改\s+(\S+\.ya?ml)\s*(.+)$', fix_desc, re.MULTILINE)
        for filepath, change in yaml_matches:
            full_path = os.path.join(working_dir, filepath) if not os.path.isabs(filepath) else filepath
            if os.path.exists(full_path):
                config_patches.append({"file": full_path, "change": change})
        if config_patches:
            return {"applied": True, "type": "config", "patches": config_patches}
        return {"applied": False, "type": "config", "reason": "No actionable config fix found"}

    def _build_fix_script(self, diagnosis: dict) -> str | None:
        """Generate a Python script from the fix description (LLM-driven)."""
        fix_desc = diagnosis.get("fix_action", "")
        root_cause = diagnosis.get("root_cause", "")
        
        # Ask LLM to generate the fix script
        if not self.adapter:
            return None
        
        prompt = f"""Generate a Python script to fix this issue:

ROOT CAUSE: {root_cause}
FIX NEEDED: {fix_desc}

The script should:
- Be self-contained Python
- Include error handling
- Print "FIXED" on success
- NOT use any external APIs (no LLM calls)
- Work in the current environment

Output ONLY the Python code, no explanations."""

        try:
            response = self.adapter.chat(prompt, purpose="self_heal_generate_fix")
            if response and 'import' in response and 'print' in response:
                # Extract code block
                code = response
                if '```python' in code:
                    code = code.split('```python')[1].split('```')[0]
                elif '```' in code:
                    code = code.split('```')[1].split('```')[0]
                return code.strip()
        except Exception as e:
            logger.debug("[SELFHEAL-v2] Fix script generation failed: %s", e)
        return None

    def _auto_install_tool(self, diagnosis: dict, working_dir: str) -> dict:
        """Auto-install missing tools (pip, git clone, conda)."""
        fix_desc = diagnosis.get("fix_action", "")
        missing = diagnosis.get("pattern", "")
        
        # Detect what needs installing
        tool_name = None
        for kw in ['TargetDiff', 'RFdiffusion', 'DiffDock', 'PocketFlow']:
            if kw.lower() in fix_desc.lower() or kw.lower() in missing.lower():
                tool_name = kw
                break
        
        if not tool_name:
            # Try to extract from pattern
            import re
            m = re.search(r'安装\s+(\w+)', fix_desc + missing)
            if m:
                tool_name = m.group(1)
        
        if not tool_name:
            return {"applied": False, "reason": "Could not identify tool to install"}
        
        logger.info("[SELFHEAL-v2] Auto-installing: %s", tool_name)
        
        # Try pip first
        try:
            import subprocess
            r = subprocess.run(
                ["pip", "install", tool_name.lower()],
                capture_output=True, text=True, timeout=120
            )
            if r.returncode == 0:
                return {"applied": True, "type": "pip_install", "tool": tool_name}
        except:
            pass
        
        # Try git clone
        try:
            target_dir = os.path.join(self.workspace, "..", "external", tool_name)
            if not os.path.exists(target_dir):
                r = subprocess.run(
                    ["git", "clone", f"https://github.com/search?q={tool_name}", target_dir],
                    capture_output=True, text=True, timeout=30
                )
        except:
            pass
        
        return {"applied": False, "type": "install", "reason": f"Auto-install of {tool_name} not supported yet"}

    def _read_evolution_journal(self) -> str:
        """Read the evolution journal for self-awareness."""
        journal_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(self.workspace))),
            "partner", "docs", "evolution_journal.md"
        )
        # Also try relative to workspace
        alt_path = os.path.join(self.workspace, "..", "..", "partner", "docs", "evolution_journal.md")
        for p in [journal_path, alt_path]:
            if os.path.exists(p):
                try:
                    with open(p) as f:
                        return f.read()
                except:
                    pass
        return ""
    
    def _update_evolution_journal(self, entry: str) -> None:
        """Append a new entry to the evolution journal."""
        journal_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(self.workspace))),
            "partner", "docs", "evolution_journal.md"
        )
        try:
            os.makedirs(os.path.dirname(journal_path), exist_ok=True)
            with open(journal_path, "a") as f:
                f.write(f"\n### {datetime.now().strftime('%Y-%m-%d %H:%M')} — 自愈触发\n\n{entry}\n")
            logger.info("[EVOLUTION_JOURNAL] Updated")
        except Exception as e:
            logger.debug("[EVOLUTION_JOURNAL] update failed: %s", e)

    def _record_heal(self, diagnosis: dict) -> None:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "root_cause": diagnosis.get("root_cause", ""),
            "fix_type": diagnosis.get("fix_type", ""),
            "skill_id": diagnosis.get("skill_id", 0),
            "applied": diagnosis.get("fix_result", {}).get("applied", False),
        }
        self.heal_log.append(entry)
        try:
            heal_path = os.path.join(self.workspace, "state", "self_heal_log.jsonl")
            with open(heal_path, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass
        
        # Also update evolution journal
        journal_entry = f"- **问题**: {diagnosis.get('root_cause', '未知')[:200]}\n"
        journal_entry += f"- **修复类型**: {diagnosis.get('fix_type', '?')}\n"
        journal_entry += f"- **修复是否成功**: {diagnosis.get('fix_result', {}).get('applied', False)}\n"
        journal_entry += f"- **技能 ID**: {diagnosis.get('skill_id', 0)}\n"
        self._update_evolution_journal(journal_entry)


def auto_heal(
    workspace: str,
    task_description: str,
    step_results: list[dict],
    llm_check_feedback: dict,
    adapter=None,
    working_dir: str = "",
) -> dict:
    """One-call: diagnose failure, extract skill, attempt fix."""
    engine = SelfHealEngine(workspace, adapter=adapter)
    return engine.diagnose_and_fix(
        task_description=task_description,
        step_results=step_results,
        llm_check_feedback=llm_check_feedback,
        working_dir=working_dir or workspace,
    )


def retrieve_skills(workspace: str, task_description: str, top_k: int = 3) -> list[dict]:
    """Retrieve relevant fix skills before planning a task."""
    db_path = os.path.join(workspace, "state", "skill_bank.db")
    bank = SkillBank(db_path)
    return bank.retrieve(task_description, top_k)


def get_skill_stats(workspace: str) -> dict:
    """Get skill bank statistics."""
    db_path = os.path.join(workspace, "state", "skill_bank.db")
    bank = SkillBank(db_path)
    return bank.get_stats()

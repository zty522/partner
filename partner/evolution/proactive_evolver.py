"""
Proactive Self-Evolution Engine (Sprint 7).

Unlike self_heal (reactive: wait for failure → fix),
this module PROACTIVELY:
1. Scans codebase for improvement opportunities
2. Reads evolution journal to identify known gaps
3. Generates and tests code patches
4. Can modify Partner's OWN source code (self_heal, executor, etc.)
5. Records all changes for audit

Meta-evolution: this module can improve itself.
"""

from __future__ import annotations
import json, logging, os, re, subprocess, time, py_compile
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Proactive scan prompts ──

SCAN_PROMPT = """You are an AI that improves its own codebase. Below is your SELF-AWARENESS document listing known problems. Your job is to convert these into actionable fixes.

[SELF-AWARENESS — KNOWN ISSUES TO FIX]
{known_issues}

[Current capabilities]
{capabilities}

[Recent execution patterns]
{execution_patterns}

TASK: Pick the 3 highest-priority actionable issues from the self-awareness list above. For each, generate a concrete fix. Focus on issues marked "pending" or "fix_applied". Issues marked "fixed" should be skipped.

For each issue:
TYPE: handler|config|prompt|wrapper|evolution  (what kind of code change)
FILE: exact path relative to partner/ directory
ISSUE: the problem (from self-awareness)
FIX: exact code change description
PRIORITY: high|medium|low

IMPORTANT: 
- If the issue says "修改文件: executor.py", set FILE=executor.py and TYPE=handler
- If the issue says "修改文件: prompt_builder.py", set FILE=prompt_builder.py and TYPE=prompt
- If the issue says "修改文件: ooda_engine.py", set FILE=ooda_engine.py and TYPE=handler
- Be specific about what code to change"""

CODE_FIX_PROMPT = """Generate the exact code change needed. Output ONLY the replacement.

FILE: {file_path}
ISSUE: {issue}
FIX_DESC: {fix_description}

Output format:
```diff
--- old
+++ new
```"""


class ProactiveEvolver:
    """Scans codebase, finds weaknesses, generates and applies patches."""

    SCAN_INTERVAL = 600  # 10 minutes

    def __init__(self, workspace: str, adapter=None, instance_id: str = ""):
        self.workspace = workspace
        self.adapter = adapter
        self.instance_id = instance_id
        self.patches: list[dict] = []
        self._load_patches()

    def _load_patches(self):
        path = os.path.join(self.workspace, "state", "proactive_patches.jsonl")
        if os.path.exists(path):
            try:
                with open(path) as f:
                    for line in f:
                        if line.strip():
                            self.patches.append(json.loads(line))
            except:
                pass

    def _save_patch(self, patch: dict):
        path = os.path.join(self.workspace, "state", "proactive_patches.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(patch, ensure_ascii=False) + "\n")
        self.patches.append(patch)

    def scan(self) -> list[dict]:
        """Proactively scan for improvements. Returns list of findings."""
        findings = []

        # 0. Read self-awareness doc — THE source of truth
        self_awareness = self._read_self_awareness()
        if self_awareness:
            logger.info("[PROACTIVE] Loaded self_awareness.md (%d chars)", len(self_awareness))
        
        # 1. Read evolution journal for known issues
        journal = self._read_journal()

        # 2. Check execution patterns
        patterns = self._analyze_patterns()

        # 3. Check codebase health
        code_issues = self._check_codebase()

        # 4. Ask LLM for improvement suggestions
        if self.adapter:
            llm_findings = self._llm_scan(journal, patterns)
            findings.extend(llm_findings)

        findings.extend(code_issues)

        logger.info("[PROACTIVE] Scan found %d improvement opportunities", len(findings))
        return findings

    def apply_if_safe(self, finding: dict) -> bool:
        """Apply a fix with priority-based strategy."""
        ftype = finding.get("TYPE", "")
        priority = finding.get("PRIORITY", "low")
        
        # Safe types: always auto-apply
        if ftype in ("config", "prompt"):
            return self._apply_finding(finding)
        
        # Code type: auto-apply if high priority, log for review if low
        if ftype in ("handler", "code", "wrapper", "evolution", "codebase"):
            if priority == "high":
                return self._apply_finding(finding)
            elif priority == "medium":
                # Try, but log detailed record regardless of outcome
                result = self._apply_finding(finding)
                if not result:
                    self._save_patch({
                        "timestamp": datetime.now().isoformat(),
                        "type": ftype, "file": finding.get("FILE", ""),
                        "issue": finding.get("ISSUE", ""), "fix": finding.get("FIX", ""),
                        "applied": False, "reason": "medium priority — auto-fix failed, needs manual",
                        "method": "auto_fixer",
                    })
                return result
            else:
                return False
        
        return False

    def _llm_scan(self, journal: str, patterns: str) -> list[dict]:
        """Use LLM to identify improvements."""
        if not self.adapter:
            return []

        # Read current capabilities from skill.md
        capabilities = self._read_capabilities()

                # Merge self-awareness with journal for richer LLM input
        full_context = (self._read_self_awareness() or "") + "\n\n" + journal
        prompt = SCAN_PROMPT.format(
            capabilities=capabilities[:2000],
            known_issues=full_context[:3000],
            execution_patterns=patterns[:1000],
        )

        try:
            response = self.adapter.chat(prompt, purpose="proactive_scan")
            findings = []
            current = {}
            for line in response.split('\n'):
                line = line.strip()
                if line.startswith('TYPE:'):
                    if current:
                        findings.append(current)
                    current = {'TYPE': line.split(':',1)[1].strip()}
                elif line.startswith('FILE:'):
                    current['FILE'] = line.split(':',1)[1].strip()
                elif line.startswith('ISSUE:'):
                    current['ISSUE'] = line.split(':',1)[1].strip()
                elif line.startswith('FIX:'):
                    current['FIX'] = line.split(':',1)[1].strip()
                elif line.startswith('PRIORITY:'):
                    current['PRIORITY'] = line.split(':',1)[1].strip()
            if current:
                # Normalize TYPE to recognized values
                ftype = current.get("TYPE", "").strip()
                recognized = {"config", "prompt", "handler", "wrapper", "evolution", "model", "codebase"}
                if ftype not in recognized:
                    # Map unknown types to closest recognized
                    if "config" in ftype: current["TYPE"] = "config"
                    elif "prompt" in ftype: current["TYPE"] = "prompt"
                    elif any(k in ftype for k in ["handler", "event", "executor", "harness"]): current["TYPE"] = "handler"
                    elif any(k in ftype for k in ["wrapper", "agent"]): current["TYPE"] = "wrapper"
                    elif any(k in ftype for k in ["code", "fix", "file"]): current["TYPE"] = "codebase"
                    else: current["TYPE"] = "codebase"
                findings.append(current)
            return findings
        except Exception as e:
            logger.debug("[PROACTIVE] LLM scan failed: %s", e)
            return []

    def _analyze_patterns(self) -> str:
        """Analyze recent execution patterns for weaknesses."""
        patterns = []
        
        # Check heal log
        heal_log = os.path.join(self.workspace, "state", "self_heal_log.jsonl")
        if os.path.exists(heal_log):
            try:
                with open(heal_log) as f:
                    entries = [json.loads(l) for l in f if l.strip()]
                recent = entries[-20:]
                successes = sum(1 for e in recent if e.get("applied"))
                patterns.append(f"自愈成功率: {successes}/{len(recent)}")
                
                # Common root causes
                causes = {}
                for e in recent:
                    rc = e.get("root_cause", "")[:50]
                    if rc:
                        causes[rc] = causes.get(rc, 0) + 1
                for rc, count in sorted(causes.items(), key=lambda x: -x[1])[:3]:
                    patterns.append(f"常见问题({count}x): {rc}")
            except:
                pass

        # Check health snapshots
        health = os.path.join(self.workspace, "state", "health_snapshots.jsonl")
        if os.path.exists(health):
            try:
                with open(health) as f:
                    snaps = [json.loads(l) for l in f if l.strip()]
                if snaps:
                    patterns.append(f"健康快照: {len(snaps)}次检查")
            except:
                pass

        return "\n".join(patterns)

    def _check_codebase(self) -> list[dict]:
        """Check Partner codebase — only flag real issues, not expected states."""
        findings = []
        partner_root = os.path.join(self.workspace, "..", "..", "partner", "partner")
        if not os.path.isdir(partner_root):
            partner_root = os.path.join(os.path.dirname(self.workspace), "..", "partner", "partner")
        
        # Sprint 7: Only check files that SHOULD exist and are critical
        checks = {
            "evolution/proactive_evolver.py": "主动进化（自身）",
        }
        
        for rel, name in checks.items():
            fp = os.path.join(partner_root, rel)
            if not os.path.exists(fp):
                findings.append({
                    "TYPE": "codebase", "FILE": rel,
                    "ISSUE": f"{name} 缺失",
                    "FIX": "Create the file if it's needed for the evolution loop",
                    "PRIORITY": "medium",
                })
        
        return findings

    def _resolve_path(self, rel_path: str) -> str | None:
        """Resolve a relative path to the actual Partner source file."""
        candidates = [
            os.path.join(self.workspace, "..", "..", "partner", rel_path),
            os.path.join(self.workspace, "..", "..", "partner", "partner", rel_path),
            os.path.join(os.path.dirname(self.workspace), "..", "partner", rel_path),
            os.path.join(os.path.dirname(self.workspace), "..", "partner", "partner", rel_path),
        ]
        for c in candidates:
            norm = os.path.normpath(c)
            if os.path.exists(norm):
                return norm
        return None

    def _apply_finding(self, finding: dict) -> bool:
        """Apply a finding at code level. Returns True if applied."""
        ftype = finding.get("TYPE", "")
        fpath = finding.get("FILE", "")
        fix = finding.get("FIX", "")

        if ftype == "config":
            return self._apply_config_fix(fpath, fix)
        
        if ftype in ("handler", "wrapper", "evolution", "model", "prompt", "codebase"):
            return self._apply_code_fix(finding)
        
        patch = {
            "timestamp": datetime.now().isoformat(),
            "type": ftype, "file": fpath,
            "issue": finding.get("ISSUE", ""), "fix": fix,
            "applied": False,
            "reason": f"Type {ftype} requires manual review",
        }
        self._save_patch(patch)
        return False

    def _apply_code_fix(self, finding: dict) -> bool:
        """Apply a code-level fix using auto_fixer (incremental patch)."""
        fpath = finding.get("FILE", "")
        issue = finding.get("ISSUE", "")
        fix_desc = finding.get("FIX", "")
        
        resolved = self._resolve_path(fpath)
        if not resolved:
            logger.debug("[PROACTIVE] Cannot resolve path: %s", fpath)
            return False
        
        if not self.adapter:
            return False
        
        try:
            from .auto_fixer import try_fix
            result = try_fix(resolved, issue, fix_desc, self.adapter)
            
            if result.get("ok"):
                self._save_patch({
                    "timestamp": datetime.now().isoformat(),
                    "type": "code", "file": fpath, "resolved": resolved,
                    "issue": issue, "fix": fix_desc,
                    "applied": True,
                    "method": "auto_fixer",
                })
                logger.info("[PROACTIVE] Auto-fix applied to %s: %s", 
                           os.path.basename(resolved), issue[:80])
                return True
            else:
                logger.debug("[PROACTIVE] Auto-fix failed: %s", result.get("error","")[:100])
                return False
        except ImportError:
            logger.debug("[PROACTIVE] auto_fixer not available")
            return False
        except Exception as e:
            logger.debug("[PROACTIVE] Auto-fix error: %s", e)
            return False

    def _apply_config_fix(self, fpath: str, fix: str) -> bool:
        """Apply a config-level fix."""
        try:
            config_path = os.path.join(self.workspace, "config", fpath)
            if not os.path.exists(config_path):
                config_path = os.path.join(self.workspace, "..", "config", "external_calls.yaml")
            
            if os.path.exists(config_path):
                with open(config_path, "a") as f:
                    f.write(f"\n# Proactive evolution: {fix}\n")
                
                self._save_patch({
                    "timestamp": datetime.now().isoformat(),
                    "type": "config", "file": fpath,
                    "issue": finding if isinstance(finding := {}, dict) else {},
                    "fix": fix, "applied": True,
                })
                logger.info("[PROACTIVE] Config fix applied: %s", fpath)
                return True
        except Exception as e:
            logger.debug("[PROACTIVE] Config fix failed: %s", e)
        return False

    def _apply_prompt_fix(self, fpath: str, fix: str) -> bool:
        """Apply a prompt-level fix via LLM code generation."""
        if not self.adapter:
            return False
        
        try:
            code_prompt = CODE_FIX_PROMPT.format(
                file_path=fpath,
                issue="improvement",
                fix_description=fix,
            )
            response = self.adapter.chat(code_prompt, purpose="proactive_code_fix")
            
            if response and '--- old' in response and '--- new' in response:
                self._save_patch({
                    "timestamp": datetime.now().isoformat(),
                    "type": "prompt", "file": fpath,
                    "fix": fix, "diff": response[:1000],
                    "applied": False,  # Mark for review
                    "reason": "Generated diff, needs review",
                })
                logger.info("[PROACTIVE] Generated fix for %s (needs review)", fpath)
                return True
        except Exception as e:
            logger.debug("[PROACTIVE] Prompt fix failed: %s", e)
        return False

    def _read_self_awareness(self) -> str:
        """Read the self-awareness document — primary input for evolution."""
        for candidate in [
            os.path.join(self.workspace, "..", "..", "partner", "docs", "self_awareness.md"),
        ]:
            p = os.path.normpath(candidate)
            if os.path.exists(p):
                try:
                    with open(p) as f:
                        return f.read()
                except:
                    pass
        return ""

    def _read_journal(self) -> str:
        for candidate in [
            os.path.join(self.workspace, "..", "..", "partner", "docs", "evolution_journal.md"),
        ]:
            p = os.path.normpath(candidate)
            if os.path.exists(p):
                try:
                    with open(p) as f:
                        return f.read()
                except:
                    pass
        return ""

    def _read_capabilities(self) -> str:
        for candidate in [
            os.path.join(self.workspace, "..", "..", "partner", "docs", "skill.md"),
        ]:
            p = os.path.normpath(candidate)
            if os.path.exists(p):
                try:
                    with open(p) as f:
                        return f.read()
                except:
                    pass
        return ""

    def run_loop(self):
        """Background loop: scan → suggest → apply safe fixes → repeat."""
        logger.info("[PROACTIVE] Started for instance %s, interval=%ds",
                     self.instance_id, self.SCAN_INTERVAL)
        while True:
            try:
                findings = self.scan()
                applied = 0
                for f in findings[:5]:
                    if self.apply_if_safe(f):
                        applied += 1
                
                if findings:
                    logger.info("[PROACTIVE] Scan: %d findings, %d auto-applied",
                               len(findings), applied)
                else:
                    logger.debug("[PROACTIVE] Scan: no new findings")
                
            except Exception as e:
                logger.debug("[PROACTIVE] scan error: %s", e)
            
            time.sleep(self.SCAN_INTERVAL)

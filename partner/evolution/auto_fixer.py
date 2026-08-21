"""
Auto Fixer — incremental code patching for proactive evolution (Sprint 7).

Replaces the old "LLM rewrites entire file" approach with:
1. Read target file
2. Ask LLM to generate old_string → new_string replacement  
3. Apply with python patch tool
4. Syntax check
5. Rollback on failure
"""

from __future__ import annotations
import logging, os, re, subprocess, time, json, ast, py_compile
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

FIX_PROMPT = """You are fixing a bug in a Python file. Read the code, then output ONLY the replacement.

FILE: {file_path}
ISSUE: {issue}
FIX: {fix_description}

The current code (around the problem area):
```
{code_context}
```

Output your fix in this EXACT format — only the lines to be replaced:
```
--- SEARCH
<exact old lines to find>
--- REPLACE
<exact new lines to put in place>
```

Rules:
1. SEARCH must match EXACTLY (including whitespace/indentation) — copy from the code above
2. Keep the change minimal — only change what's needed
3. Preserve all imports and surrounding code
4. Output NOTHING else besides the SEARCH/REPLACE block"""


def try_fix(file_path: str, issue: str, fix_description: str, adapter=None) -> dict:
    """Attempt to fix a bug in a file. Returns result dict."""
    
    # 1. Read the target file
    if not os.path.exists(file_path):
        return {"ok": False, "error": f"File not found: {file_path}"}
    
    with open(file_path) as f:
        original = f.read()
    
    # 2. Extract relevant context (the area around the problem)
    context = _extract_context(original, issue)
    
    # 3. Ask LLM for patch
    if not adapter:
        return {"ok": False, "error": "No LLM adapter available"}
    
    prompt = FIX_PROMPT.format(
        file_path=os.path.basename(file_path),
        issue=issue,
        fix_description=fix_description,
        code_context=context[:3000],
    )
    
    try:
        response = adapter.chat(prompt, purpose="auto_fix")
        if not response:
            return {"ok": False, "error": "LLM returned empty response"}
    except Exception as e:
        return {"ok": False, "error": f"LLM call failed: {e}"}
    
    # 4. Parse the patch
    search_block, replace_block = _parse_patch(response)
    if not search_block:
        return {"ok": False, "error": "Could not parse SEARCH/REPLACE from LLM response"}
    
    # 5. Check if search block exists in file
    if search_block not in original:
        # Try fuzzy match
        search_block = _fuzzy_match(search_block, original)
        if not search_block:
            return {"ok": False, "error": "SEARCH block not found in file (even with fuzzy matching)"}
    
    # 6. Apply the patch (backup first)
    backup = file_path + ".auto_fix_backup"
    with open(backup, "w") as f:
        f.write(original)
    
    patched = original.replace(search_block, replace_block, 1)
    
    if patched == original:
        return {"ok": False, "error": "Patch did not change the file"}
    
    with open(file_path, "w") as f:
        f.write(patched)
    
    # 7. Syntax check
    try:
        py_compile.compile(file_path, doraise=True)
        # Also try ast.parse for deeper check
        with open(file_path) as f:
            ast.parse(f.read())
    except (py_compile.PyCompileError, SyntaxError) as e:
        # Rollback
        with open(backup) as f:
            original2 = f.read()
        with open(file_path, "w") as f:
            f.write(original2)
        return {"ok": False, "error": f"Syntax check failed: {e}", "rolled_back": True}
    
    # 8. Success!
    return {
        "ok": True,
        "file": file_path,
        "backup": backup,
        "issue": issue,
        "patch_search": search_block[:200],
        "patch_replace": replace_block[:200],
    }


def _parse_patch(response: str) -> tuple[str | None, str | None]:
    """Parse SEARCH/REPLACE from LLM response."""
    search = None
    replace = None
    
    if "--- SEARCH" in response and "--- REPLACE" in response:
        parts = response.split("--- SEARCH")
        if len(parts) > 1:
            rest = parts[1].split("--- REPLACE")
            if len(rest) > 1:
                search = rest[0].strip()
                replace = rest[1].strip()
                # Remove trailing backticks
                replace = replace.split("```")[0].strip()
    
    if not search and "```diff" in response:
        # Fallback: try diff format
        parts = response.split("```diff")
        if len(parts) > 1:
            diff = parts[1].split("```")[0]
            old_lines = []
            new_lines = []
            for line in diff.split('\n'):
                if line.startswith('-'):
                    old_lines.append(line[1:])
                elif line.startswith('+'):
                    new_lines.append(line[1:])
            if old_lines:
                search = '\n'.join(old_lines)
                replace = '\n'.join(new_lines)
    
    return search, replace


def _extract_context(code: str, issue: str) -> str:
    """Extract relevant code context based on issue keywords."""
    lines = code.split('\n')
    keywords = set(issue.lower().split())
    
    # Score each line for relevance
    scores = []
    for i, line in enumerate(lines):
        score = 0
        line_lower = line.lower()
        for kw in keywords:
            if kw in line_lower:
                score += 10
        if 'def ' in line:
            score += 5
        if 'class ' in line:
            score += 3
        scores.append((score, i))
    
    scores.sort(key=lambda x: -x[0])
    
    # Get top 3 relevant areas, expand to context windows
    if not scores or scores[0][0] == 0:
        # No keyword match — return file head + tail
        return '\n'.join(lines[:50]) + "\n...\n" + '\n'.join(lines[-30:])
    
    contexts = []
    seen = set()
    for score, idx in scores[:3]:
        start = max(0, idx - 15)
        end = min(len(lines), idx + 15)
        if start not in seen:
            contexts.append(f"--- around line {idx+1} ---\n" + '\n'.join(lines[start:end]))
            seen.add(start)
    
    return '\n\n'.join(contexts[:3])


def _fuzzy_match(search: str, code: str) -> str | None:
    """Try to find search string with fuzzy matching."""
    # Skip leading/trailing whitespace differences
    search_stripped = search.strip()
    code_stripped = code.strip()
    
    if search_stripped in code_stripped:
        # Find the actual occurrence with original whitespace
        idx = code_stripped.find(search_stripped)
        return code_stripped[idx:idx+len(search_stripped)]
    
    # Try matching by first and last lines
    search_lines = search_stripped.split('\n')
    if len(search_lines) >= 2:
        first = search_lines[0].strip()
        last = search_lines[-1].strip()
        for i, line in enumerate(code.split('\n')):
            if first in line:
                # Check if last line matches within 50 lines
                block = '\n'.join(code.split('\n')[i:i+50])
                if last in block:
                    # Found! Return exact block
                    return search  # Use original search with fuzzy matching note
    
    return None

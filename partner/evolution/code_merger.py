"""Code Merger — intelligent code merging for Partner's self-evolution.

Replaces the blind append-to-file pattern with structural code merging:
1. Parse existing file structure (classes, functions, imports)
2. Check if new code would duplicate existing definitions
3. Merge into correct position (inside existing class, or at file end)
4. Enforce file size limits and quality constraints

Usage:
    from partner.evolution.code_merger import CodeMerger

    merger = CodeMerger("/path/to/target.py")
    result = merger.merge("def new_func(): pass")
    print(result.action)  # "appended" | "merged_into_class" | "skipped_duplicate"
"""

from __future__ import annotations

import ast
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximum lines per file before triggering refactoring warning
MAX_FILE_LINES = 5000
# Maximum allowed duplicate class definitions
MAX_DUPLICATE_CLASS_INSTANCES = 1
# Maximum allowed duplicate function definitions per class
MAX_DUPLICATE_FUNC_INSTANCES = 1


# ═══════════════════════════════════════════════════════════════════════════════
# Data Types
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ClassDef:
    """Represents an existing class definition in a Python file."""
    name: str
    start_line: int
    end_line: int
    methods: list[str]
    base_classes: list[str]


@dataclass
class FuncDef:
    """Represents an existing function definition (module-level or method)."""
    name: str
    start_line: int
    end_line: int
    parent_class: str | None = None  # None = module-level function


@dataclass
class FileStructure:
    """Complete parsed structure of a Python file."""
    path: str
    imports: list[str]
    classes: list[ClassDef]
    functions: list[FuncDef]
    total_lines: int
    class_names: set[str]
    function_names: set[str]
    method_map: dict[str, set[str]]  # class_name -> {method_names}


@dataclass
class MergeResult:
    """Result of a merge operation."""
    action: str  # "appended" | "merged_into_class" | "skipped_duplicate" | "error"
    target_file: str
    target_class: str | None = None
    target_method: str | None = None
    message: str = ""
    new_total_lines: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
# Code Parser
# ═══════════════════════════════════════════════════════════════════════════════


def parse_file_structure(file_path: str) -> FileStructure:
    """Parse a Python file and extract its structural information.

    Uses the AST module to identify classes, methods, functions, and imports.
    Falls back to regex-based extraction if AST parsing fails.
    """
    if not os.path.exists(file_path):
        return FileStructure(
            path=file_path, imports=[], classes=[], functions=[],
            total_lines=0, class_names=set(), function_names=set(),
            method_map={},
        )

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.split("\n")
    total_lines = len(lines)

    # Try AST first
    try:
        return _parse_via_ast(file_path, content, lines, total_lines)
    except SyntaxError:
        logger.warning("[MERGER] AST parse failed for %s, falling back to regex", file_path)
        return _parse_via_regex(file_path, content, lines, total_lines)


def _parse_via_ast(
    file_path: str, content: str, lines: list[str], total_lines: int
) -> FileStructure:
    """Parse Python file structure using AST (most accurate)."""
    tree = ast.parse(content)

    imports: list[str] = []
    classes: list[ClassDef] = []
    functions: list[FuncDef] = []
    class_names: set[str] = set()
    function_names: set[str] = set()
    method_map: dict[str, set[str]] = {}

    for node in ast.walk(tree):
        # Collect import statements
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if hasattr(node, 'lineno'):
                imports.append(lines[node.lineno - 1].strip() if node.lineno <= len(lines) else "")

        # Collect class definitions
        if isinstance(node, ast.ClassDef):
            methods = [
                n.name for n in ast.walk(node)
                if isinstance(n, ast.FunctionDef)
            ]
            bases = [
                _resolve_base_name(b) for b in node.bases
            ]
            cd = ClassDef(
                name=node.name,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                methods=methods,
                base_classes=bases,
            )
            classes.append(cd)
            class_names.add(node.name)
            method_map[node.name] = set(methods)

        # Collect module-level functions (not methods)
        if isinstance(node, ast.FunctionDef) and not _is_method(node, tree):
            fd = FuncDef(
                name=node.name,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                parent_class=None,
            )
            functions.append(fd)
            function_names.add(node.name)

    return FileStructure(
        path=file_path, imports=imports, classes=classes,
        functions=functions, total_lines=total_lines,
        class_names=class_names, function_names=function_names,
        method_map=method_map,
    )


def _parse_via_regex(
    file_path: str, content: str, lines: list[str], total_lines: int
) -> FileStructure:
    """Fallback parser using regex when AST fails (e.g., syntax errors)."""
    imports: list[str] = []
    classes: list[ClassDef] = []
    functions: list[FuncDef] = []
    class_names: set[str] = set()
    function_names: set[str] = set()
    method_map: dict[str, set[str]] = {}

    # Extract imports
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            imports.append(stripped)

    # Extract class definitions (simplistic regex)
    class_pattern = re.compile(r"^class\s+(\w+)\s*(?:\(([^)]*)\))?\s*:")
    func_pattern = re.compile(r"^\s+def\s+(\w+)\s*\(")
    module_func_pattern = re.compile(r"^def\s+(\w+)\s*\(")

    current_class: ClassDef | None = None
    for i, line in enumerate(lines):
        m = class_pattern.match(line)
        if m:
            current_class = ClassDef(
                name=m.group(1),
                start_line=i + 1,
                end_line=i + 1,
                methods=[],
                base_classes=[b.strip() for b in m.group(2).split(",") if b.strip()] if m.group(2) else [],
            )
            classes.append(current_class)
            class_names.add(m.group(1))
            method_map[m.group(1)] = set()
            continue

        if current_class:
            fm = func_pattern.match(line)
            if fm:
                current_class.methods.append(fm.group(1))
                method_map[current_class.name].add(fm.group(1))
                current_class.end_line = i + 1

        # Module-level function (outside any class)
        if current_class is None:
            fm = module_func_pattern.match(line)
            if fm:
                fd = FuncDef(name=fm.group(1), start_line=i + 1, end_line=i + 1)
                functions.append(fd)
                function_names.add(fm.group(1))

    return FileStructure(
        path=file_path, imports=imports, classes=classes,
        functions=functions, total_lines=total_lines,
        class_names=class_names, function_names=function_names,
        method_map=method_map,
    )


def _resolve_base_name(node: ast.expr) -> str:
    """Convert an AST base class expression to a string name."""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        return f"{_resolve_base_name(node.value)}.{node.attr}"
    elif isinstance(node, ast.Subscript):
        return f"{_resolve_base_name(node.value)}"
    return "?"


def _is_method(node: ast.FunctionDef, tree: ast.Module) -> bool:
    """Check if a FunctionDef is a method (inside a class) vs module-level."""
    for parent in ast.walk(tree):
        if isinstance(parent, ast.ClassDef):
            if node in ast.walk(parent) and node is not parent:
                return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# Code Generation Analysis
# ═══════════════════════════════════════════════════════════════════════════════


def _extract_new_class_name(code_snippet: str) -> str | None:
    """Extract class name from generated code snippet."""
    m = re.search(r"^class\s+(\w+)\s*[(:]", code_snippet, re.MULTILINE)
    return m.group(1) if m else None


def _extract_new_function_name(code_snippet: str) -> str | None:
    """Extract function name from generated code snippet."""
    m = re.search(r"^def\s+(\w+)\s*\(", code_snippet, re.MULTILINE)
    return m.group(1) if m else None


def _has_self_methods(code_snippet: str) -> bool:
    """Check if snippet has 0-indent defs with 'self' as first param.

    LLM sometimes generates methods (def with self) at 0 indent level
    instead of wrapped inside a class. These are orphaned methods that
    would cause IndentationError if appended as module-level functions.
    """
    # Count 0-indent defs that have 'self' as first parameter
    count = len(re.findall(
        r"^\s*def\s+\w+\s*\(\s*self\b",
        code_snippet, re.MULTILINE,
    ))
    return count > 0


def _extract_new_class_methods(code_snippet: str) -> list[str]:
    """Extract method names from a class definition in the code snippet."""
    methods = re.findall(r"^\s+def\s+(\w+)\s*\(", code_snippet, re.MULTILINE)
    return methods


def _detect_change_intent(code_snippet: str) -> dict[str, Any]:
    """Analyze a code snippet to determine what it intends to do.

    Returns:
        dict with keys:
        - "type": "new_class" | "new_method" | "new_function" | "orphaned_methods" | "modify" | "unknown"
        - "class_name": str | None
        - "function_name": str | None
        - "methods": list[str] (methods in the new class)
    """
    class_name = _extract_new_class_name(code_snippet)
    function_name = _extract_new_function_name(code_snippet)
    methods = _extract_new_class_methods(code_snippet)

    # Detect orphaned methods: defs at any indent level with 'self' param
    # but no containing class. LLM often outputs methods at 0-indent
    # instead of wrapped inside a class statement.
    if not class_name and _has_self_methods(code_snippet):
        return {
            "type": "orphaned_methods",
            "class_name": None,
            "function_name": function_name,
            "methods": methods,
        }

    if class_name and methods:
        return {
            "type": "new_class",
            "class_name": class_name,
            "function_name": None,
            "methods": methods,
        }
    elif class_name:
        return {
            "type": "new_class",
            "class_name": class_name,
            "function_name": None,
            "methods": [],
        }
    elif function_name:
        return {
            "type": "new_function",
            "class_name": None,
            "function_name": function_name,
            "methods": [],
        }
    else:
        return {
            "type": "unknown",
            "class_name": None,
            "function_name": None,
            "methods": [],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Code Cleaners
# ═══════════════════════════════════════════════════════════════════════════════


def _strip_auto_evolution_marker(code_snippet: str) -> str:
    """Remove any existing auto-evolution markers from the snippet."""
    lines = code_snippet.split("\n")
    cleaned = [
        l for l in lines
        if "Auto-evolution addition" not in l
        and not l.strip().startswith("# ----")
        and not l.strip().startswith("# ---")
    ]
    return "\n".join(cleaned).strip()


def _filter_from_future(code_snippet: str) -> str:
    """Remove 'from __future__' lines that would be invalid mid-file."""
    return re.sub(r"^from __future__ import.*\n?", "", code_snippet, flags=re.MULTILINE)


def _normalize_imports(code_snippet: str) -> str:
    """Fix common import mistakes in generated code.

    - QStringListModel from QtWidgets -> QtCore
    """
    fixed = code_snippet.replace(
        "from PySide6.QtWidgets import QStringListModel",
        "from PySide6.QtCore import QStringListModel",
    )
    return fixed


def clean_code_snippet(code_snippet: str) -> str:
    """Apply all cleaning passes to a generated code snippet.

    - Strip auto-evolution markers
    - Remove mid-file from __future__
    - Fix common import errors
    """
    cleaned = _strip_auto_evolution_marker(code_snippet)
    cleaned = _filter_from_future(cleaned)
    cleaned = _normalize_imports(cleaned)
    return cleaned.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# Code Merger
# ═══════════════════════════════════════════════════════════════════════════════


class CodeMerger:
    """Intelligent code merger that replaces blind file appending.

    Usage:
        merger = CodeMerger("widgets.py")
        result = merger.merge("class NewWidget: ...")
    """

    def __init__(self, file_path: str | Path, max_lines: int = MAX_FILE_LINES):
        self.file_path = str(file_path)
        self.max_lines = max_lines
        self._structure = parse_file_structure(self.file_path)
        self._structure_valid = self._structure.total_lines > 0

    @property
    def structure(self) -> FileStructure:
        """Lazy-loaded file structure."""
        if not hasattr(self, '_structure') or not self._structure:
            self._structure = parse_file_structure(self.file_path)
            self._structure_valid = self._structure.total_lines > 0
        return self._structure

    def merge(self, code_snippet: str) -> MergeResult:
        """Merge a code snippet into the target file intelligently.

        Strategy:
        1. Clean the snippet
        2. Analyze intent (new class? new function? modify?)
        3. Check for duplicates
        4. Find correct insertion position
        5. Insert or append

        Returns:
            MergeResult with action and details.
        """
        if not code_snippet or not code_snippet.strip():
            return MergeResult(
                action="skipped_duplicate",
                target_file=self.file_path,
                message="Empty code snippet, skipped",
                new_total_lines=self._structure.total_lines,
            )

        code_snippet = clean_code_snippet(code_snippet)
        if not code_snippet:
            return MergeResult(
                action="skipped_duplicate",
                target_file=self.file_path,
                message="Code snippet was empty after cleaning",
                new_total_lines=self._structure.total_lines,
            )

        intent = _detect_change_intent(code_snippet)

        # Check file size limit
        if self._structure.total_lines >= self.max_lines:
            logger.warning(
                "[MERGER] File %s has %d lines (max %d). "
                "Triggering size cap — adding snippet with refactoring warning.",
                self.file_path, self._structure.total_lines, self.max_lines,
            )

        # Strategy based on intent type
        if intent["type"] == "new_class":
            return self._merge_new_class(code_snippet, intent)
        elif intent["type"] == "new_function":
            return self._merge_new_function(code_snippet, intent)
        elif intent["type"] == "orphaned_methods":
            return self._skip_orphaned_methods(code_snippet, intent)
        else:
            return self._merge_generic(code_snippet, intent)

    def _merge_new_class(
        self, code_snippet: str, intent: dict[str, Any]
    ) -> MergeResult:
        """Merge a new class definition.

        - If class already exists: merge methods into existing class
        - If class doesn't exist: append to file end
        """
        class_name = intent["class_name"]
        new_methods = intent["methods"]

        if not class_name:
            return self._merge_generic(code_snippet, intent)

        if class_name in self._structure.class_names:
            # Class already exists — merge methods into it
            existing_methods = self._structure.method_map.get(class_name, set())
            new_unique_methods = [m for m in new_methods if m not in existing_methods]

            if not new_unique_methods:
                return MergeResult(
                    action="skipped_duplicate",
                    target_file=self.file_path,
                    target_class=class_name,
                    message=f"Class '{class_name}' already exists with all methods, skipped",
                    new_total_lines=self._structure.total_lines,
                )

            # Extract only the new unique methods from the snippet
            snippet_to_add = self._extract_methods_only(code_snippet, new_unique_methods)
            if not snippet_to_add:
                return MergeResult(
                    action="skipped_duplicate",
                    target_file=self.file_path,
                    target_class=class_name,
                    message=f"Could not extract unique methods for '{class_name}', skipped",
                    new_total_lines=self._structure.total_lines,
                )

            # Insert methods into the class (before closing)
            inserted = self._insert_methods_into_class(class_name, snippet_to_add)
            self._structure = parse_file_structure(self.file_path)
            return MergeResult(
                action="merged_into_class",
                target_file=self.file_path,
                target_class=class_name,
                message=f"Merged {len(new_unique_methods)} new method(s) into existing class '{class_name}'",
                new_total_lines=self._structure.total_lines,
            )

        # New class — append to file
        marker = f"\n\n# ── Auto-evolution addition ──\n# {class_name} — added by self-evolution\n"
        with open(self.file_path, "a", encoding="utf-8") as f:
            f.write(marker + code_snippet + "\n")

        self._structure = parse_file_structure(self.file_path)
        return MergeResult(
            action="appended",
            target_file=self.file_path,
            target_class=class_name,
            message=f"New class '{class_name}' appended to {os.path.basename(self.file_path)}",
            new_total_lines=self._structure.total_lines,
        )

    def _merge_new_function(
        self, code_snippet: str, intent: dict[str, Any]
    ) -> MergeResult:
        """Merge a new module-level function.

        - If function already exists: skip
        - If function doesn't exist: append to file end
        """
        func_name = intent["function_name"]
        if not func_name:
            return self._merge_generic(code_snippet, intent)

        if func_name in self._structure.function_names:
            return MergeResult(
                action="skipped_duplicate",
                target_file=self.file_path,
                target_method=func_name,
                message=f"Function '{func_name}' already exists, skipped",
                new_total_lines=self._structure.total_lines,
            )

        marker = f"\n\n# ── Auto-evolution addition ──\n# {func_name} — added by self-evolution\n"
        with open(self.file_path, "a", encoding="utf-8") as f:
            f.write(marker + code_snippet + "\n")

        self._structure = parse_file_structure(self.file_path)
        return MergeResult(
            action="appended",
            target_file=self.file_path,
            target_method=func_name,
            message=f"New function '{func_name}' appended to {os.path.basename(self.file_path)}",
            new_total_lines=self._structure.total_lines,
        )

    def _merge_generic(
        self, code_snippet: str, intent: dict[str, Any]
    ) -> MergeResult:
        """Generic fallback: append to file end with marker."""
        marker = f"\n\n# ── Auto-evolution addition ──\n"
        with open(self.file_path, "a", encoding="utf-8") as f:
            f.write(marker + code_snippet + "\n")

        self._structure = parse_file_structure(self.file_path)
        return MergeResult(
            action="appended",
            target_file=self.file_path,
            message=f"Code appended to {os.path.basename(self.file_path)}",
            new_total_lines=self._structure.total_lines,
        )

    def _skip_orphaned_methods(
        self, code_snippet: str, intent: dict[str, Any]
    ) -> MergeResult:
        """Skip orphaned methods (indented defs without a containing class).

        LLM sometimes generates methods that belong to a class that was already
        deduplicated. These methods would cause SyntaxError if appended.
        """
        logger.warning(
            "[MERGER] Skipping %d orphaned method(s) without a containing class: %s",
            len(intent.get("methods", [])),
            ", ".join(intent.get("methods", [])[:5]),
        )
        return MergeResult(
            action="skipped_duplicate",
            target_file=self.file_path,
            message=f"Skipped {len(intent.get('methods', []))} orphaned method(s) without a class wrapper",
            new_total_lines=self._structure.total_lines,
        )

    def _extract_methods_only(self, code_snippet: str, method_names: list[str]) -> str:
        """Extract only specific methods from a class definition snippet."""
        if not method_names:
            return ""

        lines = code_snippet.split("\n")
        collected: list[str] = []
        in_target_method = False
        indent_level = 0
        current_method = ""

        for line in lines:
            stripped = line.strip()

            # Detect method start
            m = re.match(r"^(\s*)def\s+(\w+)\s*\(", line)
            if m:
                # If we were collecting a method, save it
                if in_target_method and current_method:
                    collected.append(current_method)
                    collected.append("")  # blank line separator

                method_name = m.group(2)
                in_target_method = method_name in method_names
                indent_level = len(m.group(1))
                current_method = line if in_target_method else ""
            elif in_target_method:
                # Check if we've exited the method (dedent below method indent)
                if stripped and not stripped.startswith("#") and not stripped.startswith('"""'):
                    current_indent = len(line) - len(line.lstrip())
                    if current_indent <= indent_level and stripped:
                        # Save and exit
                        if current_method.strip():
                            collected.append(current_method)
                        in_target_method = False
                        current_method = ""
                    else:
                        current_method += "\n" + line
                else:
                    current_method += "\n" + line

        # Don't forget the last collected method
        if in_target_method and current_method.strip():
            collected.append(current_method)

        return "\n".join(collected).strip()

    def _insert_methods_into_class(self, class_name: str, methods_code: str) -> bool:
        """Insert methods into an existing class (before the class's closing).

        Reads the file, finds the class, inserts methods before the last
        dedented line of the class, and writes back.
        """
        if not methods_code.strip():
            return False

        with open(self.file_path, "r", encoding="utf-8") as f:
            content = f.read()
        lines = content.split("\n")

        # Find the class definition
        class_start = None
        class_indent = ""
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(f"class {class_name}") and ":" in stripped:
                class_start = i
                class_indent = line[:len(line) - len(line.lstrip())]
                break

        if class_start is None:
            logger.warning("[MERGER] Could not find class '%s' in %s", class_name, self.file_path)
            return False

        # Find the end of the class: last line with same or greater indent
        class_end = len(lines) - 1
        for i in range(class_start + 1, len(lines)):
            stripped = lines[i].strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith('"""'):
                current_indent = len(lines[i]) - len(lines[i].lstrip())
                # A line at class level indent with actual code means class ended
                if current_indent <= len(class_indent) and not stripped.startswith("@"):
                    class_end = i - 1 if stripped != "    " else i
                    break

        # Prepare the method code with proper indentation
        indented_methods = "\n".join(
            f"    {l}" if l.strip() else l
            for l in methods_code.split("\n")
        )

        # Insert before the class end
        insert_line = class_end
        # If class ends with blank lines, step back
        while insert_line > class_start and not lines[insert_line].strip():
            insert_line -= 1

        lines.insert(insert_line + 1, indented_methods)

        with open(self.file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        logger.info(
            "[MERGER] Inserted methods into class '%s' at line %d",
            class_name, insert_line + 1,
        )
        return True


# ═══════════════════════════════════════════════════════════════════════════════
# Quality Check
# ═══════════════════════════════════════════════════════════════════════════════


def check_code_quality(file_path: str) -> dict[str, Any]:
    """Check code quality of a file and return metrics.

    Returns:
        dict with:
        - "total_lines": int
        - "num_classes": int
        - "num_functions": int
        - "duplicate_classes": list[str] (class names that appear more than once)
        - "over_max_lines": bool
        - "class_count_by_name": dict[str, int]
    """
    structure = parse_file_structure(file_path)

    # Count class name frequencies
    class_name_counts: dict[str, int] = {}
    for cls in structure.classes:
        class_name_counts[cls.name] = class_name_counts.get(cls.name, 0) + 1

    duplicate_classes = [
        name for name, count in class_name_counts.items()
        if count > MAX_DUPLICATE_CLASS_INSTANCES
    ]

    return {
        "total_lines": structure.total_lines,
        "num_classes": len(structure.classes),
        "num_functions": len(structure.functions),
        "duplicate_classes": duplicate_classes,
        "over_max_lines": structure.total_lines > MAX_FILE_LINES,
        "class_count_by_name": class_name_counts,
        "class_names": sorted(structure.class_names),
        "function_names": sorted(structure.function_names),
    }

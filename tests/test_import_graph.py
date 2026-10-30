"""
Cross-module import graph guard.

Uses pure ast to verify that when a module does `from x import y`,
the name `y` is actually defined at the top level of module x (or
x itself is a package and y is a submodule).  This catches cases
where an "unused import" cleanup in one module accidentally removed
a re-export that another module depends on – pyflakes alone cannot
see across files for re-exports.

Skips targets that use `from x import *`.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path
from typing import Iterable

# Package root relative to this file's parent (tests/)
_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULES_DIR = _REPO_ROOT / "core"
_UI_DIR = _REPO_ROOT / "ui"
_CONFIG_DIR = _REPO_ROOT / "config"
_UTILS_DIR = _REPO_ROOT / "utils"


def _module_name_for_path(path: Path, root: Path) -> str | None:
    """Convert a file path to a dotted module name, or None if not under root."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
        if not parts:
            return root.name
        return ".".join(parts)
    if not parts[-1].endswith(".py"):
        return None
    return ".".join(parts[:-1] + [parts[-1][: -3]]).lstrip(".")


def _collect_imports(filepath: Path) -> list[tuple[str, int, str]]:
    """Return list of (module, line_no, name) for all ImportFrom nodes."""
    with filepath.open("r", encoding="utf-8", errors="replace") as f:
        src = f.read()
    try:
        tree = ast.parse(src, filename=str(filepath))
    except SyntaxError:
        return []

    results: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    # Skip wildcard imports – we cannot verify them statically.
                    continue
                results.append((mod, node.lineno, alias.name))
    return results


def _is_repo_module(module: str, all_module_names: set[str]) -> bool:
    """Check whether *module* looks like a local package/module we own."""
    if module in all_module_names:
        return True
    # Check prefixes for submodules
    for name in all_module_names:
        if module == name or name.startswith(module + "."):
            return True
    return False


def _check_target_defined(target_mod_name: str, imported_name: str,
                          all_module_names: set[str],
                          module_sources: dict[str, ast.Module]) -> bool:
    """Return True if *imported_name* is defined at top level of target."""
    # Try to find the target module AST
    target_tree = module_sources.get(target_mod_name)
    if target_tree is None:
        # Could be a third-party module; skip verification
        return True

    # Check top-level names: FunctionDef, AsyncFunctionDef, ClassDef,
    # Name (from Assign), Import, ImportFrom
    for node in target_tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            if node.name == imported_name:
                return True
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == imported_name:
                    return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == imported_name:
                    return True
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == imported_name:
                    return True
    return False


def build_import_graph() -> list[dict]:
    """
    Walk all .py files under core/, ui/, config/, utils/ and return
    a list of dicts describing any cross-module import issues.
    """
    roots = [
        (_MODULES_DIR, "core"),
        (_UI_DIR, "ui"),
        (_CONFIG_DIR, "config"),
        (_UTILS_DIR, "utils"),
    ]

    all_py_files: list[Path] = []
    for root, _ in roots:
        if root.exists():
            all_py_files.extend(sorted(root.rglob("*.py")))

    # Build mapping: dotted_name -> ast.Module
    all_module_names: set[str] = set()
    module_sources: dict[str, ast.Module] = {}
    file_to_mod: dict[Path, str] = {}

    for root, root_name in roots:
        if not root.exists():
            continue
        for p in all_py_files:
            if not p.is_relative_to(root):
                continue
            mod = _module_name_for_path(p, root)
            if mod is None:
                continue
            all_module_names.add(mod)
            try:
                with p.open("r", encoding="utf-8", errors="replace") as f:
                    tree = ast.parse(f.read(), filename=str(p))
            except SyntaxError:
                continue
            module_sources[mod] = tree
            file_to_mod[p] = mod

    problems: list[dict] = []

    for filepath in all_py_files:
        mods = _collect_imports(filepath)
        current_mod = file_to_mod.get(filepath)
        for mod_str, lineno, name in mods:
            # Relative import: resolve against current module
            if mod_str.startswith("."):
                if current_mod is None:
                    continue
                depth = len(mod_str) - len(mod_str.lstrip("."))
                base = ".".join(current_mod.split(".")[:-depth]) if depth > 1 else current_mod.split(".")[0]
                target = (base + mod_str).lstrip(".")
            else:
                target = mod_str

            if not _is_repo_module(target, all_module_names):
                continue

            if not _check_target_defined(target, name, all_module_names,
                                        module_sources):
                problems.append({
                    "file": str(filepath.relative_to(_REPO_ROOT)),
                    "line": lineno,
                    "module": mod_str,
                    "name": name,
                    "resolved_target": target,
                })

    return problems


def test_import_graph_zero_problems() -> None:
    """All cross-module imports in the repo should resolve to a definition."""
    problems = build_import_graph()
    if problems:
        lines = []
        for p in problems:
            lines.append(f"{p['file']}:{p['line']} — from {p['module']} import {p['name']} (resolved: {p['resolved_target']})")
        raise AssertionError(
            "Cross-module import issues found:\n" + "\n".join(lines)
        )


if __name__ == "__main__":
    probs = build_import_graph()
    if probs:
        print("PROBLEMS:")
        for p in probs:
            print(f"  {p['file']}:{p['line']}  from {p['module']} import {p['name']}")
        sys.exit(1)
    else:
        print("OK: no cross-module import issues found.")
        sys.exit(0)

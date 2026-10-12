"""Map pytest failures to suspect source files, ranked by traceback and import evidence."""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .patcher import is_test_file
from .pytest_parser import PytestReport

_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", ".tox", "node_modules", "site-packages"}
_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")

# Evidence weights: a frame in the traceback is the strongest signal.
_W_FRAME = 3.0
_W_TEST_IMPORT = 2.0
_W_NAME = 1.5
_W_TRANSITIVE = 1.0
_W_PACKAGE = 0.5  # a package __init__ is usually only a re-export


@dataclass
class Suspect:
    """A source file the model should see, with why it was picked."""

    path: str
    score: float
    reasons: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    content: str = ""
    truncated: bool = False


def source_files(repo: Path) -> list[str]:
    """Repo-relative POSIX paths of non-test Python files."""
    out = []
    for p in sorted(repo.rglob("*.py")):
        rel = p.relative_to(repo)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        posix = rel.as_posix()
        if not is_test_file(posix):
            out.append(posix)
    return out


def _parse(repo: Path, rel: str) -> ast.Module | None:
    try:
        return ast.parse((repo / rel).read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None


def _module_candidates(module: str) -> list[str]:
    base = module.replace(".", "/")
    return [f"{base}.py", f"{base}/__init__.py", f"src/{base}.py", f"src/{base}/__init__.py"]


def _imports(repo: Path, rel: str, known: set[str]) -> set[str]:
    """Repo files imported by `rel` (absolute and relative imports)."""
    tree = _parse(repo, rel)
    if tree is None:
        return set()
    package = rel.rsplit("/", 1)[0].replace("/", ".") if "/" in rel else ""
    found: set[str] = set()
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".") if package else []
                parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join(p for p in [*parts, node.module or ""] if p)
            else:
                base = node.module or ""
            modules = [base] + [f"{base}.{a.name}" if base else a.name for a in node.names]
        for module in modules:
            for cand in _module_candidates(module):
                if cand in known and cand != rel:
                    found.add(cand)
                    break
    return found


def _imported_names(repo: Path, rel: str) -> set[str]:
    """Names pulled in with `from x import name` (used to find re-exported definitions)."""
    tree = _parse(repo, rel)
    if tree is None:
        return set()
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name != "*"
    }


def _definitions(repo: Path, files: list[str]) -> dict[str, set[str]]:
    """Function/class name -> files defining it."""
    defs: dict[str, set[str]] = defaultdict(set)
    for rel in files:
        tree = _parse(repo, rel)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defs[node.name].add(rel)
    return defs


def _resolve_frame(path: str, known: set[str]) -> str | None:
    path = path.replace("\\", "/")
    if path in known:
        return path
    for rel in known:  # absolute path inside the sandbox, e.g. /tmp/w/pkg/mod.py
        if path.endswith("/" + rel):
            return rel
    return None


def localize(report: PytestReport, repo: Path, top_k: int, max_chars: int) -> list[Suspect]:
    """Rank non-test source files by how strongly the failures implicate them."""
    files = source_files(repo)
    known = set(files)
    scores: dict[str, float] = defaultdict(float)
    reasons: dict[str, list[str]] = defaultdict(list)
    functions: dict[str, list[str]] = defaultdict(list)

    def add(rel: str, weight: float, reason: str) -> None:
        scores[rel] += weight
        if reason not in reasons[rel]:
            reasons[rel].append(reason)

    for failure in report.failures:
        frames = failure.frames
        for depth, frame in enumerate(frames):
            rel = _resolve_frame(frame.path, known)
            if rel:
                add(rel, _W_FRAME * (1 + depth / max(len(frames), 1)),
                    f"traceback frame {frame.func}() line {frame.line}")
                if frame.func not in functions[rel] and frame.func != "<module>":
                    functions[rel].append(frame.func)

    defs = _definitions(repo, files)
    for test_file in sorted({f.test_id.split("::", 1)[0] for f in report.failures}):
        if not (repo / test_file).is_file():
            continue
        for name in sorted(_imported_names(repo, test_file)):
            for rel in defs.get(name, ()):
                add(rel, _W_TEST_IMPORT, f"defines {name}, imported by failing test {test_file}")
                if name not in functions[rel]:
                    functions[rel].append(name)
        for rel in _imports(repo, test_file, known):
            weight = _W_PACKAGE if rel.endswith("__init__.py") else _W_TEST_IMPORT
            add(rel, weight, f"imported by failing test file {test_file}")

    text = "\n".join(f.message + "\n" + f.traceback for f in report.failures)
    for name in set(_IDENT_RE.findall(text)):
        for rel in defs.get(name, ()):
            add(rel, _W_NAME, f"defines {name}() referenced in the failure output")
            if name not in functions[rel]:
                functions[rel].append(name)

    for rel in [r for r in list(scores) if scores[r] > 0]:
        for dep in _imports(repo, rel, known):
            add(dep, _W_TRANSITIVE, f"imported by suspect {rel}")

    if not scores:  # no evidence (e.g. timeout with no traceback): show files in path order
        for rel in files:
            add(rel, 0.1, "no traceback evidence; included for context")

    ranked = sorted(scores, key=lambda r: (-scores[r], r))
    suspects: list[Suspect] = []
    for rel in ranked:
        content = (repo / rel).read_text(encoding="utf-8", errors="replace")
        if not content.strip():
            continue
        suspects.append(
            Suspect(
                path=rel,
                score=round(scores[rel], 2),
                reasons=reasons[rel],
                functions=functions[rel],
                content=content[:max_chars],
                truncated=len(content) > max_chars,
            )
        )
        if len(suspects) == top_k:
            break
    return suspects

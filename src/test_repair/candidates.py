"""Choose among parallel patch candidates.

Follows Agentless (Xia et al., 2024): candidates that are the same program after parsing
(formatting, comments, and docstrings ignored) are merged and counted as votes; the
remaining distinct patches are ranked by real test results, then by votes.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass

from .patcher import PreparedPatch
from .pytest_parser import PytestReport


@dataclass
class Proposal:
    """One Ultra candidate for an attempt (valid when `prepared` is set)."""

    index: int
    temperature: float
    prepared: PreparedPatch | None
    diagnosis: str
    diff: str
    error: str
    votes: int = 1
    report: PytestReport | None = None


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return tree


def semantic_key(prepared: PreparedPatch) -> tuple[tuple[str, str], ...]:
    """Identity of a patch's effect: hash of each patched file's normalized AST."""
    parts = []
    for path, text in sorted(prepared.new_contents.items()):
        try:
            norm = ast.dump(_strip_docstrings(ast.parse(text)))
        except SyntaxError:
            norm = text
        parts.append((path, hashlib.sha256(norm.encode("utf-8")).hexdigest()))
    return tuple(parts)


def dedupe(proposals: list[Proposal]) -> list[Proposal]:
    """Valid proposals with duplicates merged into the earliest one (votes summed)."""
    groups: dict[tuple[tuple[str, str], ...], Proposal] = {}
    for p in proposals:
        if p.prepared is None:
            continue
        key = semantic_key(p.prepared)
        if key in groups:
            groups[key].votes += 1
        else:
            groups[key] = p
    return list(groups.values())


def rank(proposals: list[Proposal]) -> list[Proposal]:
    """Best first: all green, most passing, fewest failing, most votes, smallest diff."""

    def key(p: Proposal) -> tuple:
        r = p.report
        assert r is not None and p.prepared is not None
        return (not r.all_passed, r.timed_out, -r.passed, r.bad, -p.votes,
                p.prepared.changed_lines, p.index)

    return sorted(proposals, key=key)

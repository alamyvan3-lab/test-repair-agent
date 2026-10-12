"""Spectrum-based fault localization (SBFL) with the Ochiai coefficient.

A small stdlib-only pytest plugin is injected into the sandbox copy. It records which source
lines each test executes (via `sys.monitoring` on Python 3.12+, `sys.settrace` before that)
and prints per-line counts of failing and passing tests that executed the line. Lines run
mostly by failing tests score high:

    ochiai(line) = ef / sqrt(total_failed * (ef + ep))

References: Abreu, Zoeteweij, van Gemund, "An Evaluation of Similarity Coefficients for
Software Fault Localization" (PRDC 2006) and "On the Accuracy of Spectrum-based Fault
Localization" (TAIC PART 2007); AutoCodeRover (Zhang et al., ISSTA 2024) uses SBFL to guide
an LLM repair agent.
"""

from __future__ import annotations

import ast
import json
import math
from dataclasses import dataclass
from pathlib import Path

from .patcher import is_test_file

PLUGIN_MODULE = "_tr_sbfl_plugin"
MARKER = "TR_SBFL "
PYTEST_ARGS = ["-q", "--tb=no", "-p", "no:cacheprovider", "-p", PLUGIN_MODULE, "--color=no"]

PLUGIN_SOURCE = '''\
"""Injected by test-repair: per-line pass/fail spectra for SBFL. Stdlib only."""
import json
import os
import sys

_ROOT = os.path.realpath(os.getcwd()) + os.sep
_SELF = os.path.realpath(__file__)
_MON = getattr(sys, "monitoring", None)
_tool = None
_cache = {}
_hits = set()
_counts = {}
_failed = set()
_skipped = set()
_totals = [0, 0]


def _rel(filename):
    rel = _cache.get(filename)
    if rel is None:
        path = os.path.realpath(filename)
        rel = path[len(_ROOT):].replace(os.sep, "/") if path.startswith(_ROOT) and path != _SELF else ""
        _cache[filename] = rel
    return rel


def _on_line(code, line):
    rel = _rel(code.co_filename)
    if rel:
        _hits.add((rel, line))
    return _MON.DISABLE


def _local(frame, event, arg):
    if event == "line":
        _hits.add((_rel(frame.f_code.co_filename), frame.f_lineno))
    return _local


def _trace(frame, event, arg):
    return _local if _rel(frame.f_code.co_filename) else None


def pytest_configure(config):
    global _tool
    if _MON is not None:
        try:
            _MON.use_tool_id(_MON.COVERAGE_ID, "test-repair-sbfl")
            _tool = _MON.COVERAGE_ID
            _MON.register_callback(_tool, _MON.events.LINE, _on_line)
            _MON.set_events(_tool, _MON.events.LINE)
        except ValueError:
            _tool = None


def pytest_runtest_logstart(nodeid, location):
    _hits.clear()
    if _tool is not None:
        _MON.restart_events()
    else:
        sys.settrace(_trace)


def pytest_runtest_logreport(report):
    if report.failed:
        _failed.add(report.nodeid)
    elif report.skipped:
        _skipped.add(report.nodeid)


def pytest_runtest_logfinish(nodeid, location):
    if _tool is None:
        sys.settrace(None)
    failed = nodeid in _failed
    if failed or nodeid not in _skipped:
        _totals[0 if failed else 1] += 1
        for key in _hits:
            _counts.setdefault(key, [0, 0])[0 if failed else 1] += 1
    _hits.clear()


def pytest_terminal_summary(terminalreporter):
    if _tool is not None:
        _MON.set_events(_tool, 0)
    lines = [[p, n, f, s] for (p, n), (f, s) in sorted(_counts.items())]
    payload = {"failed": _totals[0], "passed": _totals[1], "lines": lines}
    terminalreporter.write_line("TR_SBFL " + json.dumps(payload, separators=(",", ":")))
'''


@dataclass(frozen=True)
class Suspicious:
    """A source line ranked by SBFL."""

    path: str
    line: int
    score: float
    failed_hits: int
    passed_hits: int
    function: str
    source: str


def ochiai(failed_hits: int, passed_hits: int, total_failed: int) -> float:
    denom = math.sqrt(total_failed * (failed_hits + passed_hits))
    return failed_hits / denom if denom else 0.0


def parse_spectra(output: str) -> dict | None:
    """The plugin's JSON payload from pytest output, or None if absent/garbled."""
    for line in reversed(output.splitlines()):
        idx = line.find(MARKER)
        if idx != -1:
            try:
                payload = json.loads(line[idx + len(MARKER):])
            except json.JSONDecodeError:
                return None
            return payload if isinstance(payload, dict) else None
    return None


def _functions(source: str) -> list[tuple[int, int, str]]:
    """(start, end, qualified name) for every function, innermost resolvable by span."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    spans: list[tuple[int, int, str]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                if not isinstance(child, ast.ClassDef):
                    spans.append((child.lineno, child.end_lineno or child.lineno, name))
                visit(child, name + ".")
            else:
                visit(child, prefix)

    visit(tree, "")
    return spans


def rank(output: str, repo: Path, top: int = 10) -> list[Suspicious]:
    """Top suspicious non-test source lines, highest Ochiai score first."""
    payload = parse_spectra(output)
    if not payload or not payload.get("failed"):
        return []
    total_failed = int(payload["failed"])
    sources: dict[str, list[str]] = {}
    spans: dict[str, list[tuple[int, int, str]]] = {}
    ranked: list[Suspicious] = []
    for path, line, ef, ep in payload.get("lines", []):
        if is_test_file(path) or not (repo / path).is_file():
            continue
        score = ochiai(ef, ep, total_failed)
        if score <= 0:
            continue
        if path not in sources:
            text = (repo / path).read_text(encoding="utf-8", errors="replace")
            sources[path] = text.splitlines()
            spans[path] = _functions(text)
        inner = [s for s in spans[path] if s[0] <= line <= s[1]]
        function = min(inner, key=lambda s: s[1] - s[0])[2] if inner else "<module>"
        text_line = sources[path][line - 1].strip() if 0 < line <= len(sources[path]) else ""
        ranked.append(Suspicious(path, line, round(score, 3), ef, ep, function, text_line[:160]))
    ranked.sort(key=lambda s: (-s.score, -s.failed_hits, s.path, s.line))
    return ranked[:top]

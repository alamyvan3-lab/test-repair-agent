"""SBFL, isolation rerun, parallel candidates with voting, and patch minimization."""

import dataclasses
from pathlib import Path

import pytest
from helpers import BENCH, EXAMPLES, FakeLLM, reference_fix, ultra_answer

from test_repair import sbfl
from test_repair.agent import RepairAgent
from test_repair.candidates import Proposal, dedupe, semantic_key
from test_repair.config import Settings
from test_repair.events import Event
from test_repair.minimize import Units, minimize
from test_repair.patcher import PreparedPatch
from test_repair.sandbox import LocalSandbox

DEMO = EXAMPLES / "demo_invoice"
NO_EFFECT = """\
--- a/invoice/coupons.py
+++ b/invoice/coupons.py
@@ -13,3 +13,3 @@
     if kind == "percent":
         return round(amount * value / 100, 2)
-    return value
+    return float(value)
"""


def run(settings: Settings, llm: FakeLLM, repo: Path, tmp_path: Path):
    events: list[Event] = []
    agent = RepairAgent(settings, llm, LocalSandbox(timeout_s=60), on_event=events.append)
    return agent.run(repo, tmp_path / "out"), events


def of_type(events: list[Event], kind: str) -> list[Event]:
    return [e for e in events if e.type == kind]


# -- SBFL -------------------------------------------------------------------------


def test_ochiai_formula():
    assert sbfl.ochiai(3, 0, 3) == 1.0
    assert sbfl.ochiai(0, 5, 3) == 0.0
    assert sbfl.ochiai(2, 2, 4) == pytest.approx(0.5)


def run_spectra(repo: Path, plugin: str = sbfl.PLUGIN_SOURCE) -> list[sbfl.Suspicious]:
    res = LocalSandbox(60).run_pytest(repo, sbfl.PYTEST_ARGS, {f"{sbfl.PLUGIN_MODULE}.py": plugin})
    return sbfl.rank(res.output, repo)


@pytest.mark.parametrize("tracer", ["monitoring", "settrace"])
def test_sbfl_puts_the_faulty_function_first(tracer: str):
    plugin = sbfl.PLUGIN_SOURCE
    if tracer == "settrace":  # the Python < 3.12 path
        plugin = plugin.replace('_MON = getattr(sys, "monitoring", None)', "_MON = None")
    ranked = run_spectra(BENCH / "repos" / "pager_offbyone", plugin)
    assert ranked, "no spectrum produced"
    top = ranked[0]
    assert (top.path, top.function) == ("pager/core.py", "page")
    assert top.source in {"start = number * size", "return items[start:start + size]"}
    assert all(not s.path.startswith("tests/") for s in ranked)


def test_sbfl_without_marker_is_empty(tmp_path: Path):
    assert sbfl.rank("3 failed, 1 passed in 0.1s", tmp_path) == []


def test_agent_feeds_sbfl_to_ultra(settings: Settings, tmp_path: Path):
    s = dataclasses.replace(settings, sbfl=True)
    llm = FakeLLM([ultra_answer(reference_fix("pager_offbyone"))])
    result, events = run(s, llm, BENCH / "repos" / "pager_offbyone", tmp_path)
    assert result.status == "SUCCESS"
    (event,) = of_type(events, "sbfl")
    assert "pager/core.py" in event.message and event.data["top"]
    prompt = next(m for role, m in llm.calls if role == "ultra")[1]["content"]
    assert "<sbfl>" in prompt and "in page() score" in prompt


# -- isolation rerun ------------------------------------------------------------------


def shared_state_repo(root: Path) -> Path:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "registry.py").write_text(
        "_items = []\n\n\ndef register(x):\n    _items.append(x)\n    return len(_items)\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_registry.py").write_text(
        "from pkg.registry import register\n\n\n"
        "def test_first():\n    assert register('a') == 1\n\n\n"
        "def test_second():\n    assert register('b') == 1\n")
    return root


def test_isolation_rerun_flags_shared_state(settings: Settings, tmp_path: Path):
    s = dataclasses.replace(settings, isolation_rerun=True, max_attempts=1, max_corrections=0)
    llm = FakeLLM(["DIAGNOSIS: unsure"])
    _, events = run(s, llm, shared_state_repo(tmp_path / "repo"), tmp_path)
    (event,) = of_type(events, "isolation_check")
    assert event.data["pass_alone"] == ["tests/test_registry.py::test_second"]
    prompt = next(m for role, m in llm.calls if role == "ultra")[1]["content"]
    assert "PASS when rerun without the rest of the suite" in prompt


def test_isolation_rerun_on_a_deterministic_failure(settings: Settings, tmp_path: Path):
    s = dataclasses.replace(settings, isolation_rerun=True)
    llm = FakeLLM([ultra_answer(reference_fix("demo_invoice"))])
    result, events = run(s, llm, DEMO, tmp_path)
    assert result.status == "SUCCESS"
    (event,) = of_type(events, "isolation_check")
    assert event.data["pass_alone"] == []
    triage_input = next(m for role, m in llm.calls if role == "nano")[1]["content"]
    assert ">       assert total(" in triage_input  # --tb=long output reached Nano triage


# -- parallel candidates -----------------------------------------------------------------


def test_candidates_are_voted_and_ranked_by_tests(settings: Settings, tmp_path: Path):
    s = dataclasses.replace(settings, candidates=3)
    fix = ultra_answer(reference_fix("demo_invoice"))
    llm = FakeLLM([ultra_answer(NO_EFFECT), fix, fix])
    result, events = run(s, llm, DEMO, tmp_path)

    assert result.status == "SUCCESS" and result.attempts == 1
    assert result.usage["ultra"].calls == 3
    temps = sorted(k["temperature"] for k in llm.kwargs if k["role"] == "ultra")
    assert temps == [0.0, 0.1, 0.2]
    (ranking,) = of_type(events, "candidates_ranked")
    assert ranking.data["total"] == 3 and len(ranking.data["ranking"]) == 2
    best = ranking.data["ranking"][0]
    assert best["all_passed"] and best["votes"] == 2
    assert len([e for e in of_type(events, "tests_run") if e.data["candidate"] is not None]) == 2
    assert "min(value, amount)" in result.patch


def test_identical_candidates_collapse_to_one(settings: Settings, tmp_path: Path):
    s = dataclasses.replace(settings, candidates=2)
    llm = FakeLLM([ultra_answer(reference_fix("demo_invoice"))])
    result, events = run(s, llm, DEMO, tmp_path)
    (ranking,) = of_type(events, "candidates_ranked")
    assert len(ranking.data["ranking"]) == 1 and ranking.data["ranking"][0]["votes"] == 2
    assert result.status == "SUCCESS"


def test_semantic_key_ignores_formatting_comments_and_docstrings():
    a = PreparedPatch("", ["m.py"], 1, {"m.py": 'def f(x):\n    """Doc."""\n    return x+1\n'})
    b = PreparedPatch("", ["m.py"], 1, {"m.py": "def f(x):  # note\n    return (x + 1)\n"})
    c = PreparedPatch("", ["m.py"], 1, {"m.py": "def f(x):\n    return x + 2\n"})
    assert semantic_key(a) == semantic_key(b) != semantic_key(c)
    merged = dedupe([Proposal(0, 0.0, a, "", "", ""), Proposal(1, 0.1, b, "", "", ""),
                     Proposal(2, 0.2, c, "", "", ""), Proposal(3, 0.2, None, "", "", "bad")])
    assert [(p.index, p.votes) for p in merged] == [(0, 2), (2, 1)]


# -- minimization ------------------------------------------------------------------------


def test_minimize_finds_the_needed_units():
    before = {"a.py": "x = 1\ny = 2\nz = 3\n"}
    after = {"a.py": "x = 10\ny = 2\nz = 30\n"}
    units = Units.of(before, after)
    assert units.count == 2
    needs_x = lambda files: "x = 10" in files["a.py"]
    removed, runs = minimize(units, needs_x, max_runs=10)
    assert removed == {1} and runs >= 2
    assert units.build(removed)["a.py"] == "x = 10\ny = 2\nz = 3\n"


def test_minimize_respects_run_budget():
    units = Units.of({"a.py": "a\nb\nc\nd\n"}, {"a.py": "A\nb\nC\nd\n"})
    removed, runs = minimize(units, lambda _files: True, max_runs=1)
    assert runs == 1 and len(removed) == 1


def test_agent_drops_unneeded_hunks(settings: Settings, tmp_path: Path):
    extra = """\
--- a/invoice/totals.py
+++ b/invoice/totals.py
@@ -14,2 +14,3 @@
 def subtotal(lines: list[Line]) -> float:
+    # sum of unit price times quantity
     return round(sum(line.unit_price * line.quantity for line in lines), 2)
"""
    s = dataclasses.replace(settings, minimize_patch=True)
    llm = FakeLLM([ultra_answer(reference_fix("demo_invoice") + extra)])
    result, events = run(s, llm, DEMO, tmp_path)
    assert result.status == "SUCCESS"
    (event,) = of_type(events, "patch_minimized")
    assert (event.data["regions"], event.data["removed"]) == (2, 1)
    assert "min(value, amount)" in result.patch
    assert "sum of unit price" not in result.patch


def test_all_techniques_together(settings: Settings, tmp_path: Path):
    s = dataclasses.replace(settings, candidates=2, sbfl=True, isolation_rerun=True,
                            minimize_patch=True)
    llm = FakeLLM([ultra_answer(reference_fix("checkout_second_file"))])
    result, events = run(s, llm, BENCH / "repos" / "checkout_second_file", tmp_path)
    assert result.status == "SUCCESS"
    kinds = {e.type for e in events}
    assert {"isolation_check", "sbfl", "candidates_ranked"} <= kinds

"""Agent loop with a scripted fake LLM and the local sandbox (no network, no Docker)."""

import dataclasses
import json
from pathlib import Path

from helpers import (
    BENCH,
    EXAMPLES,
    FAKE_KEY,
    SANITY_SPECIAL,
    FakeLLM,
    default_nano,
    reference_fix,
    ultra_answer,
)

from test_repair.agent import RepairAgent, compare
from test_repair.config import Settings
from test_repair.events import Event
from test_repair.pytest_parser import PytestReport
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

BREAKING = """\
--- a/invoice/totals.py
+++ b/invoice/totals.py
@@ -14,2 +14,2 @@
 def subtotal(lines: list[Line]) -> float:
-    return round(sum(line.unit_price * line.quantity for line in lines), 2)
+    return 0.0
"""


def run(settings: Settings, llm: FakeLLM, repo: Path, tmp_path: Path, mode="agent"):
    events: list[Event] = []
    agent = RepairAgent(settings, llm, LocalSandbox(timeout_s=60), on_event=events.append)
    result = agent.run(repo, tmp_path / "out", mode=mode)
    return result, events


def types(events: list[Event]) -> list[str]:
    return [e.type for e in events]


def test_red_to_green_with_artifacts(settings: Settings, tmp_path: Path):
    llm = FakeLLM([ultra_answer(reference_fix("demo_invoice"))])
    result, events = run(settings, llm, DEMO, tmp_path)

    assert result.status == "SUCCESS", result.message
    assert result.attempts == 1
    assert result.initial.failed == 2 and result.final.failed == 0
    assert types(events) == ["run_started", "tests_run", "triage", "diagnosis", "patch_proposed",
                             "sanity_check", "patch_applied", "tests_run", "success"]
    assert llm.roles() == ["nano", "ultra", "nano"]  # triage, diagnose+patch, sanity
    out = tmp_path / "out"
    assert "min(value, amount)" in (out / "repair.patch").read_text(encoding="utf-8")
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "SUCCESS" in report and "| ultra | `fake/ultra` | 1 |" in report
    lines = (out / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["type"] for line in lines] == types(events)
    # the user's directory is never modified
    assert "return value\n" in (DEMO / "invoice" / "coupons.py").read_text(encoding="utf-8")


def test_llm_events_carry_role_model_tokens_latency(settings: Settings, tmp_path: Path):
    llm = FakeLLM([ultra_answer(reference_fix("demo_invoice"))], tokens=(321, 45))
    _, events = run(settings, llm, DEMO, tmp_path)
    for e in events:
        if e.type in {"triage", "diagnosis", "sanity_check"}:
            assert e.role in {"ultra", "nano"} and e.model.startswith("fake/")
            assert (e.prompt_tokens, e.completion_tokens) == (321, 45)
            assert e.latency_s is not None
    assert next(e for e in events if e.type == "diagnosis").role == "ultra"
    assert next(e for e in events if e.type == "triage").role == "nano"


def test_invalid_diff_is_corrected_without_a_test_run(settings: Settings, tmp_path: Path):
    llm = FakeLLM(["I think the coupon is wrong.", ultra_answer(reference_fix("demo_invoice"))])
    result, events = run(settings, llm, DEMO, tmp_path)
    assert result.status == "SUCCESS" and result.attempts == 1
    assert types(events).count("tests_run") == 2  # baseline + after the corrected patch
    rejected = next(e for e in events if e.type == "patch_rejected")
    assert "No ```diff fenced block" in rejected.message
    second_ultra = [m for role, m in llm.calls if role == "ultra"][1]
    assert "was rejected and NOT applied" in second_ultra[-1]["content"]


def test_corrections_are_capped_then_attempt_fails(settings: Settings, tmp_path: Path):
    s = dataclasses.replace(settings, max_attempts=2, max_corrections=2)
    llm = FakeLLM(["no diff here"])
    result, events = run(s, llm, DEMO, tmp_path)
    assert result.status == "EXHAUSTED"
    assert types(events).count("diagnosis") == 6  # 2 attempts x (1 + 2 corrections)
    assert types(events).count("tests_run") == 1


def test_regression_is_reverted_and_reported(settings: Settings, tmp_path: Path):
    llm = FakeLLM([ultra_answer(BREAKING), ultra_answer(reference_fix("demo_invoice"))])
    result, events = run(settings, llm, DEMO, tmp_path)
    assert result.status == "SUCCESS" and result.attempts == 2
    assert "regression" in types(events) and "patch_reverted" in types(events)
    assert "return 0.0" not in result.patch
    second_prompt = [m for role, m in llm.calls if role == "ultra"][1][1]["content"]
    assert "regressed; patch reverted" in second_prompt


def test_triage_after_revert_sees_restored_state(settings: Settings, tmp_path: Path):
    llm = FakeLLM([ultra_answer(BREAKING), ultra_answer(reference_fix("demo_invoice"))])
    run(settings, llm, DEMO, tmp_path)
    triage_inputs = [m[1]["content"] for role, m in llm.calls
                     if role == "nano" and "You triage" in m[0]["content"]]
    assert len(triage_inputs) == 2
    assert "5 passed" in triage_inputs[1]  # baseline state, not the regressed "1 passed"


def test_no_effect_patch_is_reverted(settings: Settings, tmp_path: Path):
    llm = FakeLLM([ultra_answer(NO_EFFECT), ultra_answer(reference_fix("demo_invoice"))])
    result, events = run(settings, llm, DEMO, tmp_path)
    assert result.status == "SUCCESS"
    assert "float(value)" not in result.patch
    reverted = next(e for e in events if e.type == "patch_reverted")
    assert reverted.data["reason"] == "no effect"


def test_exhausted_after_max_attempts(settings: Settings, tmp_path: Path):
    s = dataclasses.replace(settings, max_attempts=2)
    result, events = run(s, FakeLLM([ultra_answer(NO_EFFECT)]), DEMO, tmp_path)
    assert result.status == "EXHAUSTED" and result.attempts == 2
    assert events[-1].type == "failure" and events[-1].data["status"] == "EXHAUSTED"


def test_token_budget_aborts(settings: Settings, tmp_path: Path):
    s = dataclasses.replace(settings, max_total_tokens=150)
    result, events = run(s, FakeLLM([ultra_answer(NO_EFFECT)], tokens=(100, 20)), DEMO, tmp_path)
    assert result.status == "ABORTED_GUARDRAIL"
    assert "Token budget exceeded" in result.message
    assert events[-1].type == "failure"


def test_sanity_check_rejection_requests_new_patch(settings: Settings, tmp_path: Path):
    verdicts = iter([SANITY_SPECIAL])

    def nano(messages):
        if "You triage" in messages[0]["content"]:
            return default_nano(messages)
        return next(verdicts, default_nano(messages))

    llm = FakeLLM([ultra_answer(NO_EFFECT), ultra_answer(reference_fix("demo_invoice"))], nano=nano)
    result, events = run(settings, llm, DEMO, tmp_path)
    assert result.status == "SUCCESS" and result.attempts == 1
    rejected = [e for e in events if e.type == "patch_rejected"]
    assert rejected and rejected[0].data["stage"] == "sanity"


def test_single_shot_baseline(settings: Settings, tmp_path: Path):
    llm = FakeLLM([ultra_answer(NO_EFFECT)])
    result, events = run(settings, llm, DEMO, tmp_path, mode="single_shot")
    assert result.status == "EXHAUSTED" and result.attempts == 1
    assert llm.roles() == ["ultra"]  # no triage, no sanity, no corrections
    assert "triage" not in types(events)


def test_already_green_repo(settings: Settings, tmp_path: Path):
    repo = tmp_path / "green"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    result, _ = run(settings, FakeLLM(["unused"]), repo, tmp_path)
    assert result.status == "SUCCESS" and result.attempts == 0


def test_bench_repo_with_two_function_bug(settings: Settings, tmp_path: Path):
    repo = BENCH / "repos" / "durations_two_functions"
    result, _ = run(settings, FakeLLM([ultra_answer(reference_fix(repo.name))]), repo, tmp_path)
    assert result.status == "SUCCESS"


def test_api_key_never_appears_in_events_or_artifacts(settings: Settings, tmp_path: Path):
    leaky = ultra_answer(reference_fix("demo_invoice"), diagnosis=f"key is {FAKE_KEY} Bearer abc123")
    _result, events = run(settings, FakeLLM([leaky]), DEMO, tmp_path)
    blob = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "out").iterdir())
    blob += "".join(e.model_dump_json() for e in events)
    assert FAKE_KEY not in blob
    assert "Bearer abc123" not in blob
    assert "[REDACTED]" in blob


def test_compare_verdicts():
    def r(passed, failed, errors=0, timed_out=False):
        return PytestReport(exit_code=1, passed=passed, failed=failed, errors=errors,
                            timed_out=timed_out)

    assert compare(r(3, 2), r(4, 1)) == "improved"
    assert compare(r(3, 2), r(2, 3)) == "regressed"
    assert compare(r(0, 0, 1), r(5, 1)) == "improved"  # collection error fixed
    assert compare(r(3, 2), r(3, 2)) == "same"
    assert compare(r(3, 2), r(0, 0, timed_out=True)) == "regressed"

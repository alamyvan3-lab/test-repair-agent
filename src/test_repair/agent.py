"""The repair loop.

Per run: test; rerun failing tests on their own; SBFL over per-test coverage. Per attempt:
Nano triage; N parallel Ultra diagnoses + diffs; validation with corrections; Nano sanity
check; dedupe and rank candidates by real test runs; apply, retest, keep or revert. After a
fix: shrink the patch to what the tests need.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import patcher, prompts, sbfl
from .candidates import Proposal, dedupe, rank
from .config import RoleModel, Settings
from .events import Event, EventLog, Status, jsonl_sink
from .llm import ChatModel, LLMError, LLMResult, Role
from .localize import localize
from .minimize import Units, minimize
from .patcher import PatchError
from .pytest_parser import PytestReport, compact_failures, parse_pytest_output
from .report import render_report
from .sandbox import ExecResult, Sandbox, SandboxError
from .workspace import Workspace, WorkspaceError, remove_tree

Mode = Literal["agent", "single_shot"]
Verdict = Literal["improved", "same", "regressed"]

# Failing tests only, with long tracebacks and local variables (pytest --showlocals).
ISOLATION_ARGS = ["-q", "--tb=long", "-l", "-p", "no:cacheprovider", "-rfE", "--color=no"]

log = logging.getLogger("test_repair.agent")


class BudgetExceeded(RuntimeError):
    """Token or wall-clock budget for the run was exhausted."""


class RoleUsage(BaseModel):
    """Accumulated model usage for one role."""

    model: str = ""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def cost_usd(self, cfg: RoleModel) -> float | None:
        if cfg.price_in_per_m is None or cfg.price_out_per_m is None:
            return None
        return (self.prompt_tokens * cfg.price_in_per_m
                + self.completion_tokens * cfg.price_out_per_m) / 1_000_000


class SuiteCounts(BaseModel):
    passed: int = 0
    failed: int = 0
    errors: int = 0
    timed_out: bool = False

    @classmethod
    def of(cls, r: PytestReport) -> SuiteCounts:
        return cls(passed=r.passed, failed=r.failed, errors=r.errors, timed_out=r.timed_out)


class RunResult(BaseModel):
    """Final outcome of one run; also the payload of the terminal event."""

    repo: str
    mode: Mode
    status: Status
    message: str
    attempts: int = 0
    diagnosis: str = ""
    patch: str = ""
    initial: SuiteCounts = Field(default_factory=SuiteCounts)
    final: SuiteCounts = Field(default_factory=SuiteCounts)
    usage: dict[str, RoleUsage] = Field(default_factory=dict)
    wall_time_s: float = 0.0
    test_runs: int = 0
    sandbox: str = ""
    triage_input_chars: int = 0  # raw pytest output Nano read
    triage_summary_chars: int = 0  # what Ultra received from Nano instead


def compare(before: PytestReport, after: PytestReport) -> Verdict:
    """Did a patch improve, keep, or regress the suite?"""
    if after.timed_out and not before.timed_out:
        return "regressed"
    if after.passed != before.passed:
        return "improved" if after.passed > before.passed else "regressed"
    if after.bad != before.bad:
        return "improved" if after.bad < before.bad else "regressed"
    return "same"


def parse_json_object(text: str) -> dict | None:
    """First-to-last brace JSON object in a model answer, or None."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def parse_diagnosis(text: str) -> str:
    m = re.search(r"DIAGNOSIS:\s*(.*?)(?=```|\Z)", text, re.DOTALL | re.IGNORECASE)
    raw = m.group(1) if m else text.split("```", 1)[0]
    return " ".join(raw.split())[:1000]


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.3)
    return f"{text[:head]}\n... [clipped {len(text) - limit} chars] ...\n{text[-(limit - head):]}"


def _llm_fields(res: LLMResult) -> dict:
    return {
        "role": res.role,
        "model": res.model,
        "prompt_tokens": res.prompt_tokens,
        "completion_tokens": res.completion_tokens,
        "latency_s": round(res.latency_s, 3),
    }


class RepairAgent:
    """Runs one repair session against a repository directory."""

    def __init__(
        self,
        settings: Settings,
        llm: ChatModel,
        sandbox: Sandbox,
        on_event: Callable[[Event], None] | None = None,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.sandbox = sandbox
        self.on_event = on_event
        self.log = EventLog()
        self._usage: dict[str, RoleUsage] = {}
        self._usage_lock = threading.Lock()
        self._t0 = 0.0
        self._test_runs = 0
        self._last_output = ""
        self._tmp = Path()
        self._result = RunResult(repo="", mode="agent", status="ERROR", message="")

    # -- public ---------------------------------------------------------------

    def run(self, repo: Path, out_dir: Path | None = None, mode: Mode = "agent") -> RunResult:
        """Repair `repo` (never modified) and write artifacts to `out_dir` if given."""
        s = self.settings
        sinks: list[Callable[[Event], None]] = []
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            sinks.append(jsonl_sink(out_dir / "events.jsonl"))
        if self.on_event is not None:
            sinks.append(self.on_event)
        self.log = EventLog(secrets=[s.api_key], sinks=sinks)
        self._usage = {
            "ultra": RoleUsage(model=s.ultra.model),
            "nano": RoleUsage(model=s.nano.model),
        }
        self._t0 = time.monotonic()
        self._test_runs = 0
        result = RunResult(repo=repo.name, mode=mode, status="ERROR", message="",
                           sandbox=self.sandbox.name)
        self._result = result

        single = mode == "single_shot"
        self.log.emit(
            "run_started",
            f"Repairing {repo.name} ({mode.replace('_', '-')} mode, sandbox: {self.sandbox.name})",
            data={
                "repo": repo.name,
                "mode": mode,
                "max_attempts": 1 if single else s.max_attempts,
                "models": {"ultra": s.ultra.model, "nano": s.nano.model},
                "sandbox": self.sandbox.name,
                "techniques": {
                    "candidates": 1 if single else s.candidates,
                    "sbfl": s.sbfl and not single,
                    "isolation_rerun": s.isolation_rerun and not single,
                    "minimize_patch": s.minimize_patch and not single,
                    "sanity_check": s.sanity_check and not single,
                },
            },
        )
        tmp = Path(tempfile.mkdtemp(prefix="test-repair-"))
        self._tmp = tmp
        workspace: Workspace | None = None
        try:
            workspace = Workspace(repo, tmp)
            workspace.init()
            result.status, result.message = self._loop(workspace, result, mode)
        except BudgetExceeded as exc:
            result.status, result.message = "ABORTED_GUARDRAIL", str(exc)
        except (LLMError, SandboxError, WorkspaceError) as exc:
            result.status, result.message = "ERROR", str(exc)
        except Exception as exc:  # report, never crash the caller (CLI, server, bench)
            log.exception("unexpected error during run")
            result.status, result.message = "ERROR", f"Unexpected {type(exc).__name__}: {exc}"
        finally:
            if workspace is not None and workspace.baseline:
                try:
                    result.patch = workspace.diff()
                except WorkspaceError:
                    result.patch = ""
            remove_tree(tmp)

        result.usage = self._usage
        result.wall_time_s = round(time.monotonic() - self._t0, 3)
        result.test_runs = self._test_runs
        result = RunResult.model_validate(self.log.redact(result.model_dump()))
        self.log.emit(
            "success" if result.status == "SUCCESS" else "failure",
            f"{result.status}: {result.message}",
            data=result.model_dump(),
        )
        if out_dir is not None:
            (out_dir / "repair.patch").write_text(result.patch, encoding="utf-8", newline="\n")
            (out_dir / "report.md").write_text(
                render_report(result, self.log.events, s), encoding="utf-8", newline="\n"
            )
        return result

    # -- loop -----------------------------------------------------------------

    def _loop(self, ws: Workspace, result: RunResult, mode: Mode) -> tuple[Status, str]:
        s = self.settings
        report = self._run_tests(ws, attempt=0)
        result.initial = result.final = SuiteCounts.of(report)
        if report.all_passed:
            return "SUCCESS", "All tests already pass; nothing to repair."
        if not report.timed_out and report.exit_code in (3, 4, 5):
            return "ERROR", (
                f"pytest could not run the suite (exit code {report.exit_code}): "
                f"{report.summary or 'see tests_run output'}"
            )

        single = mode == "single_shot"
        pass_alone = self._isolation_check(ws, report) if s.isolation_rerun and not single else []
        spectra = self._sbfl(ws, report, attempt=0) if s.sbfl and not single else []
        max_attempts = 1 if single else s.max_attempts
        history: list[prompts.AttemptRecord] = []
        for attempt in range(1, max_attempts + 1):
            result.attempts = attempt
            failures = compact_failures(report)
            triage = None if single else self._triage(attempt)
            suspects = localize(report, ws.path, s.top_k_files, s.max_file_chars, spectra)
            messages = prompts.repair_messages(
                repo=ws.source.name, attempt=attempt, max_attempts=max_attempts,
                max_lines=s.max_diff_lines, failures=failures, triage=triage,
                suspects=suspects, history=history, spectra=spectra, pass_alone=pass_alone,
            )
            proposals = self._propose_all(
                messages, ws, attempt, failures,
                count=1 if single else s.candidates,
                corrections=0 if single else s.max_corrections,
                sanity=s.sanity_check and not single,
            )
            distinct = dedupe(proposals)
            if not distinct:
                diagnosis = next((p.diagnosis for p in proposals if p.diagnosis), "")
                result.diagnosis = diagnosis or result.diagnosis
                errors = "; ".join(p.error for p in proposals if p.error)
                history.append(prompts.AttemptRecord(
                    attempt, diagnosis, proposals[0].diff, "rejected", errors))
                continue

            kept_output = self._last_output
            chosen, new = self._choose(ws, distinct, attempt, len(proposals))
            result.diagnosis = chosen.diagnosis or result.diagnosis
            assert chosen.prepared is not None
            if new.all_passed:
                ws.commit(f"attempt {attempt}")
                result.final = SuiteCounts.of(new)
                if s.minimize_patch and not single:
                    self._minimize(ws, attempt)
                return "SUCCESS", f"All tests pass after {attempt} attempt(s)."

            verdict = compare(report, new)
            detail = compact_failures(new)
            if verdict == "regressed" or new.signature() == report.signature():
                if verdict == "regressed":
                    self.log.emit(
                        "regression",
                        f"Patch made things worse: {report.passed} -> {new.passed} passed, "
                        f"{report.bad} -> {new.bad} failing",
                        attempt=attempt,
                        data={"before": SuiteCounts.of(report).model_dump(),
                              "after": SuiteCounts.of(new).model_dump()},
                    )
                outcome = "regressed" if verdict == "regressed" else "no effect"
                ws.revert()
                self._last_output = kept_output  # triage must see the restored state
                self.log.emit("patch_reverted", f"Reverted attempt {attempt} ({outcome})",
                              attempt=attempt, data={"reason": outcome})
                history.append(prompts.AttemptRecord(
                    attempt, chosen.diagnosis, chosen.prepared.diff,
                    f"{outcome}; patch reverted", detail))
                continue

            ws.commit(f"attempt {attempt}")
            kept = "improved" if verdict == "improved" else "failures changed"
            history.append(prompts.AttemptRecord(
                attempt, chosen.diagnosis, chosen.prepared.diff, f"{kept}; patch kept", detail))
            report = new
            result.final = SuiteCounts.of(new)
            if s.sbfl and not single:
                spectra = self._sbfl(ws, report, attempt)

        return "EXHAUSTED", f"Tests still failing after {max_attempts} attempt(s)."

    # -- tests ----------------------------------------------------------------

    def _run_tests(self, ws: Workspace, attempt: int) -> PytestReport:
        self._check_budget()
        res = self.sandbox.run_pytest(ws.path)
        self._test_runs += 1
        self._last_output = res.output
        return self._record_tests(res, attempt)

    def _record_tests(self, res: ExecResult, attempt: int,
                      candidate: int | None = None) -> PytestReport:
        report = parse_pytest_output(res.output, res.exit_code, res.timed_out)
        if res.timed_out:
            message = f"Tests timed out after {self.settings.test_timeout_s}s"
        else:
            message = (f"Tests: {report.passed} passed, {report.failed} failed, "
                       f"{report.errors} errors ({res.duration_s:.2f}s)")
        if candidate is not None:
            message = f"Candidate #{candidate}: {message}"
        self.log.emit(
            "tests_run",
            message,
            attempt=attempt,
            data={
                "all_passed": report.all_passed,
                "passed": report.passed,
                "failed": report.failed,
                "errors": report.errors,
                "exit_code": res.exit_code,
                "timed_out": res.timed_out,
                "duration_s": res.duration_s,
                "failing": report.failing_ids()[:20],
                "summary": report.summary,
                "output_tail": res.output[-3000:],
                "truncated": res.truncated,
                "candidate": candidate,
            },
        )
        return report

    def _isolation_check(self, ws: Workspace, report: PytestReport) -> list[str]:
        """Rerun only the failing tests (long tracebacks + locals). Tests that now pass hint
        at shared state between tests; the richer output also feeds the first triage."""
        ids = [f.test_id for f in report.failures if "::" in f.test_id][:20]
        if not ids or report.timed_out:
            return []
        self._check_budget()
        res = self.sandbox.run_pytest(ws.path, [*ISOLATION_ARGS, *ids])
        self._test_runs += 1
        iso = parse_pytest_output(res.output, res.exit_code, res.timed_out)
        alone: list[str] = []
        if not res.timed_out and iso.passed + iso.bad > 0:
            still = set(iso.failing_ids())
            alone = [t for t in ids if t not in still]
        if iso.failures:
            self._last_output = res.output
        message = (f"{len(alone)} of {len(ids)} failing tests pass without the rest of the "
                   "suite: likely shared state between tests" if alone else
                   f"All {len(ids)} failing tests also fail when rerun on their own")
        self.log.emit("isolation_check", message, attempt=0, data={
            "rerun": ids, "pass_alone": alone, "duration_s": res.duration_s,
            "output_tail": res.output[-3000:],
        })
        return alone

    def _sbfl(self, ws: Workspace, report: PytestReport, attempt: int) -> list[sbfl.Suspicious]:
        """Spectrum-based fault localization over per-test line coverage."""
        if report.failed == 0 or report.timed_out:
            return []
        self._check_budget()
        res = self.sandbox.run_pytest(ws.path, sbfl.PYTEST_ARGS,
                                      {f"{sbfl.PLUGIN_MODULE}.py": sbfl.PLUGIN_SOURCE})
        self._test_runs += 1
        ranked = sbfl.rank(res.output, ws.path)
        if ranked:
            top = ranked[0]
            message = (f"Most suspicious: {top.path}:{top.line} in {top.function}() "
                       f"(Ochiai {top.score})")
        else:
            message = "No coverage spectrum available; continuing without SBFL"
        self.log.emit("sbfl", message, attempt=attempt,
                      data={"top": [asdict(x) for x in ranked], "duration_s": res.duration_s})
        return ranked

    # -- candidates -----------------------------------------------------------

    def _propose_all(self, messages: list[dict[str, str]], ws: Workspace, attempt: int,
                     failures: str, count: int, corrections: int,
                     sanity: bool) -> list[Proposal]:
        """`count` independent Ultra proposals, requested concurrently."""
        if count == 1:
            return [self._propose(list(messages), ws, attempt, failures, corrections, sanity,
                                  index=0, count=1)]
        with ThreadPoolExecutor(count) as pool:
            futures = [pool.submit(self._propose, list(messages), ws, attempt, failures,
                                   corrections, sanity, i, count) for i in range(count)]
            return [f.result() for f in futures]

    def _choose(self, ws: Workspace, distinct: list[Proposal], attempt: int,
                total: int) -> tuple[Proposal, PytestReport]:
        """Apply the best distinct proposal to the workspace and return it with its tests."""
        if len(distinct) == 1:
            chosen = distinct[0]
            self._apply(ws, chosen, attempt)
            chosen.report = self._run_tests(ws, attempt)
            if total > 1:
                self._emit_ranking([chosen], attempt, total)
            return chosen, chosen.report

        self._check_budget()

        def evaluate(p: Proposal) -> ExecResult:
            assert p.prepared is not None
            path = ws.variant(self._tmp / "variants", p.prepared.new_contents)
            try:
                return self.sandbox.run_pytest(path)
            finally:
                remove_tree(path)

        with ThreadPoolExecutor(len(distinct)) as pool:
            results = list(pool.map(evaluate, distinct))
        outputs = {}
        for p, res in zip(distinct, results, strict=True):
            self._test_runs += 1
            p.report = self._record_tests(res, attempt, candidate=p.index)
            outputs[p.index] = res.output
        ranked = rank(distinct)
        chosen = ranked[0]
        self._emit_ranking(ranked, attempt, total)
        self._apply(ws, chosen, attempt)
        self._last_output = outputs[chosen.index]
        assert chosen.report is not None
        return chosen, chosen.report

    def _apply(self, ws: Workspace, p: Proposal, attempt: int) -> None:
        assert p.prepared is not None
        patcher.apply(p.prepared, ws.path)
        self.log.emit(
            "patch_applied",
            f"Applied patch to {', '.join(p.prepared.files)} "
            f"({p.prepared.changed_lines} changed lines)",
            attempt=attempt,
            data={"diff": p.prepared.diff, "files": p.prepared.files,
                  "candidate": p.index, "votes": p.votes},
        )

    def _emit_ranking(self, ranked: list[Proposal], attempt: int, total: int) -> None:
        rows = []
        for p in ranked:
            assert p.report is not None and p.prepared is not None
            rows.append({"candidate": p.index, "votes": p.votes, "temperature": p.temperature,
                         "passed": p.report.passed, "failing": p.report.bad,
                         "all_passed": p.report.all_passed,
                         "changed_lines": p.prepared.changed_lines})
        best = rows[0]
        self.log.emit(
            "candidates_ranked",
            f"{total} candidates, {len(ranked)} distinct: chose #{best['candidate']} "
            f"({best['passed']} passed, {best['failing']} failing, {best['votes']} vote(s))",
            attempt=attempt,
            data={"ranking": rows, "chosen": best["candidate"], "total": total},
        )

    # -- minimization ---------------------------------------------------------

    def _minimize(self, ws: Workspace, attempt: int) -> None:
        """Drop change regions the tests do not need; keep the original fix on any doubt."""
        try:
            paths = ws.modified_files()
            before = {p: ws.baseline_text(p) for p in paths}
            after = {p: (ws.path / p).read_bytes().decode("utf-8", "replace") for p in paths}
            units = Units.of(before, after)
            if units.count < 2:
                return
            executed = 0

            def passes(contents: dict[str, str]) -> bool:
                nonlocal executed
                try:
                    self._check_budget()
                except BudgetExceeded:
                    return False  # out of budget: keep the remaining regions
                path = ws.variant(self._tmp / "variants", contents)
                try:
                    res = self.sandbox.run_pytest(path)
                finally:
                    remove_tree(path)
                executed += 1
                self._test_runs += 1
                return parse_pytest_output(res.output, res.exit_code, res.timed_out).all_passed

            removed, _ = minimize(units, passes, self.settings.minimize_max_runs)
            if removed:
                for p, text in units.build(removed).items():
                    (ws.path / p).write_bytes(text.encode("utf-8"))
                if not self._run_tests(ws, attempt).all_passed:  # confirm on the real tree
                    ws.revert()
                    removed = set()
                else:
                    ws.commit("minimized")
            message = (f"Removed {len(removed)} of {units.count} change regions the tests do "
                       f"not need ({executed} test runs)" if removed else
                       f"All {units.count} change regions are needed ({executed} test runs)")
            self.log.emit("patch_minimized", message, attempt=attempt, data={
                "regions": units.count, "removed": len(removed), "runs": executed,
                "diff": ws.diff() if removed else "",
            })
        except (SandboxError, WorkspaceError, BudgetExceeded) as exc:
            log.warning("patch minimization skipped: %s", exc)
            ws.revert()

    # -- model calls ----------------------------------------------------------

    def _call(self, role: Role, messages: list[dict[str, str]], *,
              temperature: float | None = None, json_mode: bool = False) -> LLMResult:
        res = self.llm.chat(role, messages, temperature=temperature, json_mode=json_mode)
        with self._usage_lock:
            usage = self._usage[role]
            usage.model = res.model
            usage.calls += 1
            usage.prompt_tokens += res.prompt_tokens
            usage.completion_tokens += res.completion_tokens
            usage.latency_s = round(usage.latency_s + res.latency_s, 3)
        return res

    def _check_budget(self) -> None:
        s = self.settings
        with self._usage_lock:
            total = sum(u.total_tokens for u in self._usage.values())
        if total > s.max_total_tokens:
            raise BudgetExceeded(
                f"Token budget exceeded: {total} tokens used, limit {s.max_total_tokens} "
                "(MAX_TOTAL_TOKENS). Run aborted."
            )
        elapsed = time.monotonic() - self._t0
        if elapsed > s.max_run_seconds:
            raise BudgetExceeded(
                f"Time budget exceeded: {elapsed:.0f}s elapsed, limit {s.max_run_seconds}s "
                "(MAX_RUN_SECONDS). Run aborted."
            )

    def _triage(self, attempt: int) -> str:
        raw = self._last_output
        res = self._call("nano", prompts.triage_messages(_clip(raw, self.settings.max_triage_chars)),
                         json_mode=self.settings.nano_json_mode)
        parsed = parse_json_object(res.text)
        if parsed:
            raw_lines = parsed.get("key_lines")
            key_lines = [str(x)[:300] for x in raw_lines][:8] if isinstance(raw_lines, list) else []
            summary = (
                f"error_class: {parsed.get('error_class', '?')}\n"
                f"summary: {parsed.get('summary', '')}\n"
                "key lines:\n" + "\n".join(f"  {line}" for line in key_lines)
            )
            message = str(parsed.get("summary") or parsed.get("error_class") or "Triage done")
        else:
            summary = "(triage output was not valid JSON; rely on the parsed failures)"
            message = "Triage returned no valid JSON; continuing with parsed failures"
        self.log.emit(
            "triage",
            message[:500],
            attempt=attempt,
            **_llm_fields(res),
            data={
                "triage": parsed,
                "valid_json": parsed is not None,
                "raw_output_chars": len(raw),
                "summary_chars": len(summary),
            },
        )
        self._result.triage_input_chars += len(raw)
        self._result.triage_summary_chars += len(summary)
        self._check_budget()
        return summary

    def _propose(
        self,
        messages: list[dict[str, str]],
        ws: Workspace,
        attempt: int,
        failures: str,
        corrections: int,
        sanity: bool,
        index: int,
        count: int,
    ) -> Proposal:
        """Ask Ultra for a diff; feed validation/sanity errors back up to `corrections` times."""
        temperature = self.settings.candidate_temperature(index, count)
        tag = {"correction": 0, "candidate": index}
        diagnosis = diff = error = ""
        for round_ in range(corrections + 1):
            tag["correction"] = round_
            res = self._call("ultra", messages, temperature=temperature)
            diagnosis = parse_diagnosis(res.text)
            self.log.emit(
                "diagnosis",
                diagnosis or "(no diagnosis given)",
                attempt=attempt,
                **_llm_fields(res),
                data={**tag, "finish_reason": res.finish_reason, "temperature": temperature},
            )
            self._check_budget()
            try:
                diff = patcher.extract_diff(res.text)
                self.log.emit("patch_proposed", "Ultra proposed a diff", attempt=attempt,
                              data={**tag, "diff": diff})
                prepared = patcher.prepare(res.text, ws.path, self.settings.max_diff_lines)
            except PatchError as exc:
                error = str(exc)
                self.log.emit("patch_rejected", error, attempt=attempt,
                              data={**tag, "stage": "validation"})
                messages = [*messages, {"role": "assistant", "content": res.text},
                            {"role": "user", "content": prompts.CORRECTION.format(error=error)}]
                continue
            diff = prepared.diff
            if sanity:
                verdict, reason = self._sanity(diagnosis, failures, prepared.diff, attempt, index)
                if verdict == "special_case":
                    error = f"Rejected by sanity check: {reason}"
                    self.log.emit("patch_rejected", error, attempt=attempt,
                                  data={**tag, "stage": "sanity"})
                    messages = [
                        *messages, {"role": "assistant", "content": res.text},
                        {"role": "user", "content": prompts.SANITY_CORRECTION.format(reason=reason)},
                    ]
                    continue
            return Proposal(index, temperature, prepared, diagnosis, diff, "")
        return Proposal(index, temperature, None, diagnosis, diff, error)

    def _sanity(self, diagnosis: str, failures: str, diff: str, attempt: int,
                candidate: int) -> tuple[str, str]:
        res = self._call("nano", prompts.sanity_messages(diagnosis, failures, diff),
                         json_mode=self.settings.nano_json_mode)
        parsed = parse_json_object(res.text) or {}
        verdict = parsed.get("verdict")
        if verdict not in {"ok", "special_case", "unrelated"}:
            verdict = "unparsed"  # never block a patch on an unreadable review
        reason = str(parsed.get("reason", ""))[:300]
        self.log.emit(
            "sanity_check",
            f"Sanity check: {verdict}" + (f" - {reason}" if reason else ""),
            attempt=attempt,
            **_llm_fields(res),
            data={"verdict": verdict, "reason": reason, "candidate": candidate},
        )
        self._check_budget()
        return verdict, reason

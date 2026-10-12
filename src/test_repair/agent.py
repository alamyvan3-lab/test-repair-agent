"""The repair loop: test, triage (Nano), diagnose and patch (Ultra), validate, sanity-check, retest."""

from __future__ import annotations

import json
import logging
import re
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import patcher, prompts
from .config import RoleModel, Settings
from .events import Event, EventLog, Status, jsonl_sink
from .llm import ChatModel, LLMError, LLMResult, Role
from .localize import localize
from .patcher import PatchError, PreparedPatch
from .pytest_parser import PytestReport, compact_failures, parse_pytest_output
from .report import render_report
from .sandbox import Sandbox, SandboxError
from .workspace import Workspace, WorkspaceError, remove_tree

Mode = Literal["agent", "single_shot"]
Verdict = Literal["improved", "same", "regressed"]

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
        self._t0 = 0.0
        self._test_runs = 0
        self._last_output = ""
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

        self.log.emit(
            "run_started",
            f"Repairing {repo.name} ({mode.replace('_', '-')} mode, sandbox: {self.sandbox.name})",
            data={
                "repo": repo.name,
                "mode": mode,
                "max_attempts": 1 if mode == "single_shot" else s.max_attempts,
                "models": {"ultra": s.ultra.model, "nano": s.nano.model},
                "sandbox": self.sandbox.name,
            },
        )
        tmp = Path(tempfile.mkdtemp(prefix="test-repair-"))
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
        max_attempts = 1 if single else s.max_attempts
        history: list[prompts.AttemptRecord] = []
        for attempt in range(1, max_attempts + 1):
            result.attempts = attempt
            failures = compact_failures(report)
            triage = None if single else self._triage(attempt)
            suspects = localize(report, ws.path, s.top_k_files, s.max_file_chars)
            messages = prompts.repair_messages(
                repo=ws.source.name, attempt=attempt, max_attempts=max_attempts,
                max_lines=s.max_diff_lines, failures=failures, triage=triage,
                suspects=suspects, history=history,
            )
            prepared, diagnosis, diff, error = self._propose(
                messages, ws, attempt, failures,
                corrections=0 if single else s.max_corrections,
                sanity=s.sanity_check and not single,
            )
            result.diagnosis = diagnosis or result.diagnosis
            if prepared is None:
                history.append(prompts.AttemptRecord(attempt, diagnosis, diff, "rejected", error))
                continue

            patcher.apply(prepared, ws.path)
            self.log.emit(
                "patch_applied",
                f"Applied patch to {', '.join(prepared.files)} "
                f"({prepared.changed_lines} changed lines)",
                attempt=attempt,
                data={"diff": prepared.diff, "files": prepared.files},
            )
            new = self._run_tests(ws, attempt)
            if new.all_passed:
                ws.commit(f"attempt {attempt}")
                result.final = SuiteCounts.of(new)
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
                self.log.emit("patch_reverted", f"Reverted attempt {attempt} ({outcome})",
                              attempt=attempt, data={"reason": outcome})
                history.append(prompts.AttemptRecord(
                    attempt, diagnosis, prepared.diff, f"{outcome}; patch reverted", detail))
                continue

            ws.commit(f"attempt {attempt}")
            kept = "improved" if verdict == "improved" else "failures changed"
            history.append(prompts.AttemptRecord(
                attempt, diagnosis, prepared.diff, f"{kept}; patch kept", detail))
            report = new
            result.final = SuiteCounts.of(new)

        return "EXHAUSTED", f"Tests still failing after {max_attempts} attempt(s)."

    # -- steps ----------------------------------------------------------------

    def _run_tests(self, ws: Workspace, attempt: int) -> PytestReport:
        self._check_budget()
        res = self.sandbox.run_pytest(ws.path)
        self._test_runs += 1
        self._last_output = res.output
        report = parse_pytest_output(res.output, res.exit_code, res.timed_out)
        if res.timed_out:
            message = f"Tests timed out after {self.settings.test_timeout_s}s"
        else:
            message = (f"Tests: {report.passed} passed, {report.failed} failed, "
                       f"{report.errors} errors ({res.duration_s:.2f}s)")
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
            },
        )
        return report

    def _call(self, role: Role, messages: list[dict[str, str]]) -> LLMResult:
        res = self.llm.chat(role, messages)
        usage = self._usage[role]
        usage.model = res.model
        usage.calls += 1
        usage.prompt_tokens += res.prompt_tokens
        usage.completion_tokens += res.completion_tokens
        usage.latency_s = round(usage.latency_s + res.latency_s, 3)
        return res

    def _check_budget(self) -> None:
        s = self.settings
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
        res = self._call("nano", prompts.triage_messages(_clip(raw, self.settings.max_triage_chars)))
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
    ) -> tuple[PreparedPatch | None, str, str, str]:
        """Ask Ultra for a diff; feed validation/sanity errors back up to `corrections` times."""
        diagnosis = diff = error = ""
        for round_ in range(corrections + 1):
            res = self._call("ultra", messages)
            diagnosis = parse_diagnosis(res.text)
            self.log.emit(
                "diagnosis",
                diagnosis or "(no diagnosis given)",
                attempt=attempt,
                **_llm_fields(res),
                data={"correction": round_, "finish_reason": res.finish_reason},
            )
            self._check_budget()
            try:
                diff = patcher.extract_diff(res.text)
                self.log.emit("patch_proposed", "Ultra proposed a diff", attempt=attempt,
                              data={"diff": diff, "correction": round_})
                prepared = patcher.prepare(res.text, ws.path, self.settings.max_diff_lines)
            except PatchError as exc:
                error = str(exc)
                self.log.emit("patch_rejected", error, attempt=attempt,
                              data={"stage": "validation", "correction": round_})
                messages = [*messages, {"role": "assistant", "content": res.text},
                            {"role": "user", "content": prompts.CORRECTION.format(error=error)}]
                continue
            diff = prepared.diff
            if sanity:
                verdict, reason = self._sanity(diagnosis, failures, prepared.diff, attempt)
                if verdict == "special_case":
                    error = f"Rejected by sanity check: {reason}"
                    self.log.emit("patch_rejected", error, attempt=attempt,
                                  data={"stage": "sanity", "correction": round_})
                    messages = [
                        *messages, {"role": "assistant", "content": res.text},
                        {"role": "user", "content": prompts.SANITY_CORRECTION.format(reason=reason)},
                    ]
                    continue
            return prepared, diagnosis, diff, ""
        return None, diagnosis, diff, error

    def _sanity(self, diagnosis: str, failures: str, diff: str, attempt: int) -> tuple[str, str]:
        res = self._call("nano", prompts.sanity_messages(diagnosis, failures, diff))
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
            data={"verdict": verdict, "reason": reason},
        )
        self._check_budget()
        return verdict, reason

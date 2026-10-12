"""Render report.md for a finished run."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from .config import Settings
from .events import Event

if TYPE_CHECKING:
    from .agent import RunResult


def _tests_text(d: dict) -> str:
    if d.get("timed_out"):
        return "timed out"
    return f"{d.get('passed')} passed / {d.get('failed')} failed / {d.get('errors')} errors"


def _attempt_rows(events: list[Event]) -> list[str]:
    rows: dict[int, dict[str, object]] = defaultdict(
        lambda: {"ultra": 0, "rejected": 0, "candidates": "1", "applied": "no", "tests": "-",
                 "outcome": "-"}
    )
    candidate_runs: dict[tuple[int, int], dict] = {}
    for e in events:
        if not e.attempt:
            continue
        row = rows[e.attempt]
        if e.type == "diagnosis":
            row["ultra"] = int(row["ultra"]) + 1  # type: ignore[call-overload]
        elif e.type == "patch_rejected":
            row["rejected"] = int(row["rejected"]) + 1  # type: ignore[call-overload]
        elif e.type == "candidates_ranked":
            row["candidates"] = f"{e.data['total']} ({len(e.data['ranking'])} distinct)"
        elif e.type == "patch_applied":
            row["applied"] = f"yes (#{e.data.get('candidate', 0)})"
            chosen = candidate_runs.get((e.attempt, e.data.get("candidate", 0)))
            if chosen:
                row["tests"] = _tests_text(chosen)
                row["outcome"] = "green" if chosen.get("all_passed") else "still failing"
        elif e.type == "tests_run":
            if e.data.get("candidate") is not None:
                candidate_runs[(e.attempt, e.data["candidate"])] = e.data
                continue
            row["tests"] = _tests_text(e.data)
            row["outcome"] = "green" if e.data.get("all_passed") else "still failing"
        elif e.type == "patch_reverted":
            row["outcome"] = f"reverted ({e.data.get('reason')})"
    out = [("| Attempt | Ultra calls | Candidates | Rejected diffs | Applied | Tests after | "
           "Outcome |"), "|---|---|---|---|---|---|---|"]
    for n in sorted(rows):
        r = rows[n]
        out.append(f"| {n} | {r['ultra']} | {r['candidates']} | {r['rejected']} | "
                   f"{r['applied']} | {r['tests']} | {r['outcome']} |")
    return out


def _evidence(events: list[Event]) -> list[str]:
    """One line per localization / minimization step, in run order."""
    labels = {"isolation_check": "Isolation rerun", "sbfl": "SBFL (Ochiai)",
              "patch_minimized": "Patch minimization"}
    return [f"- **{labels[e.type]}:** {e.message}" for e in events if e.type in labels]


def render_report(result: RunResult, events: list[Event], settings: Settings) -> str:
    i, f = result.initial, result.final
    lines = [
        f"# Test-Repair report: {result.repo}",
        "",
        f"- **Status:** {result.status} ({result.message})",
        f"- **Mode:** {result.mode.replace('_', '-')}",
        f"- **Attempts used:** {result.attempts}",
        f"- **Tests before:** {i.passed} passed, {i.failed} failed, {i.errors} errors"
        + (" (timed out)" if i.timed_out else ""),
        f"- **Tests after:** {f.passed} passed, {f.failed} failed, {f.errors} errors"
        + (" (timed out)" if f.timed_out else ""),
        (f"- **Wall time:** {result.wall_time_s:.1f}s across {result.test_runs} sandboxed test runs "
        f"({result.sandbox} sandbox)"),
        "",
        "## Explanation",
        "",
        result.diagnosis or "_No diagnosis was produced._",
        "",
        "## Attempts",
        "",
        *_attempt_rows(events),
        "",
        *(["## Localization and minimization", "", *_evidence(events), ""]
          if _evidence(events) else []),
        "## Model usage",
        "",
        ("| Role | Model | Calls | Prompt tokens | Completion tokens | Total latency (s) "
        "| Mean latency (s) | Cost (USD) |"),
        "|---|---|---|---|---|---|---|---|",
    ]
    for role, cfg in (("ultra", settings.ultra), ("nano", settings.nano)):
        u = result.usage.get(role)
        if u is None:
            continue
        mean = u.latency_s / u.calls if u.calls else 0.0
        cost = u.cost_usd(cfg)
        lines.append(
            f"| {role} | `{u.model}` | {u.calls} | {u.prompt_tokens} | {u.completion_tokens} | "
            f"{u.latency_s:.2f} | {mean:.2f} | {'n/a' if cost is None else f'{cost:.5f}'} |"
        )
    lines += ["", "## Patch", ""]
    lines += ["```diff", result.patch.rstrip(), "```"] if result.patch.strip() else [
        "_No changes were kept._"
    ]
    return "\n".join(lines) + "\n"

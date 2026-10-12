"""Benchmark: run the agent and a single-shot baseline over bench/repos, report measured numbers.

Runners:
- `LocalRunner` (default) runs every trial on this machine.
- `NebiusJobsRunner` submits one Nebius Serverless AI job per repo and collects results from
  job logs. It follows the documented `nebius ai job` CLI but is UNTESTED against live
  Nebius infrastructure (see deploy/README.md).
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .agent import Mode, RepairAgent, RoleUsage
from .config import ConfigError, Settings
from .events import Status
from .llm import ChatModel, TokenFactoryClient
from .sandbox import Sandbox, SandboxError, make_sandbox

MARKER = "TR_BENCH_RESULT "
README_START, README_END = "<!-- BENCH:START -->", "<!-- BENCH:END -->"
ROLES = ("ultra", "nano")


@dataclass(frozen=True)
class Task:
    name: str
    path: Path
    category: str
    description: str


class TrialResult(BaseModel):
    repo: str
    category: str
    mode: Mode
    trial: int
    status: Status
    message: str = ""
    attempts: int = 0
    wall_time_s: float = 0.0
    test_runs: int = 0
    usage: dict[str, RoleUsage] = Field(default_factory=dict)
    triage_input_chars: int = 0
    triage_summary_chars: int = 0


def load_tasks(bench_dir: Path, names: list[str] | None = None) -> list[Task]:
    """Benchmark repos from bench/repos, labelled via bench/manifest.json."""
    manifest = json.loads((bench_dir / "manifest.json").read_text(encoding="utf-8"))
    tasks = []
    for d in sorted((bench_dir / "repos").iterdir()):
        if not d.is_dir() or (names and d.name not in names):
            continue
        meta = manifest.get(d.name, {})
        tasks.append(Task(d.name, d, meta.get("category", "?"), meta.get("description", "")))
    if names:
        missing = set(names) - {t.name for t in tasks}
        if missing:
            raise ConfigError(f"unknown benchmark repos: {', '.join(sorted(missing))}")
    return tasks


class Runner(Protocol):
    def run(self, tasks: list[Task], modes: list[Mode], trials: int) -> list[TrialResult]: ...


class LocalRunner:
    """Runs trials in this process (optionally several at once)."""

    def __init__(
        self,
        settings: Settings,
        out_dir: Path,
        llm_factory: Callable[[Settings], ChatModel] = TokenFactoryClient,
        sandbox_factory: Callable[[Settings], Sandbox] = make_sandbox,
        workers: int = 1,
        emit_marker: bool = False,
    ) -> None:
        self.settings = settings
        self.out_dir = out_dir
        self.llm_factory = llm_factory
        self.sandbox_factory = sandbox_factory
        self.workers = max(1, workers)
        self.emit_marker = emit_marker

    def _one(self, task: Task, mode: Mode, trial: int) -> TrialResult:
        agent = RepairAgent(self.settings, self.llm_factory(self.settings),
                            self.sandbox_factory(self.settings))
        res = agent.run(task.path, self.out_dir / "runs" / f"{task.name}-{mode}-t{trial}", mode)
        trial_result = TrialResult(
            repo=task.name, category=task.category, mode=mode, trial=trial,
            status=res.status, message=res.message, attempts=res.attempts,
            wall_time_s=res.wall_time_s, test_runs=res.test_runs, usage=res.usage,
            triage_input_chars=res.triage_input_chars,
            triage_summary_chars=res.triage_summary_chars,
        )
        print(f"{task.name:<28} {mode:<12} t{trial} {res.status:<18} attempts={res.attempts} "
              f"wall={res.wall_time_s:.1f}s", flush=True)
        if self.emit_marker:
            print(MARKER + trial_result.model_dump_json(), flush=True)
        return trial_result

    def run(self, tasks: list[Task], modes: list[Mode], trials: int) -> list[TrialResult]:
        jobs = [(t, m, n) for n in range(trials) for t in tasks for m in modes]
        if self.workers == 1:
            return [self._one(*job) for job in jobs]
        with ThreadPoolExecutor(self.workers) as pool:
            return list(pool.map(lambda job: self._one(*job), jobs))


class NebiusJobsRunner:
    """One Nebius Serverless AI job per repo; results are parsed from job logs.

    UNTESTED against live Nebius infrastructure. Built from the documented CLI:
    `nebius ai job create` (--image, --platform, --preset, --container-command, --args,
    --env, --env-secret, --timeout, --async, --format json), `nebius ai job get --id`,
    and `nebius ai job logs <id>`. Inside the job there is no Docker daemon, so the job
    sets SANDBOX_BACKEND=local (subprocess runner; see docs/ARCHITECTURE.md).
    """

    TERMINAL = frozenset({"COMPLETED", "SUCCEEDED", "FAILED", "ERROR", "CANCELLED", "CANCELED",
                "TERMINATED"})

    def __init__(
        self,
        settings: Settings,
        image: str,
        api_key_secret: str,
        platform_name: str = "cpu-d3",
        preset: str = "4vcpu-16gb",
        poll_s: float = 20.0,
        timeout_s: float = 3600.0,
        nebius: str = "nebius",
        execute: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not image:
            raise ConfigError("NebiusJobsRunner needs --image (a pushed copy of ./Dockerfile)")
        if not api_key_secret:
            raise ConfigError(
                "NebiusJobsRunner needs --api-key-secret: a MysteryBox selector holding "
                "TOKEN_FACTORY_API_KEY. Literal keys are never passed to jobs."
            )
        self.settings = settings
        self.image = image
        self.api_key_secret = api_key_secret
        self.platform = platform_name
        self.preset = preset
        self.poll_s = poll_s
        self.timeout_s = timeout_s
        self.nebius = nebius
        self.execute = execute or (
            lambda cmd: subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=300)
        )
        self.sleep = sleep

    def _env_flags(self) -> list[str]:
        s = self.settings
        values = [
            ("SANDBOX_BACKEND", "local"),
            ("MODEL_ULTRA", s.ultra.model),
            ("MODEL_NANO", s.nano.model),
            ("ULTRA_BASE_URL", s.ultra.base_url),
            ("NANO_BASE_URL", s.nano.base_url),
            ("MAX_ATTEMPTS", str(s.max_attempts)),
            ("MAX_TOTAL_TOKENS", str(s.max_total_tokens)),
            ("ULTRA_EXTRA_BODY", json.dumps(s.ultra.extra_body) if s.ultra.extra_body else ""),
            ("NANO_EXTRA_BODY", json.dumps(s.nano.extra_body) if s.nano.extra_body else ""),
        ]
        flags: list[str] = []
        for key, value in values:
            if value:
                flags += ["--env", f"{key}={value}"]
        return flags + ["--env-secret", f"TOKEN_FACTORY_API_KEY={self.api_key_secret}"]

    def job_command(self, task: Task, modes: list[Mode], trials: int, name: str) -> list[str]:
        args = (f"bench --runner local --repos {task.name} --modes {','.join(modes)} "
                f"--trials {trials} --emit-marker --out /tmp/bench")
        return [
            self.nebius, "ai", "job", "create",
            "--name", name,
            "--image", self.image,
            "--platform", self.platform,
            "--preset", self.preset,
            "--timeout", "1h",
            "--container-command", "test-repair",
            "--args", args,
            *self._env_flags(),
            "--async",
            "--format", "json",
        ]

    def _job_id(self, create_stdout: str, name: str) -> str:
        try:
            op = json.loads(create_stdout)
            job_id = op.get("resource_id") or op.get("metadata", {}).get("resource_id")
            if job_id:
                return str(job_id)
        except json.JSONDecodeError:
            pass
        proc = self.execute([self.nebius, "ai", "job", "get-by-name", "--name", name,
                             "--format", "json"])
        if proc.returncode != 0:
            raise RuntimeError(f"cannot find job {name}: {proc.stderr.strip()[:300]}")
        return str(json.loads(proc.stdout)["metadata"]["id"])

    def _state(self, job_id: str) -> str:
        proc = self.execute([self.nebius, "ai", "job", "get", "--id", job_id, "--format", "json"])
        if proc.returncode != 0:
            return "UNKNOWN"
        return str(json.loads(proc.stdout).get("status", {}).get("state", "UNKNOWN")).upper()

    @staticmethod
    def parse_logs(text: str) -> list[TrialResult]:
        out = []
        for line in text.splitlines():
            idx = line.find(MARKER)
            if idx != -1:
                out.append(TrialResult.model_validate_json(line[idx + len(MARKER):]))
        return out

    def run(self, tasks: list[Task], modes: list[Mode], trials: int) -> list[TrialResult]:
        stamp = time.strftime("%Y%m%d%H%M%S")
        jobs: dict[str, Task] = {}
        failures: list[TrialResult] = []
        for task in tasks:
            name = re.sub(r"[^a-z0-9-]", "-", f"tr-bench-{stamp}-{task.name}".lower())[:63]
            cmd = self.job_command(task, modes, trials, name)
            print("+ " + shlex.join(cmd), flush=True)
            proc = self.execute(cmd)
            if proc.returncode != 0:
                failures += self._failed(task, modes, trials, f"job create failed: "
                                                              f"{proc.stderr.strip()[:300]}")
                continue
            jobs[self._job_id(proc.stdout, name)] = task

        deadline = time.monotonic() + self.timeout_s
        pending = dict(jobs)
        results: list[TrialResult] = []
        while pending and time.monotonic() < deadline:
            for job_id, task in list(pending.items()):
                state = self._state(job_id)
                if state not in self.TERMINAL:
                    continue
                del pending[job_id]
                logs = self.execute([self.nebius, "ai", "job", "logs", job_id])
                parsed = self.parse_logs(logs.stdout)
                print(f"job {job_id} ({task.name}) {state}: {len(parsed)} results")
                results += parsed or self._failed(task, modes, trials,
                                                  f"job {job_id} ended {state} with no results")
            if pending:
                self.sleep(self.poll_s)
        for job_id, task in pending.items():
            failures += self._failed(task, modes, trials, f"job {job_id} timed out")
        return results + failures

    @staticmethod
    def _failed(task: Task, modes: list[Mode], trials: int, message: str) -> list[TrialResult]:
        return [TrialResult(repo=task.name, category=task.category, mode=m, trial=n,
                            status="ERROR", message=message)
                for n in range(trials) for m in modes]


# -- aggregation and reporting -------------------------------------------------


def aggregate(results: list[TrialResult], settings: Settings) -> dict[str, Any]:
    """Per-mode totals. Pass rate counts SUCCESS only."""
    prices = {"ultra": settings.ultra, "nano": settings.nano}
    summary: dict[str, Any] = {}
    for mode in sorted({r.mode for r in results}):
        rows = [r for r in results if r.mode == mode]
        wins = [r for r in rows if r.status == "SUCCESS"]
        roles: dict[str, Any] = {}
        total_cost: float | None = 0.0
        for role in ROLES:
            usage = [r.usage[role] for r in rows if role in r.usage]
            calls = sum(u.calls for u in usage)
            latency = sum(u.latency_s for u in usage)
            merged = RoleUsage(model=next((u.model for u in usage if u.model), ""), calls=calls,
                               prompt_tokens=sum(u.prompt_tokens for u in usage),
                               completion_tokens=sum(u.completion_tokens for u in usage),
                               latency_s=round(latency, 3))
            cost = merged.cost_usd(prices[role])
            total_cost = None if cost is None or total_cost is None else total_cost + cost
            roles[role] = {
                **merged.model_dump(),
                "mean_latency_per_call_s": round(latency / calls, 3) if calls else None,
                "cost_usd": None if cost is None else round(cost, 6),
            }
        statuses: dict[str, int] = {}
        for r in rows:
            statuses[r.status] = statuses.get(r.status, 0) + 1
        summary[mode] = {
            "runs": len(rows),
            "successes": len(wins),
            "pass_rate": round(len(wins) / len(rows), 4) if rows else 0.0,
            "mean_attempts_to_success": round(sum(r.attempts for r in wins) / len(wins), 2)
            if wins else None,
            "statuses": statuses,
            "roles": roles,
            "total_tokens": sum(v["prompt_tokens"] + v["completion_tokens"] for v in roles.values()),
            "total_cost_usd": None if total_cost is None else round(total_cost, 6),
            "total_wall_time_s": round(sum(r.wall_time_s for r in rows), 2),
            "triage_input_chars": sum(r.triage_input_chars for r in rows),
            "triage_summary_chars": sum(r.triage_summary_chars for r in rows),
        }
    return summary


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def summary_table(agg: dict[str, Any]) -> str:
    lines = [
        ("| Mode | Runs | Pass rate | Mean attempts to success | Ultra tokens (in / out) "
        "| Nano tokens (in / out) | Ultra mean latency/call | Nano mean latency/call "
        "| Total cost (USD) | Total wall time |"),
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for mode, m in agg.items():
        u, n = m["roles"]["ultra"], m["roles"]["nano"]
        lines.append(
            f"| {mode.replace('_', '-')} | {m['runs']} | {m['successes']}/{m['runs']} "
            f"({m['pass_rate'] * 100:.1f}%) | {_fmt(m['mean_attempts_to_success'])} | "
            f"{u['prompt_tokens']} / {u['completion_tokens']} | "
            f"{n['prompt_tokens']} / {n['completion_tokens']} | "
            f"{_fmt(u['mean_latency_per_call_s'])}s | {_fmt(n['mean_latency_per_call_s'])}s | "
            f"{_fmt(m['total_cost_usd'], 4)} | {m['total_wall_time_s']:.1f}s |"
        )
    for mode, m in agg.items():
        raw, short = m["triage_input_chars"], m["triage_summary_chars"]
        if raw:
            lines.append(
                f"\nNano triage ({mode.replace('_', '-')}): {raw:,} chars of raw pytest output "
                f"were condensed to {short:,} chars of summary for Ultra "
                f"({(1 - short / raw) * 100:.0f}% smaller)."
            )
    return "\n".join(lines)


def render_markdown(meta: dict[str, Any], agg: dict[str, Any], results: list[TrialResult]) -> str:
    lines = [
        "# Benchmark results",
        "",
        (f"Generated by `bench/run_bench.py` at {meta['finished_at']} (runner: {meta['runner']}, "
        f"sandbox: {meta['sandbox']}, trials per repo: {meta['trials']}, seed: {meta['seed']}, "
        f"commit: {meta['commit']})."),
        f"Models: Ultra role `{meta['models']['ultra']}`, Nano role `{meta['models']['nano']}`.",
        "All numbers below are measured by this run; none are edited by hand.",
        "",
        "## Summary",
        "",
        summary_table(agg),
        "",
        ("Pass rate counts runs that ended with every test green. The agent mode is the full loop "
        "(Nano triage, Ultra diagnosis and patch, validation with up to "
        f"{meta['max_corrections']} corrections, Nano sanity check, up to {meta['max_attempts']} "
        "attempts). Single-shot is one Ultra call and one test run, with no loop, triage, or "
        "corrections."),
        "",
        "## Per repo",
        "",
        ("| Repo | Bug category | Mode | Trial | Outcome | Attempts | Ultra tokens | Nano tokens "
        "| Ultra latency | Nano latency | Wall time |"),
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda r: (r.repo, r.mode, r.trial)):
        u = r.usage.get("ultra", RoleUsage())
        n = r.usage.get("nano", RoleUsage())
        lines.append(
            f"| {r.repo} | {r.category} | {r.mode.replace('_', '-')} | {r.trial} | {r.status} | "
            f"{r.attempts} | {u.total_tokens} | {n.total_tokens} | {u.latency_s:.1f}s | "
            f"{n.latency_s:.1f}s | {r.wall_time_s:.1f}s |"
        )
    misses = [r for r in results if r.status != "SUCCESS"]
    lines += ["", "## Runs that did not succeed", ""]
    lines += [f"- `{r.repo}` ({r.mode.replace('_', '-')}, trial {r.trial}): {r.status}: "
              f"{r.message}" for r in sorted(misses, key=lambda r: (r.repo, r.mode, r.trial))]
    if not misses:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def update_readme(readme: Path, meta: dict[str, Any], agg: dict[str, Any]) -> None:
    """Replace the block between the BENCH markers with the measured summary table."""
    text = readme.read_text(encoding="utf-8")
    if README_START not in text or README_END not in text:
        raise ConfigError(f"{readme} has no {README_START} / {README_END} markers")
    block = (f"{README_START}\n_Measured {meta['finished_at']} with `{meta['models']['ultra']}` "
             f"(Ultra role) and `{meta['models']['nano']}` (Nano role), {meta['sandbox']} "
             f"sandbox, {meta['trials']} trial(s) per repo. Full table: "
             f"[bench/results/results.md](bench/results/results.md)._\n\n"
             f"{summary_table(agg)}\n{README_END}")
    head, rest = text.split(README_START, 1)
    readme.write_text(head + block + rest.split(README_END, 1)[1], encoding="utf-8", newline="\n")


def _commit() -> str:
    try:
        proc = subprocess.run(["git", "rev-parse", "--short", "HEAD"], check=False, capture_output=True,
                              text=True, timeout=10)
        return proc.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="test-repair bench", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bench-dir", default="bench")
    parser.add_argument("--repos", help="comma-separated subset of bench/repos")
    parser.add_argument("--modes", default="agent,single_shot",
                        help="comma-separated: agent, single_shot")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0, help="shuffles repo order")
    parser.add_argument("--runner", choices=["local", "nebius-jobs"], default="local")
    parser.add_argument("--workers", type=int, default=1, help="parallel trials (local runner)")
    parser.add_argument("--out", default="bench/results")
    parser.add_argument("--readme", help="also refresh the BENCH block in this README")
    parser.add_argument("--emit-marker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--image", default="", help="nebius-jobs: pushed app image")
    parser.add_argument("--api-key-secret", default="",
                        help="nebius-jobs: MysteryBox selector for TOKEN_FACTORY_API_KEY")
    parser.add_argument("--platform", default="cpu-d3")
    parser.add_argument("--preset", default="4vcpu-16gb")
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
        if args.runner == "local":
            settings.require_llm()
        elif not (settings.ultra.model and settings.nano.model):
            raise ConfigError("set MODEL_ULTRA and MODEL_NANO (they are passed to the jobs)")
        modes: list[Mode] = []
        for m in args.modes.split(","):
            if m not in ("agent", "single_shot"):
                raise ConfigError(f"unknown mode {m!r}")
            modes.append(m)  # type: ignore[arg-type]
        tasks = load_tasks(Path(args.bench_dir),
                           args.repos.split(",") if args.repos else None)
        random.Random(args.seed).shuffle(tasks)
        out = Path(args.out)
        runner: Runner
        if args.runner == "local":
            runner = LocalRunner(settings, out, workers=args.workers,
                                 emit_marker=args.emit_marker)
            sandbox = settings.sandbox_backend
        else:
            runner = NebiusJobsRunner(settings, args.image, args.api_key_secret,
                                      platform_name=args.platform, preset=args.preset)
            sandbox = "local (inside Nebius job)"
        started = datetime.now(UTC).isoformat(timespec="seconds")
        results = runner.run(tasks, modes, args.trials)
    except (ConfigError, SandboxError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    meta = {
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "runner": args.runner,
        "sandbox": sandbox,
        "trials": args.trials,
        "seed": args.seed,
        "commit": _commit(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "models": {"ultra": settings.ultra.model, "nano": settings.nano.model},
        "max_attempts": settings.max_attempts,
        "max_corrections": settings.max_corrections,
        "repos": [t.name for t in tasks],
    }
    agg = aggregate(results, settings)
    if not args.emit_marker:  # job mode prints markers only; the submitting runner aggregates
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(json.dumps(
            {"meta": meta, "aggregate": agg, "trials": [r.model_dump() for r in results]},
            indent=2), encoding="utf-8", newline="\n")
        (out / "results.md").write_text(render_markdown(meta, agg, results), encoding="utf-8", newline="\n")
        if args.readme:
            update_readme(Path(args.readme), meta, agg)
        print(f"\n{summary_table(agg)}\n\nWrote {out / 'results.json'} and {out / 'results.md'}")
    return 0

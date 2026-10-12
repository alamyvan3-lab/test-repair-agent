"""Command line: `test-repair run PATH`, `test-repair bench`, `test-repair serve`."""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
import time
from pathlib import Path

from .agent import RepairAgent
from .config import ConfigError, Settings
from .events import Event
from .llm import TokenFactoryClient
from .sandbox import SandboxError, make_sandbox

_LABELS = {
    "run_started": "start",
    "tests_run": "tests",
    "triage": "triage",
    "diagnosis": "diagnose",
    "patch_proposed": "patch",
    "patch_rejected": "rejected",
    "sanity_check": "sanity",
    "patch_applied": "applied",
    "regression": "REGRESS",
    "patch_reverted": "revert",
    "success": "SUCCESS",
    "failure": "STOPPED",
}


def format_event(event: Event, t0: float) -> str:
    """One readable progress line (ASCII only, safe on any console)."""
    label = _LABELS[event.type]
    if event.type == "tests_run":
        label = "tests OK" if event.data.get("all_passed") else "tests FAIL"
    model = ""
    if event.role:
        model = (f" [{event.role} {event.model} | {event.prompt_tokens}->"
                 f"{event.completion_tokens} tok | {event.latency_s:.1f}s]")
    attempt = f"#{event.attempt} " if event.attempt else ""
    line = f"[{event.ts - t0:6.1f}s] {attempt}{label:<10}{model} {event.message}"
    if event.type == "patch_applied":
        diff = event.data.get("diff", "")
        line += "\n" + "\n".join("    " + d for d in diff.rstrip().splitlines())
    return line


def _printer(as_json: bool):  # type: ignore[no-untyped-def]
    t0 = time.time()

    def emit(event: Event) -> None:
        text = event.model_dump_json() if as_json else format_event(event, t0)
        print(text, flush=True)

    return emit


def cmd_run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    overrides = {}
    if args.max_attempts is not None:
        overrides["max_attempts"] = args.max_attempts
    if args.sandbox:
        overrides["sandbox_backend"] = args.sandbox
    settings = dataclasses.replace(settings, **overrides)
    settings.require_llm()
    repo = Path(args.path).resolve()
    if not repo.is_dir():
        raise ConfigError(f"{repo} is not a directory")
    out = Path(args.out) if args.out else Path("runs") / f"{repo.name}-{time.strftime('%Y%m%d-%H%M%S')}"
    agent = RepairAgent(settings, TokenFactoryClient(settings), make_sandbox(settings),
                        on_event=_printer(args.json))
    result = agent.run(repo, out, mode="single_shot" if args.single_shot else "agent")
    if not args.json:
        print(f"\n{result.status}: {result.message}")
        for role, u in result.usage.items():
            print(f"  {role:<5} {u.model}: {u.calls} calls, {u.prompt_tokens} prompt + "
                  f"{u.completion_tokens} completion tokens, {u.latency_s:.1f}s")
        print(f"Artifacts: {out / 'repair.patch'}, {out / 'report.md'}, {out / 'events.jsonl'}")
    return 0 if result.status == "SUCCESS" else 1


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("test_repair.server:create_app", factory=True, host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["bench"]:  # forward everything to the benchmark's own parser
        from .benchmark import main as bench_main

        return bench_main(argv[1:])

    parser = argparse.ArgumentParser(prog="test-repair", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="repair the failing tests of a repository")
    run.add_argument("path", help="repository directory (it is copied, never modified)")
    run.add_argument("--max-attempts", type=int, default=None)
    run.add_argument("--out", help="artifact directory (default: runs/<repo>-<time>)")
    run.add_argument("--sandbox", choices=["docker", "local", "auto"],
                     help="override SANDBOX_BACKEND")
    run.add_argument("--single-shot", action="store_true",
                     help="baseline: one Ultra call, no loop, no triage")
    run.add_argument("--json", action="store_true", help="print raw JSON events")
    sub.add_parser("bench", help="run the benchmark (see `test-repair bench --help`)")
    serve = sub.add_parser("serve", help="start the web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        return cmd_run(args) if args.command == "run" else cmd_serve(args)
    except (ConfigError, SandboxError) as exc:
        print(json.dumps({"error": str(exc)}) if getattr(args, "json", False) else f"error: {exc}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

"""Command line: `test-repair run PATH`, `bench`, `serve`, `doctor`, `publish-recording`."""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
from pathlib import Path

from . import recordings
from .agent import RepairAgent
from .config import ConfigError, Settings
from .events import Event
from .llm import TokenFactoryClient, list_models
from .sandbox import DockerSandbox, SandboxError, make_sandbox

_LABELS = {
    "run_started": "start",
    "tests_run": "tests",
    "isolation_check": "isolate",
    "sbfl": "sbfl",
    "triage": "triage",
    "diagnosis": "diagnose",
    "patch_proposed": "patch",
    "patch_rejected": "rejected",
    "sanity_check": "sanity",
    "candidates_ranked": "rank",
    "patch_applied": "applied",
    "regression": "REGRESS",
    "patch_reverted": "revert",
    "patch_minimized": "minimize",
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


def _printer(as_json: bool, transcript: list[str]):  # type: ignore[no-untyped-def]
    t0 = time.time()

    def emit(event: Event) -> None:
        line = format_event(event, t0)
        transcript.append(line)
        print(event.model_dump_json() if as_json else line, flush=True)

    return emit


def _save_markdown(path: Path, argv: list[str], transcript: list[str], report: str) -> None:
    """Write a shareable log of a run: command, streamed progress, and the final report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    log = "\n".join(transcript)
    path.write_text(
        "# Example run\n\n"
        f"Recorded {time.strftime('%Y-%m-%d %H:%M %Z')} with real Token Factory calls.\n\n"
        f"```\n$ test-repair {' '.join(argv)}\n{log}\n```\n\n"
        + report.replace("# Test-Repair report", "## Report", 1),
        encoding="utf-8",
        newline="\n",
    )


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
    transcript: list[str] = []
    agent = RepairAgent(settings, TokenFactoryClient(settings), make_sandbox(settings),
                        on_event=_printer(args.json, transcript))
    result = agent.run(repo, out, mode="single_shot" if args.single_shot else "agent")
    if args.save_md:
        _save_markdown(Path(args.save_md), args.argv, transcript,
                       (out / "report.md").read_text(encoding="utf-8"))
    if not args.json:
        print(f"\n{result.status}: {result.message}")
        for role, u in result.usage.items():
            print(f"  {role:<5} {u.model}: {u.calls} calls, {u.prompt_tokens} prompt + "
                  f"{u.completion_tokens} completion tokens, {u.latency_s:.1f}s")
        print(f"Artifacts: {out / 'repair.patch'}, {out / 'report.md'}, {out / 'events.jsonl'}")
    return 0 if result.status == "SUCCESS" else 1


def doctor_checks(settings: Settings, env_file: Path = Path(".env")) -> list[tuple[str, bool, str]]:
    """Environment checks as (name, ok, detail). Never prints the key."""
    checks = [
        ("Python >= 3.11", sys.version_info >= (3, 11), platform.python_version()),
        ("git on PATH", bool(shutil.which("git")), shutil.which("git") or "not found"),
        (".env file", env_file.is_file(),
         "found" if env_file.is_file() else "missing: copy .env.example to .env"),
        ("TOKEN_FACTORY_API_KEY", bool(settings.api_key),
         "set (value hidden)" if settings.api_key else "missing"),
        ("MODEL_ULTRA", bool(settings.ultra.model), settings.ultra.model or "missing"),
        ("MODEL_NANO", bool(settings.nano.model), settings.nano.model or "missing"),
    ]
    if settings.sandbox_backend == "local":
        checks.append(("sandbox", True, "local subprocess runner (weaker isolation)"))
    else:
        docker_ok = DockerSandbox.available()
        checks.append(("Docker daemon", docker_ok, "running" if docker_ok else
                       "not reachable: start Docker or set SANDBOX_BACKEND=local"))
        if docker_ok:
            image_ok = subprocess.run(
                ["docker", "image", "inspect", settings.sandbox_image],
                check=False, capture_output=True, timeout=30).returncode == 0
            checks.append(("sandbox image", image_ok, settings.sandbox_image if image_ok else
                           f"missing: docker build -t {settings.sandbox_image} sandbox/"))
    if settings.api_key:
        for role, cfg in (("ultra", settings.ultra), ("nano", settings.nano)):
            try:
                ids = {m.get("id") for m in list_models(cfg.base_url, settings.api_key)}
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                checks.append((f"{role} model listed", False, f"{cfg.base_url}: {exc}"))
                continue
            listed = cfg.model in ids
            checks.append((f"{role} model listed", listed, f"{cfg.model} at {cfg.base_url}" if listed
                           else f"{cfg.model or '(unset)'} not in /models at {cfg.base_url}; "
                                "run scripts/hello_tokenfactory.py"))
    return checks


def cmd_doctor(_args: argparse.Namespace) -> int:
    checks = doctor_checks(Settings.from_env())
    for name, ok, detail in checks:
        print(f"[{' OK ' if ok else 'FAIL'}] {name}: {detail}")
    return 0 if all(ok for _, ok, _ in checks) else 1


def cmd_publish_recording(args: argparse.Namespace) -> int:
    entry = recordings.publish(Path(args.run_dir), Path(args.dest), args.title)
    print(f"Published {entry['file']} ({entry['status']}) to {args.dest}; commit it to update "
          "the replay page.")
    return 0


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
    run.add_argument("--save-md", help="also write the progress log + report to this .md file")
    sub.add_parser("bench", help="run the benchmark (see `test-repair bench --help`)")
    serve = sub.add_parser("serve", help="start the web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("doctor", help="check Python, git, Docker, sandbox image, key, and models")
    rec = sub.add_parser("publish-recording",
                         help="add a real run's events.jsonl to web/recordings for replay")
    rec.add_argument("run_dir", help="artifact directory of a finished `test-repair run`")
    rec.add_argument("--title", help="label shown in the replay picker")
    rec.add_argument("--dest", default="web/recordings")
    args = parser.parse_args(argv)
    args.argv = argv

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    commands = {"run": cmd_run, "serve": cmd_serve, "doctor": cmd_doctor,
                "publish-recording": cmd_publish_recording}
    try:
        return commands[args.command](args)
    except (ConfigError, SandboxError) as exc:
        print(json.dumps({"error": str(exc)}) if getattr(args, "json", False) else f"error: {exc}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

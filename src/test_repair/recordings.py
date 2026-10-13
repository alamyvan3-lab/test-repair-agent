"""Publish the event log of a real run so the web UI can replay it (e.g. on GitHub Pages)."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from .config import ConfigError

# Model IDs used only by tests and UI previews; a recording made with them is not real.
_NOT_REAL = ("fake/", "preview/")


def _events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ConfigError(f"{path} not found; pass a run directory written by `test-repair run`")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def publish(run_dir: Path, dest: Path, title: str | None = None) -> dict[str, Any]:
    """Copy `run_dir/events.jsonl` into `dest` and list it in `dest/index.json`."""
    events = _events(run_dir / "events.jsonl")
    start = next((e for e in events if e["type"] == "run_started"), None)
    end = next((e for e in reversed(events) if e["type"] in ("success", "failure")), None)
    if start is None or end is None:
        raise ConfigError("the run is incomplete (no run_started or final event)")
    called = {e["model"] for e in events if e.get("model")}
    if not called or any(m.startswith(_NOT_REAL) for m in called):
        raise ConfigError("refusing to publish: this run made no real Token Factory model calls")

    repo = start["data"]["repo"]
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(start["ts"]))
    name = f"{repo}-{stamp}.jsonl"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(run_dir / "events.jsonl", dest / name)

    result = end.get("data", {})
    entry = {
        "file": name,
        "repo": repo,
        "title": title or f"{repo} ({start['data'].get('mode', 'agent')} mode)",
        "status": result.get("status", end["message"].split(":", 1)[0]),
        "attempts": result.get("attempts"),
        "wall_time_s": result.get("wall_time_s"),
        "recorded_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(start["ts"])),
        "models": start["data"].get("models", {}),
        "sandbox": start["data"].get("sandbox", ""),
    }
    index_path = dest / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {}
    index.setdefault("live_url", "")
    index["recordings"] = [r for r in index.get("recordings", []) if r["file"] != name] + [entry]
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8", newline="\n")
    return entry

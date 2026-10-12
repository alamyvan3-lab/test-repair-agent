"""FastAPI app: start runs on bundled demo repos (or uploaded zips) and stream events over SSE.

Start with `test-repair serve` or `uvicorn test_repair.server:create_app --factory`.
The Token Factory key is only read server-side; nothing secret is sent to the browser.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
import zipfile
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)

from .agent import RepairAgent, RunResult
from .config import ConfigError, ServerSettings, Settings
from .events import TERMINAL_EVENTS, Event
from .llm import ChatModel, TokenFactoryClient
from .sandbox import Sandbox, make_sandbox
from .workspace import remove_tree

log = logging.getLogger("test_repair.server")

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_RUN_ID_RE = re.compile(r"^[a-f0-9]{12}$")
_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")


@dataclass
class RunState:
    id: str
    repo: str
    created: float
    out_dir: Path
    events: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    result: RunResult | None = None


class RateLimiter:
    """At most `limit` run starts per client IP per sliding `window_s`."""

    def __init__(self, limit: int, window_s: int) -> None:
        self.limit, self.window_s = limit, window_s
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


class UploadError(ValueError):
    """Uploaded zip rejected."""


def extract_zip(data: bytes, dest: Path, max_files: int, max_bytes: int) -> Path:
    """Safely extract an uploaded repo zip; returns the repository root inside `dest`."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UploadError("not a valid zip file") from exc
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > max_files:
        raise UploadError(f"zip has {len(infos)} files; the limit is {max_files}")
    if sum(i.file_size for i in infos) > max_bytes:
        raise UploadError(f"zip expands beyond {max_bytes} bytes")
    total = 0
    for info in infos:
        name = info.filename.replace("\\", "/")
        parts = PurePosixPath(name).parts
        if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in parts:
            raise UploadError(f"unsafe path in zip: {info.filename!r}")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise UploadError(f"symlinks are not allowed: {info.filename!r}")
        if parts and parts[0] in {".git", "__MACOSX"}:
            continue
        with zf.open(info) as src:
            content = src.read(info.file_size + 1)
        if len(content) != info.file_size:
            raise UploadError(f"size mismatch for {info.filename!r}")
        total += len(content)
        if total > max_bytes:
            raise UploadError(f"zip expands beyond {max_bytes} bytes")
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    tops = [p for p in dest.iterdir()]
    root = tops[0] if len(tops) == 1 and tops[0].is_dir() else dest
    if not any(root.rglob("*.py")):
        raise UploadError("zip contains no Python files")
    return root


class RunManager:
    """Runs agents in background threads with a global concurrency cap."""

    def __init__(
        self,
        settings: Settings,
        server: ServerSettings,
        llm_factory: Callable[[Settings], ChatModel],
        sandbox_factory: Callable[[Settings], Sandbox],
    ) -> None:
        self.settings = settings
        self.server = server
        self.llm_factory = llm_factory
        self.sandbox_factory = sandbox_factory
        self.runs: dict[str, RunState] = {}
        self._slots = threading.BoundedSemaphore(server.max_concurrent_runs)
        self._lock = threading.Lock()

    def start(self, repo_dir: Path, label: str, cleanup: Path | None = None) -> RunState:
        if not self._slots.acquire(blocking=False):
            raise HTTPException(429, "The server is busy with other runs; try again shortly.")
        run_id = uuid.uuid4().hex[:12]
        state = RunState(id=run_id, repo=label, created=time.time(),
                         out_dir=Path(self.server.runs_dir) / run_id)
        with self._lock:
            self.runs[run_id] = state
            self._prune()
        threading.Thread(target=self._work, args=(state, repo_dir, cleanup), daemon=True).start()
        return state

    def _work(self, state: RunState, repo_dir: Path, cleanup: Path | None) -> None:
        try:
            agent = RepairAgent(
                self.settings,
                self.llm_factory(self.settings),
                self.sandbox_factory(self.settings),
                on_event=lambda e: state.events.append(e.model_dump()),
            )
            state.result = agent.run(repo_dir, state.out_dir)
        except Exception as exc:  # e.g. sandbox unavailable; surface as a terminal event
            log.exception("run %s failed to start", state.id)
            state.events.append(Event(seq=len(state.events) + 1, ts=time.time(), type="failure",
                                      message=f"ERROR: {exc}",
                                      data={"status": "ERROR"}).model_dump())
        finally:
            state.done = True
            self._slots.release()
            if cleanup is not None:
                remove_tree(cleanup)

    def _prune(self) -> None:
        finished = sorted((r for r in self.runs.values() if r.done), key=lambda r: r.created)
        for old in finished[: max(0, len(self.runs) - self.server.max_kept_runs)]:
            del self.runs[old.id]
            remove_tree(old.out_dir)


def _demo_repos(demo_dirs: list[Path]) -> dict[str, Path]:
    repos: dict[str, Path] = {}
    for base in demo_dirs:
        if base.is_dir():
            for d in sorted(base.iterdir()):
                if d.is_dir() and _NAME_RE.match(d.name) and (d / "tests").is_dir():
                    repos.setdefault(d.name, d)
    return repos


def _project_path(rel: str) -> Path:
    """Resolve a project-relative path from the CWD, else from a source checkout."""
    path = Path(rel)
    if path.is_absolute() or path.exists():
        return path
    checkout = Path(__file__).resolve().parents[2] / rel
    return checkout if checkout.exists() else path


def _description(repo: Path) -> str:
    readme = repo / "README.md"
    if readme.is_file():
        for line in readme.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                return line.strip()[:200]
    return ""


def create_app(
    settings: Settings | None = None,
    server: ServerSettings | None = None,
    llm_factory: Callable[[Settings], ChatModel] | None = None,
    sandbox_factory: Callable[[Settings], Sandbox] | None = None,
    demo_dirs: list[Path] | None = None,
    web_dir: Path | None = None,
) -> FastAPI:
    """Build the app. Factories are injectable so tests can run without network or Docker."""
    settings = settings or Settings.from_env()
    server = server or ServerSettings.from_env()
    if demo_dirs is None:
        demo_dirs = [_project_path(p.strip())
                     for p in os.environ.get("DEMO_DIRS", "examples,bench/repos").split(",")]
    web_dir = web_dir or _project_path(os.environ.get("WEB_DIR", "web"))
    demos = _demo_repos(demo_dirs)
    manager = RunManager(settings, server, llm_factory or TokenFactoryClient,
                         sandbox_factory or make_sandbox)
    limiter = RateLimiter(server.rate_limit_runs, server.rate_limit_window_s)
    uploads_ok = server.allow_uploads and settings.sandbox_backend == "docker"

    app = FastAPI(title="Test-Repair Agent", docs_url=None, redoc_url=None)

    def client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        if server.trust_proxy_headers and forwarded:
            # The last entry is the one our proxy appended; earlier ones are client-supplied.
            return forwarded.split(",")[-1].strip()
        return request.client.host if request.client else "unknown"

    def get_run(run_id: str) -> RunState:
        state = manager.runs.get(run_id) if _RUN_ID_RE.match(run_id) else None
        if state is None:
            raise HTTPException(404, "run not found")
        return state

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(web_dir / "index.html", headers={"Content-Security-Policy": _CSP})

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "sandbox": settings.sandbox_backend,
            "models": {"ultra": settings.ultra.model, "nano": settings.nano.model},
            "llm_configured": bool(settings.api_key and settings.ultra.model and settings.nano.model),
            "uploads": uploads_ok,
            "max_attempts": settings.max_attempts,
        }

    @app.get("/api/demos")
    def list_demos() -> list[dict[str, str]]:
        return [{"name": n, "description": _description(p)} for n, p in demos.items()]

    @app.post("/api/runs", status_code=201)
    async def start_run(request: Request) -> JSONResponse:
        if not limiter.allow(client_ip(request)):
            raise HTTPException(429, "Rate limit reached for your address; try again later.")
        try:
            settings.require_llm()
        except ConfigError as exc:
            raise HTTPException(503, f"Server is not configured for model calls: {exc}") from exc
        ctype = request.headers.get("content-type", "").split(";")[0].strip()
        cleanup: Path | None = None
        if ctype == "application/json":
            try:
                body = await request.json()
            except json.JSONDecodeError as exc:
                raise HTTPException(400, "invalid JSON body") from exc
            name = str(body.get("demo", "")) if isinstance(body, dict) else ""
            if name not in demos:
                raise HTTPException(404, f"unknown demo repo {name!r}")
            repo_dir, label = demos[name], name
        elif ctype in {"application/zip", "application/x-zip-compressed", "application/octet-stream"}:
            if not uploads_ok:
                raise HTTPException(403, "Uploads are disabled (ALLOW_UPLOADS=false or no Docker "
                                         "sandbox). Pick a bundled demo repo.")
            data = bytearray()
            async for chunk in request.stream():
                data += chunk
                if len(data) > server.max_upload_bytes:
                    raise HTTPException(413, f"upload exceeds {server.max_upload_bytes} bytes")
            cleanup = Path(tempfile.mkdtemp(prefix="tr-upload-"))
            try:
                repo_dir = extract_zip(bytes(data), cleanup, server.max_upload_files,
                                       server.max_uncompressed_bytes)
            except UploadError as exc:
                remove_tree(cleanup)
                raise HTTPException(400, f"upload rejected: {exc}") from exc
            label = "upload"
        else:
            raise HTTPException(415, "send JSON {\"demo\": name} or a zip (application/zip)")
        try:
            state = manager.start(repo_dir, label, cleanup)
        except HTTPException:
            if cleanup is not None:
                remove_tree(cleanup)
            raise
        return JSONResponse(
            {"id": state.id, "repo": label, "events": f"/api/runs/{state.id}/events",
             "patch": f"/api/runs/{state.id}/patch"},
            status_code=201,
        )

    @app.get("/api/runs/{run_id}")
    def run_status(run_id: str) -> dict[str, Any]:
        state = get_run(run_id)
        return {"id": state.id, "repo": state.repo, "done": state.done,
                "status": state.result.status if state.result else None,
                "events": len(state.events)}

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request) -> StreamingResponse:
        state = get_run(run_id)

        async def stream():  # type: ignore[no-untyped-def]
            sent, last_ping = 0, time.monotonic()
            while True:
                while sent < len(state.events):
                    event = state.events[sent]
                    sent += 1
                    yield f"id: {event['seq']}\nevent: {event['type']}\ndata: {json.dumps(event)}\n\n"
                    if event["type"] in TERMINAL_EVENTS:
                        yield "event: end\ndata: {}\n\n"
                        return
                if state.done and sent >= len(state.events):
                    yield "event: end\ndata: {}\n\n"
                    return
                if await request.is_disconnected():
                    return
                if time.monotonic() - last_ping > 15:
                    last_ping = time.monotonic()
                    yield ": ping\n\n"
                await asyncio.sleep(0.2)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/runs/{run_id}/patch")
    def run_patch(run_id: str) -> PlainTextResponse:
        state = get_run(run_id)
        if not state.done or state.result is None:
            raise HTTPException(409, "run has not finished")
        return PlainTextResponse(
            state.result.patch, media_type="text/x-diff",
            headers={"Content-Disposition": f'attachment; filename="repair-{state.id}.patch"'},
        )

    @app.get("/api/runs/{run_id}/report")
    def run_report(run_id: str) -> PlainTextResponse:
        state = get_run(run_id)
        report = state.out_dir / "report.md"
        if not state.done or not report.is_file():
            raise HTTPException(409, "run has not finished")
        return PlainTextResponse(report.read_text(encoding="utf-8"), media_type="text/markdown")

    return app

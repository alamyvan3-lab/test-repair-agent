"""Web API: starts a real uvicorn server, runs a demo repo, reads the SSE stream to completion."""

import dataclasses
import io
import json
import socket
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn
from helpers import EXAMPLES, FAKE_KEY, ROOT, FakeLLM, reference_fix, ultra_answer

from test_repair.config import ServerSettings, Settings
from test_repair.sandbox import LocalSandbox
from test_repair.server import UploadError, create_app, extract_zip


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def serve(app) -> Iterator[str]:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def request(url: str, body: bytes | None = None, ctype: str = "application/json"):
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": ctype} if body is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode("utf-8")


def read_sse(url: str) -> list[dict]:
    events, current = [], None
    with urllib.request.urlopen(url, timeout=120) as resp:
        for raw in resp:
            line = raw.decode("utf-8").rstrip("\r\n")
            if line.startswith("event: "):
                current = line[len("event: "):]
                if current == "end":
                    break
            elif line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


def app_for(settings: Settings, tmp_path: Path, llm: FakeLLM | None = None, **server_kw):
    server = ServerSettings(runs_dir=str(tmp_path / "runs"), **server_kw)
    return create_app(
        settings, server,
        llm_factory=lambda _s: llm or FakeLLM([ultra_answer(reference_fix("demo_invoice"))]),
        sandbox_factory=lambda _s: LocalSandbox(timeout_s=60),
        demo_dirs=[EXAMPLES], web_dir=ROOT / "web",
    )


def test_demo_run_streams_to_completion(settings: Settings, tmp_path: Path):
    with serve(app_for(settings, tmp_path)) as base:
        status, body = request(f"{base}/api/health")
        health = json.loads(body)
        assert status == 200 and health["llm_configured"] and health["sandbox"] == "local"
        assert FAKE_KEY not in body

        status, body = request(f"{base}/api/demos")
        assert {"demo_invoice", "injection_demo"} <= {d["name"] for d in json.loads(body)}

        status, body = request(f"{base}/api/runs", json.dumps({"demo": "demo_invoice"}).encode())
        assert status == 201, body
        run = json.loads(body)

        events = read_sse(base + run["events"])
        types = [e["type"] for e in events]
        assert types[0] == "run_started" and types[-1] == "success"
        assert {"tests_run", "triage", "diagnosis", "patch_applied"} <= set(types)
        assert all(e["role"] in {"ultra", "nano"} for e in events
                   if e["type"] in {"triage", "diagnosis", "sanity_check"})

        status, patch = request(f"{base}{run['patch']}")
        assert status == 200 and "min(value, amount)" in patch
        status, report = request(f"{base}/api/runs/{run['id']}/report")
        assert status == 200 and "SUCCESS" in report

        status, page = request(f"{base}/")
        assert status == 200 and "<title>" in page and FAKE_KEY not in page


def test_bad_requests(settings: Settings, tmp_path: Path):
    with serve(app_for(settings, tmp_path)) as base:
        assert request(f"{base}/api/runs", json.dumps({"demo": "../etc"}).encode())[0] == 404
        assert request(f"{base}/api/runs", b"PK\x03\x04", "application/zip")[0] == 403
        assert request(f"{base}/api/runs", b"x", "text/plain")[0] == 415
        assert request(f"{base}/api/runs/zzz/events")[0] == 404
        assert request(f"{base}/api/runs/0123456789ab/patch")[0] == 404


def test_uploads_need_docker_even_when_enabled(settings: Settings, tmp_path: Path):
    with serve(app_for(settings, tmp_path, allow_uploads=True)) as base:
        health = json.loads(request(f"{base}/api/health")[1])
        assert health["uploads"] is False  # local sandbox: uploads stay off
        assert request(f"{base}/api/runs", b"PK\x03\x04", "application/zip")[0] == 403


def test_rate_limit_per_ip(settings: Settings, tmp_path: Path):
    unconfigured = dataclasses.replace(settings, api_key="")
    with serve(app_for(unconfigured, tmp_path, rate_limit_runs=2)) as base:
        body = json.dumps({"demo": "demo_invoice"}).encode()
        codes = [request(f"{base}/api/runs", body)[0] for _ in range(3)]
        assert codes == [503, 503, 429]


def make_zip(entries: dict[str, bytes], symlink: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = 0o120777 << 16
            zf.writestr(info, "/etc/passwd")
    return buf.getvalue()


def test_extract_zip_accepts_a_repo(tmp_path: Path):
    data = make_zip({"proj/pkg/a.py": b"x = 1\n", "proj/tests/test_a.py": b"def test(): pass\n"})
    root = extract_zip(data, tmp_path, max_files=10, max_bytes=10_000)
    assert root.name == "proj" and (root / "pkg" / "a.py").is_file()


@pytest.mark.parametrize(
    ("entries", "symlink", "match"),
    [
        ({"../evil.py": b"x"}, None, "unsafe path"),
        ({"/abs.py": b"x"}, None, "unsafe path"),
        ({"a.py": b"x"}, "link.py", "symlinks"),
        ({f"f{i}.py": b"x" for i in range(11)}, None, "limit is 10"),
        ({"big.py": b"x" * 20_000}, None, "expands beyond"),
        ({"notes.txt": b"x"}, None, "no Python files"),
    ],
)
def test_extract_zip_rejects_unsafe_archives(tmp_path: Path, entries, symlink, match):
    with pytest.raises(UploadError, match=match):
        extract_zip(make_zip(entries, symlink), tmp_path, max_files=10, max_bytes=10_000)
    assert not (tmp_path.parent / "evil.py").exists()

"""Run pytest on a working copy in isolation.

`DockerSandbox` is the default: a fresh container per run with no network, a read-only
root filesystem, resource limits, all capabilities dropped, a non-root user, and a hard
wall-clock timeout enforced from the host. The repository is streamed in as a tar on
stdin, so no host path is bind-mounted (this also works when the agent itself runs in a
container that talks to the host Docker socket).

`LocalSandbox` is the fallback for hosts without Docker (e.g. serverless containers): a
subprocess with a scrubbed environment, a timeout, POSIX resource limits, and network
isolation only when `unshare -rn` is permitted. It is weaker; see docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import io
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

from .config import Settings

PYTEST_ARGS = ["-q", "--tb=short", "-p", "no:cacheprovider", "-rfE", "--color=no"]
_COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache")
# Sets POSIX limits, then execs the real command (avoids preexec_fn in a threaded server).
_RLIMIT_WRAPPER = (
    "import os, resource, sys\n"
    "mem, cpu = int(sys.argv[1]), int(sys.argv[2])\n"
    "resource.setrlimit(resource.RLIMIT_AS, (mem, mem))\n"
    "resource.setrlimit(resource.RLIMIT_FSIZE, (64 << 20, 64 << 20))\n"
    "resource.setrlimit(resource.RLIMIT_CORE, (0, 0))\n"
    "resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))\n"
    "os.execvp(sys.argv[3], sys.argv[3:])\n"
)


class SandboxError(RuntimeError):
    """The sandbox itself failed (not the tests)."""


@dataclass(frozen=True)
class ExecResult:
    """Outcome of one sandboxed test run."""

    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool
    truncated: bool

    @property
    def output(self) -> str:
        return self.stdout + ("\n" + self.stderr if self.stderr.strip() else "")


class Sandbox(Protocol):
    name: str

    def run_pytest(self, workdir: Path, args: list[str] | None = None,
                   extra_files: dict[str, str] | None = None) -> ExecResult:
        """Run pytest (default args: PYTEST_ARGS) on a copy of `workdir` plus `extra_files`."""
        ...


class _CappedReader:
    """Drains a pipe in a thread, keeping only the first and last `cap / 2` bytes."""

    def __init__(self, stream: IO[bytes], cap: int) -> None:
        self._stream = stream
        self._half = max(cap // 2, 1)
        self.head = bytearray()
        self.tail = bytearray()
        self.dropped = 0
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        for chunk in iter(lambda: self._stream.read(8192), b""):
            room = self._half - len(self.head)
            if room > 0:
                self.head += chunk[:room]
                chunk = chunk[room:]
            self.tail += chunk
            if len(self.tail) > self._half:
                self.dropped += len(self.tail) - self._half
                del self.tail[: len(self.tail) - self._half]

    def text(self) -> str:
        self._thread.join(timeout=10)
        middle = f"\n... [{self.dropped} bytes truncated] ...\n" if self.dropped else ""
        return (bytes(self.head).decode("utf-8", "replace") + middle
                + bytes(self.tail).decode("utf-8", "replace"))


def _execute(
    cmd: list[str],
    *,
    timeout_s: float,
    max_output_bytes: int,
    on_timeout: Callable[[subprocess.Popen[bytes]], None],
    stdin_bytes: bytes | None = None,
    **popen_kwargs: object,
) -> ExecResult:
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **popen_kwargs,  # type: ignore[arg-type]
        )
    except OSError as exc:
        raise SandboxError(f"could not start {cmd[0]!r}: {exc}") from exc
    out = _CappedReader(proc.stdout, max_output_bytes)  # type: ignore[arg-type]
    err = _CappedReader(proc.stderr, max_output_bytes // 4)  # type: ignore[arg-type]
    if stdin_bytes is not None:
        def feed() -> None:
            try:
                proc.stdin.write(stdin_bytes)  # type: ignore[union-attr]
                proc.stdin.close()  # type: ignore[union-attr]
            except OSError:
                pass  # process exited early; its output explains why
        threading.Thread(target=feed, daemon=True).start()
    timed_out = False
    try:
        exit_code = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        on_timeout(proc)
        try:
            exit_code = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            exit_code = proc.wait()
    stdout, stderr = out.text(), err.text()
    return ExecResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_s=round(time.monotonic() - start, 3),
        timed_out=timed_out,
        truncated=bool(out.dropped or err.dropped),
    )


def _tar_bytes(workdir: Path, extra_files: dict[str, str] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path in sorted(workdir.rglob("*")):
            rel = path.relative_to(workdir)
            if rel.parts[0] == ".git" or "__pycache__" in rel.parts or path.is_symlink():
                continue
            info = tar.gettarinfo(str(path), arcname=rel.as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if path.is_dir() else 0o644
            if path.is_file():
                with path.open("rb") as fh:
                    tar.addfile(info, fh)
            else:
                tar.addfile(info)
        for name, text in (extra_files or {}).items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class DockerSandbox:
    """One locked-down container per test run."""

    name = "docker"

    def __init__(
        self,
        image: str,
        timeout_s: float = 60,
        memory: str = "512m",
        cpus: str = "1",
        pids_limit: int = 128,
        max_output_bytes: int = 64_000,
        tmpfs_size: str = "128m",
        docker: str = "docker",
    ) -> None:
        self.image = image
        self.timeout_s = timeout_s
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.max_output_bytes = max_output_bytes
        self.tmpfs_size = tmpfs_size
        self.docker = docker

    def command(self, container_name: str, inner: list[str] | None = None) -> list[str]:
        """The full `docker run` argv; `inner` overrides the in-container command."""
        script = 'mkdir -p /tmp/w && tar -x -C /tmp/w && cd /tmp/w && exec "$@"'
        return [
            self.docker, "run", "--rm", "-i",
            "--name", container_name,
            "--network", "none",
            "--read-only",
            "--tmpfs", f"/tmp:rw,nosuid,nodev,size={self.tmpfs_size}",
            "--memory", self.memory,
            "--memory-swap", self.memory,
            "--cpus", self.cpus,
            "--pids-limit", str(self.pids_limit),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", "10001:10001",
            "--env", "HOME=/tmp",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--env", "PYTHONUTF8=1",
            "--env", "COLUMNS=200",
            "--workdir", "/tmp",
            self.image,
            "sh", "-c", script, "sh",
            *(inner or ["python", "-m", "pytest", *PYTEST_ARGS]),
        ]

    def _kill(self, container_name: str) -> None:
        subprocess.run([self.docker, "kill", container_name], check=False, capture_output=True, timeout=30)

    def run(self, workdir: Path, inner: list[str] | None = None,
            extra_files: dict[str, str] | None = None) -> ExecResult:
        """Run `inner` (default: pytest) inside a fresh container with `workdir` copied in."""
        name = f"tr-sbx-{uuid.uuid4().hex[:12]}"
        try:
            result = _execute(
                self.command(name, inner),
                timeout_s=self.timeout_s,
                max_output_bytes=self.max_output_bytes,
                on_timeout=lambda _proc: self._kill(name),
                stdin_bytes=_tar_bytes(workdir, extra_files),
            )
        finally:
            subprocess.run([self.docker, "rm", "-f", name], check=False, capture_output=True, timeout=30)
        if result.exit_code == 125 or "Unable to find image" in result.stderr:
            raise SandboxError(
                f"docker could not start the sandbox (exit {result.exit_code}): "
                f"{result.stderr.strip()[:500]}. Build the image with "
                f"`docker build -t {self.image} sandbox/`."
            )
        return result

    def run_pytest(self, workdir: Path, args: list[str] | None = None,
                   extra_files: dict[str, str] | None = None) -> ExecResult:
        return self.run(workdir, ["python", "-m", "pytest", *(args or PYTEST_ARGS)], extra_files)

    @staticmethod
    def available(docker: str = "docker") -> bool:
        try:
            proc = subprocess.run(
                [docker, "version", "--format", "{{.Server.Version}}"],
                check=False, capture_output=True, timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return proc.returncode == 0


def _can_unshare_net() -> bool:
    if sys.platform != "linux" or not shutil.which("unshare"):
        return False
    try:
        return subprocess.run(
            ["unshare", "-rn", "true"], check=False, capture_output=True, timeout=10
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class LocalSandbox:
    """Subprocess fallback. Weaker isolation than Docker; see module docstring."""

    name = "local"

    def __init__(
        self,
        timeout_s: float = 60,
        max_output_bytes: int = 64_000,
        memory_bytes: int = 1024 * 1024 * 1024,
        python: str = sys.executable,
    ) -> None:
        self.timeout_s = timeout_s
        self.max_output_bytes = max_output_bytes
        self.memory_bytes = memory_bytes
        self.python = python
        self.network_isolated = _can_unshare_net()

    def _env(self, home: Path) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(home),
            "TMPDIR": str(home),
            "TEMP": str(home),
            "TMP": str(home),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",  # pytest output is decoded as UTF-8 (Windows defaults to cp1252)
            "PYTHONHASHSEED": "0",
            "COLUMNS": "200",
        }
        if os.name == "nt":  # Python on Windows needs SYSTEMROOT for basic OS services
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
        return env

    @staticmethod
    def _kill(proc: subprocess.Popen[bytes]) -> None:
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                return
            except ProcessLookupError:
                return
        proc.kill()

    def run(self, workdir: Path, inner: list[str] | None = None,
            extra_files: dict[str, str] | None = None) -> ExecResult:
        """Run `inner` (default: pytest) on a throwaway copy of `workdir`."""
        with tempfile.TemporaryDirectory(prefix="tr-local-") as tmp:
            home = Path(tmp)
            work = home / "w"
            shutil.copytree(workdir, work, ignore=_COPY_IGNORE, symlinks=False)
            for name, text in (extra_files or {}).items():
                (work / name).write_text(text, encoding="utf-8", newline="\n")
            cmd = inner or [self.python, "-m", "pytest", *PYTEST_ARGS]
            extra: dict[str, object] = {}
            if os.name == "posix":
                limits = [str(self.memory_bytes), str(int(self.timeout_s) + 5)]
                cmd = [self.python, "-c", _RLIMIT_WRAPPER, *limits, *cmd]
                extra = {"start_new_session": True}
            if self.network_isolated:
                cmd = ["unshare", "-rn", *cmd]
            return _execute(
                cmd,
                timeout_s=self.timeout_s,
                max_output_bytes=self.max_output_bytes,
                on_timeout=self._kill,
                cwd=work,
                env=self._env(home),
                **extra,
            )

    def run_pytest(self, workdir: Path, args: list[str] | None = None,
                   extra_files: dict[str, str] | None = None) -> ExecResult:
        return self.run(workdir, [self.python, "-m", "pytest", *(args or PYTEST_ARGS)], extra_files)


def make_sandbox(settings: Settings) -> Sandbox:
    """Pick the sandbox from SANDBOX_BACKEND (docker, local, or auto)."""
    backend = settings.sandbox_backend
    if backend == "auto":
        backend = "docker" if DockerSandbox.available() else "local"
    if backend == "local":
        return LocalSandbox(settings.test_timeout_s, settings.max_output_bytes)
    if not DockerSandbox.available():
        raise SandboxError(
            "Docker is not available. Start Docker, or set SANDBOX_BACKEND=local to use the "
            "weaker subprocess runner (see README: Guardrails)."
        )
    return DockerSandbox(
        image=settings.sandbox_image,
        timeout_s=settings.test_timeout_s,
        memory=settings.sandbox_memory,
        cpus=settings.sandbox_cpus,
        pids_limit=settings.sandbox_pids,
        max_output_bytes=settings.max_output_bytes,
    )

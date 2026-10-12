"""Throwaway git-tracked copy of the target repo. The user's directory is never modified."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", ".pytest_cache", ".venv", "venv", ".tox",
    ".mypy_cache", ".ruff_cache", "node_modules", "*.egg-info",
)
_GIT = [
    "git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false",
    "-c", "user.name=test-repair", "-c", "user.email=test-repair@localhost",
    "-c", "commit.gpgsign=false",
]


class WorkspaceError(RuntimeError):
    """Git or filesystem failure while managing the working copy."""


def remove_tree(path: Path) -> None:
    """rmtree that also handles read-only git objects on Windows."""

    def force(func, p, *_):  # type: ignore[no-untyped-def]
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=force)
    else:
        shutil.rmtree(path, onerror=force)


class Workspace:
    """Copy of `source` under `root/repo`, with a baseline commit to diff and revert against."""

    def __init__(self, source: Path, root: Path) -> None:
        self.source = source
        self.path = root / "repo"
        self.baseline = ""

    def _git(self, *args: str) -> str:
        proc = subprocess.run(
            [*_GIT, *args], cwd=self.path, check=False, capture_output=True, text=True, timeout=120
        )
        if proc.returncode != 0:
            raise WorkspaceError(f"git {args[0]} failed: {proc.stderr.strip()[:500]}")
        return proc.stdout

    def init(self) -> None:
        if not self.source.is_dir():
            raise WorkspaceError(f"{self.source} is not a directory")
        shutil.copytree(self.source, self.path, ignore=_IGNORE, symlinks=False)
        self._git("init", "-q")
        self._git("add", "-A")
        self._git("commit", "-q", "--allow-empty", "-m", "baseline")
        self.baseline = self._git("rev-parse", "HEAD").strip()

    def commit(self, message: str) -> None:
        self._git("add", "-A")
        self._git("commit", "-q", "--allow-empty", "-m", message)

    def revert(self) -> None:
        """Drop uncommitted changes (used when a patch regresses)."""
        self._git("reset", "-q", "--hard", "HEAD")
        self._git("clean", "-q", "-fd")

    def diff(self) -> str:
        """Cumulative diff of the working tree against the original copy."""
        self._git("add", "-A")
        return self._git("diff", "--cached", "--no-color", self.baseline)
